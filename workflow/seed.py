"""
seed.py
Idempotent Database Seeding Script for Tele-Radiology Workflow Management.

Creates:
- SLA configuration records
- 3 Clinic records
- 6 Radiologists across subspecialties
- 100 Historical completed cases over the last 14 days with complete event trails
- 15 Active open cases in various stages (Uploaded, Retrieved, Assigned, In_Review)
- Local mock PACS images in static/images/ if none exist

Usage:
    python seed.py [--reset]
"""

import argparse
import json
import logging
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from PIL import Image, ImageDraw

from config import (
    BASE_DIR,
    DATABASE_PATH,
    IMAGES_DIR,
    PRIORITY_WEIGHTS,
    PRIORITY_THRESHOLDS,
    SLA_LIMITS_MINUTES,
)
from database import init_db, get_db_connection, execute_write, query_one

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("seed")

random.seed(42)

CLINICS_DATA = [
    ("CLN-001", "Metro Urgent Care Center", "Downtown Metro, BLDG 4"),
    ("CLN-002", "Lakeside Community Hospital", "North District, Lakeview"),
    ("CLN-003", "Valley Health Outpatient Center", "East Valley Med Hub"),
]

RADIOLOGISTS_DATA = [
    ("RAD-001", "Dr. Sarah Chen", "Neuro", 6, 1, 15.0),
    ("RAD-002", "Dr. Marcus Vance", "Chest", 7, 1, 12.0),
    ("RAD-003", "Dr. Elena Rostova", "MSK", 5, 1, 18.0),
    ("RAD-004", "Dr. David Kim", "General", 8, 1, 14.0),
    ("RAD-005", "Dr. Priya Patel", "Chest", 6, 1, 11.0),
    ("RAD-006", "Dr. James Wilson", "Neuro", 5, 1, 16.0),
]

MODALITIES_BODY_PARTS = [
    ("X-ray", "Chest"),
    ("CT", "Head"),
    ("CT", "Chest"),
    ("MRI", "Brain"),
    ("MRI", "Spine"),
    ("X-ray", "Knee"),
    ("CT", "Pelvis"),
]

FINDINGS_POOL = [
    "No acute intracranial hemorrhage or mass effect detected. Ventricles and sulci normal for age.",
    "Bilateral lung fields clear. Cardiac silhouette is within normal limits. Costophrenic angles sharp.",
    "Mild degenerative spondylosis observed at L4-L5 with minor neural foraminal narrowing.",
    "Acute right-sided middle cerebral artery (MCA) hyperdense sign suggestive of ischemic occlusion.",
    "Subsegmental consolidation noted in the right lower lobe consistent with early focal pneumonia.",
    "Trace pleural effusion in the left base. No evidence of pneumothorax.",
    "Intact anterior and posterior cruciate ligaments. Minor horizontal tear of the medial meniscus.",
    "Non-displaced fracture of the radial head with minimal intra-articular fat pad elevation.",
    "Normal gray-white matter differentiation. No acute territorial infarction or edema.",
    "Stable cardiomegaly with mild pulmonary vascular congestion.",
]


def ensure_placeholder_image(case_id: str, modality: str, body_part: str) -> str:
    """
    Creates a simulated medical grayscale scan in static/images/ if not already present.
    Returns relative path for database storage.
    """
    filename = f"{case_id}.jpg"
    filepath = IMAGES_DIR / filename
    rel_path = f"static/images/{filename}"

    if filepath.exists():
        return rel_path

    img = Image.new("L", (512, 512), color=15)
    draw = ImageDraw.Draw(img)

    if "Head" in body_part or "Brain" in body_part:
        draw.ellipse([100, 70, 412, 442], outline=180, width=6, fill=40)
        draw.ellipse([140, 110, 372, 402], outline=120, width=3, fill=70)
        draw.ellipse([210, 200, 240, 300], fill=140)
        draw.ellipse([272, 200, 302, 300], fill=140)
    elif "Chest" in body_part:
        draw.rectangle([110, 90, 402, 430], outline=160, width=4)
        draw.ellipse([130, 110, 230, 390], fill=30, outline=100, width=2)
        draw.ellipse([282, 110, 382, 390], fill=30, outline=100, width=2)
        draw.ellipse([220, 250, 320, 380], fill=90, outline=140, width=2)
    else:
        draw.rounded_rectangle([180, 80, 332, 432], radius=20, outline=170, width=5, fill=50)
        draw.ellipse([210, 210, 302, 302], fill=110)

    draw.text((20, 20), f"ID: {case_id}", fill=220)
    draw.text((20, 40), f"MOD: {modality} - {body_part}", fill=180)
    draw.text((360, 20), "TELE-RAD SIM", fill=140)
    draw.text((360, 40), "WINDOW: C/W", fill=140)

    img.save(filepath, "JPEG", quality=85)
    return rel_path


