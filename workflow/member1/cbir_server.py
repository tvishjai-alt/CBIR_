"""
member1/cbir_server.py
Thin HTTP wrapper around Member 1's TeleRadiologyCBIR (cbir_service.py, left UNMODIFIED).

Gives Member 3's workflow app the contract it expects:
    GET http://127.0.0.1:8000/retrieve/<case_id>?k=10
    GET http://127.0.0.1:8000/health

How it finds the query image and patient context:
    The workflow app's webhook saves them in the table `case_context`
    (case_id, image_path, patient_age, patient_gender, view_position, source_image_index)
    inside telemed_workflow.db. This server only READS that table.

Run (from the workflow folder, with the member1 venv active):
    python member1\\cbir_server.py
"""

import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request

HERE = Path(__file__).resolve().parent          # .../workflow/member1
ROOT = HERE.parent                              # .../workflow
sys.path.insert(0, str(HERE))

from cbir_service import TeleRadiologyCBIR      # Member 1's file, unmodified

# ---------------------------------------------------------------- settings
DB_PATH = Path(os.getenv("WORKFLOW_DB", ROOT / "telemed_workflow.db"))
INDEX_PATH = HERE / "faiss_database.index"
CSV_PATH = HERE / "database_pool_active.csv"
ALPHA = float(os.getenv("CBIR_ALPHA", "0.85"))   # weight of visual vs context score (Member 1's default)
PORT = int(os.getenv("CBIR_PORT", "8000"))
SELF_MATCH_SCORE = 0.9995                        # visual score this high = the query image itself
CANDIDATES = 20                                  # size of Member 1's candidate pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("cbir_server")

# ---------------------------------------------------------------- load engine once
log.info("Loading CBIR engine (DenseNet-121 + FAISS). First run may download weights...")
engine = TeleRadiologyCBIR(index_path=str(INDEX_PATH), db_csv=str(CSV_PATH))
if engine.index.ntotal != len(engine.db_df):
    raise SystemExit(
        f"Index/CSV mismatch: index has {engine.index.ntotal} vectors but CSV has "
        f"{len(engine.db_df)} rows. Ask Member 1 for matching files."
    )
PATIENT_OF = dict(zip(engine.db_df["Image Index"], engine.db_df["Patient ID"]))
log.info("Engine ready: %d pool images.", engine.index.ntotal)

engine_lock = threading.Lock()   # one query at a time keeps torch/faiss simple and safe
app = Flask(__name__)


# ---------------------------------------------------------------- helpers
def read_context(case_id: str):
    """Reads the case_context row saved by the workflow webhook. None if missing."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM case_context WHERE case_id = ?;", (case_id,)).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:   # table not created yet
        return None
    finally:
        conn.close()


def resolve_image(case_id: str, image_path):
    """Finds the query image on disk (relative paths are relative to the workflow folder)."""
    candidates = []
    if image_path:
        p = Path(image_path)
        candidates.append(p if p.is_absolute() else ROOT / p)
    for ext in (".png", ".jpg", ".jpeg"):
        candidates.append(ROOT / "static" / "images" / f"{case_id}{ext}")
    for c in candidates:
        if c.is_file():
            return c
    return None


def clean_gender(g):
    return "F" if str(g or "").strip().upper().startswith("F") else "M"


def clean_view(v):
    return "AP" if str(v or "").strip().upper() == "AP" else "PA"


# ---------------------------------------------------------------- routes
@app.get("/health")
def health():
    return jsonify({"status": "ok", "pool_images": int(engine.index.ntotal), "alpha": ALPHA})


@app.get("/retrieve/<case_id>")
def retrieve(case_id: str):
    k = max(1, min(request.args.get("k", 10, type=int), CANDIDATES))

    ctx = read_context(case_id)
    if not ctx:
        return jsonify({"error": f"No context saved for case '{case_id}'. "
                                 "Create it through the workflow webhook first."}), 404

    img = resolve_image(case_id, ctx.get("image_path"))
    if img is None:
        return jsonify({"error": f"Image file for '{case_id}' not found on disk."}), 404

    try:
        age = max(0, min(int(ctx.get("patient_age") or 50), 120))
    except (TypeError, ValueError):
        age = 50
    gender = clean_gender(ctx.get("patient_gender"))
    view = clean_view(ctx.get("view_position"))

    t0 = time.perf_counter()
    try:
        with engine_lock:
            raw = engine.retrieve_similar_cases(
                str(img), age, gender, view, alpha=ALPHA, top_k=CANDIDATES
            )
    except Exception as exc:   # bad image, etc.
        log.error("Retrieval failed for %s: %s", case_id, exc, exc_info=True)
        return jsonify({"error": f"Retrieval failed: {exc}"}), 500
    retrieval_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    # Drop the query itself and any other image of the same patient (trivial matches).
    src = ctx.get("source_image_index")
    own_patient = PATIENT_OF.get(src) if src else None
    results = []
    for r in raw:
        idx = r["image_index"]
        if src and idx == src:
            continue
        if own_patient is not None and PATIENT_OF.get(idx) == own_patient:
            continue
        if r["visual_score"] >= SELF_MATCH_SCORE:
            continue
        stem = Path(idx).stem
        results.append({
            "similar_case_id": stem,
            "score": r["visual_score"],
            "reranked_score": r["final_relevance_score"],
            "thumbnail_path": f"static/images/{stem}.png",
            "finding": (
                f"{str(r['pathologies']).replace('|', ', ')}"
                f" · {r['patient_age']}y {r['patient_gender']} · {r['view_position']}"
            ),
        })

    results = results[:k]
    for rank, item in enumerate(results, start=1):   # already ordered by final relevance score
        item["rank"] = rank

    return jsonify({"case_id": case_id, "retrieval_ms": retrieval_ms, "results": results})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)