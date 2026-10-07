"""
cbir_context.py  (put in the workflow/ folder, next to app.py)

Remembers the extra fields Member 1's CBIR needs (patient age, gender, view position,
original image name) in a small side table, so we do not have to touch the existing
`cases` table, schema, seed script or workflow engine.

Used by app.py:  save_context(data)  right before  ingest_platform_case(data)
Read by:         member1/cbir_server.py
"""

import sqlite3
from typing import Any, Dict

from config import DATABASE_PATH

DDL = """
CREATE TABLE IF NOT EXISTS case_context (
    case_id            TEXT PRIMARY KEY,
    image_path         TEXT,
    patient_age        INTEGER,
    patient_gender     TEXT,
    view_position      TEXT,
    source_image_index TEXT
);
"""

DEFAULT_AGE = 50
DEFAULT_GENDER = "M"
DEFAULT_VIEW = "PA"


def save_context(payload: Dict[str, Any]) -> None:
    """Stores CBIR context for a case. Does nothing if the payload has no case_id."""
    case_id = payload.get("case_id")
    if not case_id:
        return

    try:
        age = max(0, min(int(payload.get("patient_age", DEFAULT_AGE)), 120))
    except (TypeError, ValueError):
        age = DEFAULT_AGE
    gender = "F" if str(payload.get("patient_gender", DEFAULT_GENDER)).strip().upper().startswith("F") else "M"
    view = "AP" if str(payload.get("view_position", DEFAULT_VIEW)).strip().upper() == "AP" else "PA"

    conn = sqlite3.connect(str(DATABASE_PATH), timeout=10)
    try:
        conn.execute(DDL)
        conn.execute(
            "INSERT OR REPLACE INTO case_context VALUES (?, ?, ?, ?, ?, ?);",
            (case_id, payload.get("image_path"), age, gender, view, payload.get("source_image_index")),
        )
        conn.commit()
    finally:
        conn.close()