def calculate_static_priority(modality: str, body_part: str, urgency_flag: int, waiting_mins: float = 0.0, ai_triage_score: float = 0.0) -> Tuple[float, str]:
    """Computes priority score and label matching the config rules."""
    urgency_score = PRIORITY_WEIGHTS["urgency_flag"] if urgency_flag == 1 else 0.0
    mod_weights = PRIORITY_WEIGHTS["modality_body_part"]
    mod_score = mod_weights.get((modality, body_part), mod_weights.get("default", 5.0))
    ai_score = round(min(1.0, max(0.0, ai_triage_score)) * PRIORITY_WEIGHTS["ai_triage_max"], 2)
    waiting_score = min(waiting_mins * PRIORITY_WEIGHTS["waiting_rate_per_min"], PRIORITY_WEIGHTS["waiting_max_cap"])
    total_score = urgency_score + mod_score + ai_score + waiting_score

    if total_score >= PRIORITY_THRESHOLDS["CRITICAL"]:
        label = "Critical"
    elif total_score >= PRIORITY_THRESHOLDS["HIGH"]:
        label = "High"
    else:
        label = "Routine"

    return round(total_score, 1), label


def generate_mock_top_k(case_id: str, modality: str, body_part: str, k: int = 10) -> str:
    """Generates a structured mock Top-K retrieval JSON string."""
    results = []
    base_score = 0.95
    for rank in range(1, k + 1):
        sim_id = f"CASE-{random.randint(1, 100):04d}"
        score = round(base_score - (rank - 1) * 0.035 + random.uniform(-0.01, 0.01), 3)
        reranked = round(min(1.0, score + random.uniform(0.01, 0.04)), 3)
        finding = random.choice(FINDINGS_POOL)
        thumb = f"static/images/{sim_id}.jpg"
        results.append({
            "rank": rank,
            "similar_case_id": sim_id,
            "score": max(0.60, score),
            "reranked_score": max(0.62, reranked),
            "thumbnail_path": thumb,
            "finding": finding,
            "modality": modality,
            "body_part": body_part,
        })

    payload = {
        "case_id": case_id,
        "retrieval_ms": round(random.uniform(32.5, 68.4), 1),
        "results": results,
    }
    return json.dumps(payload)


