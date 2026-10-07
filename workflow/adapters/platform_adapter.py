"""
adapters/platform_adapter.py
Platform Adapter for Member 2 (Tele-Radiology Platform / Upload Portal) Integration.

Provides:
- Ingestion abstraction for cases uploaded through Member 2's clinic portal.
- Retrieval helpers for case metadata, platform images, and clinic statuses.
- Ingestion verification and schema translation between Member 2 and Member 3.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from config import DATABASE_PATH, TABLE_NAMES
from database import get_db_connection, query_all, query_one
import workflow_engine

logger = logging.getLogger("platform_adapter")


def ingest_platform_case(payload: Dict[str, Any], db_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """
    Ingests a case received from Member 2's upload portal.
    Translates payload keys if necessary and triggers the automated workflow intake:
    Upload -> Store -> CBIR -> Dynamic Priority -> Routing.

    Expected contract:
    {
        "case_id": "CASE-1001",           # Optional, generated if omitted
        "clinic_id": "CLN-001",           # Required
        "patient_ref": "PT-0421",         # Optional, generated if omitted
        "modality": "CT",                 # "X-ray" | "CT" | "MRI"
        "body_part": "Head",              # e.g. "Head", "Chest", "Knee"
        "urgency_flag": 1,                # 0 or 1
        "image_path": "static/...",       # Local path or static URI
        "upload_time": "2026-10-01T...",  # Optional, default UTC now
        "ai_triage_score": 0.85           # Optional
    }
    """
    # Validation of required fields
    if not payload:
        raise ValueError("Empty case payload received.")

    clinic_id = payload.get("clinic_id")
    if not clinic_id:
        raise ValueError("Field 'clinic_id' is required for platform ingestion.")

    modality = payload.get("modality", "X-ray")
    if modality not in ["X-ray", "CT", "MRI"]:
        raise ValueError(f"Invalid modality '{modality}'. Allowed: X-ray, CT, MRI.")

    body_part = payload.get("body_part")
    if not body_part or not str(body_part).strip():
        raise ValueError("Field 'body_part' cannot be empty.")

    # Call workflow engine to create, score, and route the case
    return workflow_engine.create_case(payload, db_path=db_path)


def get_platform_case_details(case_id: str, db_path: Optional[Union[str, Path]] = None) -> Optional[Dict[str, Any]]:
    """
    Reads case information from the local platform database.
    Returns case metadata, assignment status, and report (if completed).
    """
    conn = get_db_connection(db_path)
    try:
        sql = """
            SELECT
                c.case_id, c.clinic_id, cl.name as clinic_name,
                c.patient_ref, c.modality, c.body_part, c.urgency_flag,
                c.image_path, c.upload_time, c.status, c.priority_label,
                c.priority_score, c.assigned_rad_id,
                r.name as radiologist_name, r.subspecialty as radiologist_subspecialty,
                rep.report_text, rep.report_submit_time, rep.used_similar_cases
            FROM cases c
            LEFT JOIN clinics cl ON c.clinic_id = cl.clinic_id
            LEFT JOIN radiologists r ON c.assigned_rad_id = r.rad_id
            LEFT JOIN reports rep ON c.case_id = rep.case_id
            WHERE c.case_id = ?;
        """
        row = conn.execute(sql, (case_id,)).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def get_clinic_cases_summary(clinic_id: str, db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """
    Retrieves all cases submitted by a specific clinic for the clinic status portal.
    """
    conn = get_db_connection(db_path)
    try:
        sql = """
            SELECT
                c.case_id, c.patient_ref, c.modality, c.body_part,
                c.urgency_flag, c.upload_time, c.status, c.priority_label,
                c.priority_score, c.assigned_rad_id, r.name as radiologist_name,
                rep.report_submit_time,
                CASE WHEN rep.report_id IS NOT NULL THEN 1 ELSE 0 END as has_report
            FROM cases c
            LEFT JOIN radiologists r ON c.assigned_rad_id = r.rad_id
            LEFT JOIN reports rep ON c.case_id = rep.case_id
            WHERE c.clinic_id = ?
            ORDER BY c.upload_time DESC;
        """
        rows = conn.execute(sql, (clinic_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
