"""
workflow_engine.py
Central Transactional Workflow Orchestrator for Tele-Radiology.

Enforces:
1. Strict legal status state machine transitions.
2. Complete audit logging to the `events` table for all state changes.
3. Automated intake pipeline: Upload -> Store -> CBIR Retrieval -> Dynamic Priority -> Routing.
4. Radiologist review initiation, report submission, and clinic return.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config import LEGAL_STATUS_TRANSITIONS, DATABASE_PATH, IMAGES_DIR
from database import get_db_connection, query_one, execute_write
from priority import compute_priority
from routing import assign_radiologist
from adapters.cbir_adapter import get_similar_cases

logger = logging.getLogger("workflow_engine")


class IllegalStateTransitionError(ValueError):
    """Raised when an illegal workflow state machine transition is attempted."""
    pass


def _generate_id(prefix: str) -> str:
    """Generates unique prefixed identifier."""
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def validate_transition(current_status: str, target_status: str) -> None:
    """
    Validates whether a transition from current_status to target_status is legal.
    Raises IllegalStateTransitionError if not allowed.
    """
    allowed_next_states = LEGAL_STATUS_TRANSITIONS.get(current_status, [])
    if target_status not in allowed_next_states:
        raise IllegalStateTransitionError(
            f"Illegal state transition from '{current_status}' to '{target_status}'. "
            f"Allowed transitions from '{current_status}' are: {allowed_next_states}"
        )


def log_workflow_event(
    conn,
    case_id: str,
    status: str,
    actor: str,
    timestamp: Optional[str] = None
) -> str:
    """
    Inserts an immutable audit event into the events table.
    Must be called inside an active database transaction.
    """
    event_id = _generate_id("EVT")
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
        (event_id, case_id, status, ts, actor)
    )
    return event_id


def create_case(
    data: Dict[str, Any],
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Intake pipeline for a new case (called by Platform Webhook or locally):
    1. Validates required fields and initializes case in 'Uploaded' status.
    2. Logs 'Uploaded' event.
    3. Transitions to 'Stored' -> logs event.
    4. Invokes CBIR adapter to retrieve Top-10 similar cases -> logs retrieval_log & 'Retrieved' event.
    5. Computes priority score, label, and explainability breakdown.
    6. Executes subspecialty & load-balanced routing to assign radiologist -> logs 'Assigned' event.
    7. Returns complete case summary.
    """
    case_id = data.get("case_id") or _generate_id("CASE")
    clinic_id = data.get("clinic_id")
    patient_ref = data.get("patient_ref") or f"PT-{uuid.uuid4().hex[:4].upper()}"
    modality = data.get("modality", "X-ray")
    body_part = data.get("body_part", "Chest")
    urgency_flag = 1 if int(data.get("urgency_flag", 0)) == 1 else 0
    image_path = data.get("image_path") or f"static/images/{case_id}.jpg"
    upload_time = data.get("upload_time") or datetime.now(timezone.utc).isoformat()
    ai_triage_score = float(data.get("ai_triage_score", 0.0))

    if not clinic_id:
        raise ValueError("Missing required clinic_id for case creation.")

    conn = get_db_connection(db_path)
    try:
        with conn:
            # Verify clinic exists
            clinic = conn.execute("SELECT clinic_id FROM clinics WHERE clinic_id = ?;", (clinic_id,)).fetchone()
            if not clinic:
                raise ValueError(f"Clinic '{clinic_id}' does not exist.")

            # Initial insert with status 'Uploaded'
            conn.execute(
                """
                INSERT INTO cases (
                    case_id, clinic_id, patient_ref, modality, body_part,
                    urgency_flag, image_path, upload_time, status,
                    priority_label, priority_score, assigned_rad_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Uploaded', 'Routine', 0.0, NULL);
                """,
                (
                    case_id, clinic_id, patient_ref, modality, body_part,
                    urgency_flag, image_path, upload_time
                )
            )
            log_workflow_event(conn, case_id, "Uploaded", data.get("actor", "ClinicPortal"), upload_time)

            # Transition 1: Uploaded -> Stored
            validate_transition("Uploaded", "Stored")
            conn.execute("UPDATE cases SET status = 'Stored' WHERE case_id = ?;", (case_id,))
            t_stored = datetime.now(timezone.utc).isoformat()
            log_workflow_event(conn, case_id, "Stored", "PlatformService", t_stored)

            # Step 2: Retrieve similar cases via CBIR
            cbir_payload = get_similar_cases(case_id, k=10)
            retrieval_id = _generate_id("RET")
            top_k_json = json.dumps(cbir_payload)
            retrieval_ms = float(cbir_payload.get("retrieval_ms", 45.0))
            t_retrieved = datetime.now(timezone.utc).isoformat()

            conn.execute(
                """
                INSERT INTO retrieval_log (log_id, case_id, top_k_json, retrieval_ms, mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (retrieval_id, case_id, top_k_json, retrieval_ms, "cbir", t_retrieved)
            )

            # Transition 2: Stored -> Retrieved
            validate_transition("Stored", "Retrieved")
            conn.execute("UPDATE cases SET status = 'Retrieved' WHERE case_id = ?;", (case_id,))
            log_workflow_event(conn, case_id, "Retrieved", "CBIRAdapter", t_retrieved)

            # Step 3: Priority Calculation
            case_dict = {
                "case_id": case_id,
                "urgency_flag": urgency_flag,
                "modality": modality,
                "body_part": body_part,
                "upload_time": upload_time,
                "ai_triage_score": ai_triage_score,
            }
            score, label, breakdown = compute_priority(case_dict)

            # Step 4: Routing & Assignment
            rad_id, reason = assign_radiologist(case_dict, db_path=db_path)

            if rad_id:
                # Transition 3: Retrieved -> Assigned
                validate_transition("Retrieved", "Assigned")
                t_assigned = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    UPDATE cases
                    SET status = 'Assigned',
                        priority_score = ?,
                        priority_label = ?,
                        assigned_rad_id = ?
                    WHERE case_id = ?;
                    """,
                    (score, label, rad_id, case_id)
                )
                log_workflow_event(conn, case_id, "Assigned", "WorkflowEngine", t_assigned)

                # Record assignment
                assignment_id = _generate_id("ASG")
                conn.execute(
                    """
                    INSERT INTO assignments (assignment_id, case_id, rad_id, assigned_time, assignment_reason)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (assignment_id, case_id, rad_id, t_assigned, reason)
                )
                final_status = "Assigned"
            else:
                # Radiologists full or unavailable: stays in 'Retrieved' queue
                conn.execute(
                    """
                    UPDATE cases
                    SET priority_score = ?,
                        priority_label = ?
                    WHERE case_id = ?;
                    """,
                    (score, label, case_id)
                )
                final_status = "Retrieved"
                logger.warning(f"Case {case_id} could not be immediately assigned: {reason}")

        return {
            "case_id": case_id,
            "patient_ref": patient_ref,
            "status": final_status,
            "priority_score": score,
            "priority_label": label,
            "assigned_rad_id": rad_id,
            "assignment_reason": reason,
            "breakdown": breakdown,
            "cbir_top_k": cbir_payload,
        }

    finally:
        conn.close()


def start_review(
    case_id: str,
    rad_id: str,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Transitions a case from 'Assigned' to 'In_Review'.
    Logs 'In_Review' event with actor rad_id.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            case = conn.execute("SELECT status, assigned_rad_id FROM cases WHERE case_id = ?;", (case_id,)).fetchone()
            if not case:
                raise ValueError(f"Case '{case_id}' does not exist.")

            current_status = case["status"]
            validate_transition(current_status, "In_Review")

            # Update status
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE cases SET status = 'In_Review', assigned_rad_id = ? WHERE case_id = ?;",
                (rad_id, case_id)
            )
            log_workflow_event(conn, case_id, "In_Review", rad_id, now_iso)
            logger.info(f"Case {case_id} moved to In_Review by {rad_id}.")

            return {
                "case_id": case_id,
                "status": "In_Review",
                "assigned_rad_id": rad_id,
                "review_start_time": now_iso
            }
    finally:
        conn.close()


def submit_report(
    case_id: str,
    rad_id: str,
    text: str,
    similar_cases_viewed: int = 0,
    used_similar_cases: Optional[int] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Submits diagnostic report for a case in 'In_Review'.
    Calculates review timing, inserts report record, transitions to 'Reported'.
    Logs 'Reported' event with actor rad_id.
    """
    if not text or not text.strip():
        raise ValueError("Report diagnostic text cannot be empty.")

    conn = get_db_connection(db_path)
    try:
        with conn:
            case = conn.execute("SELECT status FROM cases WHERE case_id = ?;", (case_id,)).fetchone()
            if not case:
                raise ValueError(f"Case '{case_id}' does not exist.")

            current_status = case["status"]
            validate_transition(current_status, "Reported")

            # Determine review start time from the 'In_Review' event
            review_event = conn.execute(
                """
                SELECT timestamp FROM events
                WHERE case_id = ? AND status = 'In_Review'
                ORDER BY timestamp DESC LIMIT 1;
                """,
                (case_id,)
            ).fetchone()

            now_iso = datetime.now(timezone.utc).isoformat()
            start_iso = review_event["timestamp"] if review_event else now_iso

            used_flag = used_similar_cases if used_similar_cases is not None else (1 if similar_cases_viewed > 0 else 0)
            report_id = _generate_id("REP")

            conn.execute(
                """
                INSERT OR REPLACE INTO reports (
                    report_id, case_id, rad_id, review_start_time,
                    report_submit_time, report_text, used_similar_cases, similar_cases_viewed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    report_id, case_id, rad_id, start_iso,
                    now_iso, text.strip(), used_flag, int(similar_cases_viewed)
                )
            )

            conn.execute("UPDATE cases SET status = 'Reported' WHERE case_id = ?;", (case_id,))
            log_workflow_event(conn, case_id, "Reported", rad_id, now_iso)
            logger.info(f"Report submitted for case {case_id} by {rad_id}.")

            return {
                "report_id": report_id,
                "case_id": case_id,
                "status": "Reported",
                "submit_time": now_iso,
                "used_similar_cases": used_flag,
                "similar_cases_viewed": similar_cases_viewed,
            }
    finally:
        conn.close()


def return_to_clinic(
    case_id: str,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Finalizes case transmission back to referral clinic portal.
    Transitions 'Reported' -> 'Returned_To_Clinic'.
    Logs 'Returned_To_Clinic' event.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            case = conn.execute("SELECT status FROM cases WHERE case_id = ?;", (case_id,)).fetchone()
            if not case:
                raise ValueError(f"Case '{case_id}' does not exist.")

            current_status = case["status"]
            validate_transition(current_status, "Returned_To_Clinic")

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE cases SET status = 'Returned_To_Clinic' WHERE case_id = ?;", (case_id,))
            log_workflow_event(conn, case_id, "Returned_To_Clinic", "WorkflowEngine", now_iso)
            logger.info(f"Case {case_id} successfully returned to clinic.")

            return {
                "case_id": case_id,
                "status": "Returned_To_Clinic",
                "timestamp": now_iso,
            }
    finally:
        conn.close()