def seed_database(reset: bool = False, db_path: Optional[Union[str, Path]] = None) -> None:
    """
    Main seeding routine: populates clinics, radiologists, sla_config,
    100 completed historical cases, and 15 open cases.
    """
    logger.info("Initializing schema and database tables...")
    init_db(reset=reset, db_path=db_path)

    conn = get_db_connection(db_path)
    try:
        with conn:
            # 1. Seed SLA Configuration
            for label, minutes in SLA_LIMITS_MINUTES.items():
                conn.execute(
                    "INSERT OR REPLACE INTO sla_config (priority_label, max_minutes) VALUES (?, ?);",
                    (label, minutes)
                )

            # 2. Seed Clinics
            for cid, name, loc in CLINICS_DATA:
                conn.execute(
                    "INSERT OR REPLACE INTO clinics (clinic_id, name, location) VALUES (?, ?, ?);",
                    (cid, name, loc)
                )

            # 3. Seed Radiologists
            for rad in RADIOLOGISTS_DATA:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO radiologists
                    (rad_id, name, subspecialty, max_capacity, is_active, avg_report_minutes)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    rad
                )

            # Check existing cases to avoid duplication when not resetting
            existing_count = conn.execute("SELECT COUNT(*) as cnt FROM cases;").fetchone()["cnt"]
            if existing_count > 0 and not reset:
                logger.info(f"Database already contains {existing_count} cases. Skipping case generation.")
                return

            now = datetime.now(timezone.utc)
            event_counter = 1

            # 4. Seed 100 Historical Completed Cases (last 14 days)
            for i in range(1, 101):
                case_id = f"CASE-{i:04d}"
                patient_ref = f"PT-{i:04d}"
                clinic_id = random.choice(CLINICS_DATA)[0]
                modality, body_part = random.choice(MODALITIES_BODY_PARTS)
                urgency = 1 if random.random() < 0.25 else 0

                days_ago = random.uniform(0.5, 13.8)
                upload_dt = now - timedelta(days=days_ago)

                ai_conf = random.uniform(0.65, 0.95) if (urgency == 1 and random.random() < 0.6) else random.uniform(0.0, 0.3)
                priority_score, priority_label = calculate_static_priority(modality, body_part, urgency, ai_triage_score=ai_conf)
                img_path = ensure_placeholder_image(case_id, modality, body_part)

                assigned_rad = "RAD-004"
                if body_part in ["Head", "Brain", "Spine"]:
                    assigned_rad = random.choice(["RAD-001", "RAD-006"])
                elif body_part == "Chest":
                    assigned_rad = random.choice(["RAD-002", "RAD-005"])
                elif body_part in ["Knee", "Pelvis"]:
                    assigned_rad = "RAD-003"

                status = "Returned_To_Clinic"

                conn.execute(
                    """
                    INSERT INTO cases (
                        case_id, clinic_id, patient_ref, modality, body_part,
                        urgency_flag, image_path, upload_time, status,
                        priority_label, priority_score, assigned_rad_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        case_id, clinic_id, patient_ref, modality, body_part,
                        urgency, img_path, upload_dt.isoformat(), status,
                        priority_label, priority_score, assigned_rad
                    )
                )

                t_stored = upload_dt + timedelta(seconds=random.randint(5, 30))
                t_retrieved = t_stored + timedelta(seconds=random.randint(2, 10))
                t_assigned = t_retrieved + timedelta(seconds=random.randint(3, 15))

                wait_mins = random.uniform(10, 45) if priority_label == "Critical" else random.uniform(30, 200)
                t_in_review = t_assigned + timedelta(minutes=wait_mins)

                review_duration = random.uniform(6.5, 18.5)
                t_reported = t_in_review + timedelta(minutes=review_duration)
                t_returned = t_reported + timedelta(seconds=random.randint(5, 60))

                events = [
                    (f"EVT-{event_counter:06d}", case_id, "Uploaded", upload_dt.isoformat(), "ClinicPortal"),
                    (f"EVT-{event_counter+1:06d}", case_id, "Stored", t_stored.isoformat(), "PlatformService"),
                    (f"EVT-{event_counter+2:06d}", case_id, "Retrieved", t_retrieved.isoformat(), "CBIRAdapter"),
                    (f"EVT-{event_counter+3:06d}", case_id, "Assigned", t_assigned.isoformat(), "WorkflowEngine"),
                    (f"EVT-{event_counter+4:06d}", case_id, "In_Review", t_in_review.isoformat(), assigned_rad),
                    (f"EVT-{event_counter+5:06d}", case_id, "Reported", t_reported.isoformat(), assigned_rad),
                    (f"EVT-{event_counter+6:06d}", case_id, "Returned_To_Clinic", t_returned.isoformat(), "WorkflowEngine"),
                ]
                event_counter += 7

                conn.executemany(
                    "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
                    events
                )

                conn.execute(
                    """
                    INSERT INTO assignments (assignment_id, case_id, rad_id, assigned_time, assignment_reason)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (
                        f"ASG-{i:04d}",
                        case_id,
                        assigned_rad,
                        t_assigned.isoformat(),
                        f"Matched {body_part} specialty with balanced workload"
                    )
                )

                conn.execute(
                    """
                    INSERT INTO reports (
                        report_id, case_id, rad_id, review_start_time, report_submit_time,
                        report_text, used_similar_cases, similar_cases_viewed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        f"REP-{i:04d}",
                        case_id,
                        assigned_rad,
                        t_in_review.isoformat(),
                        t_reported.isoformat(),
                        random.choice(FINDINGS_POOL),
                        1 if random.random() < 0.85 else 0,
                        random.randint(2, 6)
                    )
                )

                mock_json = generate_mock_top_k(case_id, modality, body_part, k=10)
                conn.execute(
                    """
                    INSERT INTO retrieval_log (log_id, case_id, top_k_json, retrieval_ms, mode, created_at)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        f"RET-{i:04d}",
                        case_id,
                        mock_json,
                        round(random.uniform(35.0, 65.0), 1),
                        "cbir_rerank" if random.random() < 0.5 else "cbir",
                        t_retrieved.isoformat()
                    )
                )

            # 5. Seed 15 Active Open Cases
            open_states = [
                ("Uploaded", 2),
                ("Retrieved", 2),
                ("Assigned", 8),
                ("In_Review", 3),
            ]

            open_idx = 101
            for target_state, count in open_states:
                for _ in range(count):
                    case_id = f"CASE-{open_idx:04d}"
                    patient_ref = f"PT-{open_idx:04d}"
                    clinic_id = random.choice(CLINICS_DATA)[0]
                    modality, body_part = random.choice(MODALITIES_BODY_PARTS)
                    urgency = 1 if (open_idx % 3 == 0) else 0

                    minutes_ago = random.uniform(15, 360)
                    upload_dt = now - timedelta(minutes=minutes_ago)

                    priority_score, priority_label = calculate_static_priority(
                        modality, body_part, urgency, waiting_mins=minutes_ago
                    )
                    img_path = ensure_placeholder_image(case_id, modality, body_part)

                    assigned_rad = None
                    if target_state in ["Assigned", "In_Review"]:
                        if body_part in ["Head", "Brain", "Spine"]:
                            assigned_rad = random.choice(["RAD-001", "RAD-006"])
                        elif body_part == "Chest":
                            assigned_rad = random.choice(["RAD-002", "RAD-005"])
                        else:
                            assigned_rad = random.choice(["RAD-003", "RAD-004"])

                    conn.execute(
                        """
                        INSERT INTO cases (
                            case_id, clinic_id, patient_ref, modality, body_part,
                            urgency_flag, image_path, upload_time, status,
                            priority_label, priority_score, assigned_rad_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            case_id, clinic_id, patient_ref, modality, body_part,
                            urgency, img_path, upload_dt.isoformat(), target_state,
                            priority_label, priority_score, assigned_rad
                        )
                    )

                    conn.execute(
                        "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
                        (f"EVT-{event_counter:06d}", case_id, "Uploaded", upload_dt.isoformat(), "ClinicPortal")
                    )
                    event_counter += 1

                    if target_state in ["Retrieved", "Assigned", "In_Review"]:
                        t_stored = upload_dt + timedelta(seconds=15)
                        t_retrieved = t_stored + timedelta(seconds=5)
                        conn.execute(
                            "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
                            (f"EVT-{event_counter:06d}", case_id, "Stored", t_stored.isoformat(), "PlatformService")
                        )
                        event_counter += 1
                        conn.execute(
                            "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
                            (f"EVT-{event_counter:06d}", case_id, "Retrieved", t_retrieved.isoformat(), "CBIRAdapter")
                        )
                        event_counter += 1

                        mock_json = generate_mock_top_k(case_id, modality, body_part, k=10)
                        conn.execute(
                            """
                            INSERT INTO retrieval_log (log_id, case_id, top_k_json, retrieval_ms, mode, created_at)
                            VALUES (?, ?, ?, ?, ?, ?);
                            """,
                            (
                                f"RET-{open_idx:04d}",
                                case_id,
                                mock_json,
                                round(random.uniform(35.0, 65.0), 1),
                                "cbir",
                                t_retrieved.isoformat()
                            )
                        )

                    if target_state in ["Assigned", "In_Review"]:
                        t_assigned = upload_dt + timedelta(minutes=5)
                        conn.execute(
                            "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
                            (f"EVT-{event_counter:06d}", case_id, "Assigned", t_assigned.isoformat(), "WorkflowEngine")
                        )
                        event_counter += 1

                        conn.execute(
                            """
                            INSERT INTO assignments (assignment_id, case_id, rad_id, assigned_time, assignment_reason)
                            VALUES (?, ?, ?, ?, ?);
                            """,
                            (
                                f"ASG-{open_idx:04d}",
                                case_id,
                                assigned_rad,
                                t_assigned.isoformat(),
                                f"Assigned to {assigned_rad} based on subspecialty and current load"
                            )
                        )

                    if target_state == "In_Review":
                        t_in_review = upload_dt + timedelta(minutes=15)
                        conn.execute(
                            "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
                            (f"EVT-{event_counter:06d}", case_id, "In_Review", t_in_review.isoformat(), assigned_rad)
                        )
                        event_counter += 1

                    open_idx += 1

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Seed the Tele-Radiology Workflow Intelligence database.")
    parser.add_argument("--reset", action="store_true", help="Drop and rebuild database schema before seeding.")
    args = parser.parse_args()

    seed_database(reset=args.reset)


if __name__ == "__main__":
    main()
