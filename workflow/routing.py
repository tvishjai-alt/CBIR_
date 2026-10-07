"""
routing.py
Intelligent Radiologist Routing and Workload Balancing Engine.

Implements:
1. Subspecialty anatomical matching (Neuro, Chest, MSK) with General fallback.
2. Hard concurrency capacity constraints (max_capacity check).
3. Priority-weighted workload minimization (Critical=3, High=2, Routine=1).
4. Deterministic tie-breaking via historical average turnaround.
5. Dynamic rebalancing to shed Routine backlog from overloaded specialists.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union
from pathlib import Path

from config import (
    ROUTING_LOAD_WEIGHTS,
    ROUTING_SUBSPECIALTY_MAP,
    ROUTING_FALLBACK_SUBSPECIALTY,
    ROUTING_REBALANCE_THRESHOLD,
    DATABASE_PATH,
)
from database import get_db_connection, query_all, query_one, execute_write

logger = logging.getLogger("routing")


def determine_target_subspecialty(body_part: str) -> str:
    """
    Maps an anatomical body part or organ to its radiological subspecialty.
    Falls back to 'General' if no specialized match is found.
    """
    clean_part = body_part.strip().lower()
    for keyword, subspecialty in ROUTING_SUBSPECIALTY_MAP.items():
        if keyword in clean_part:
            return subspecialty
    return ROUTING_FALLBACK_SUBSPECIALTY


def get_radiologist_current_load(rad_id: str, db_path: Optional[Union[str, Path]] = None) -> Tuple[int, int]:
    """
    Calculates active case count and priority-weighted workload for a given radiologist.
    Only counts open cases with status in ('Assigned', 'In_Review').

    Returns:
        (active_case_count, weighted_load_points)
    """
    sql = """
        SELECT priority_label
        FROM cases
        WHERE assigned_rad_id = ?
          AND status IN ('Assigned', 'In_Review');
    """
    rows = query_all(sql, (rad_id,), db_path=db_path)
    count = len(rows)
    weighted_score = sum(ROUTING_LOAD_WEIGHTS.get(r["priority_label"], 1) for r in rows)
    return count, weighted_score


def assign_radiologist(
    case: Union[Dict[str, Any], Mapping[str, Any]],
    db_path: Optional[Union[str, Path]] = None
) -> Tuple[Optional[str], str]:
    """
    Selects the optimal active radiologist for an incoming case.

    Rules:
    1. Filter active radiologists whose subspecialty matches the case body part;
       if none are available or below capacity, fall back to active General radiologists.
    2. Exclude anyone currently at or exceeding max_capacity.
    3. Pick the lowest current weighted load (Critical=3, High=2, Routine=1).
       Tie-break by lowest avg_report_minutes.
    4. Provide clear assignment_reason in plain English.
    5. If nobody is available, returns (None, reason).

    Returns:
        (assigned_rad_id, assignment_reason)
    """
    body_part = str(case.get("body_part", ""))
    target_subspecialty = determine_target_subspecialty(body_part)

    conn = get_db_connection(db_path)
    try:
        # Fetch active radiologists
        all_active = conn.execute(
            """
            SELECT rad_id, name, subspecialty, max_capacity, avg_report_minutes
            FROM radiologists
            WHERE is_active = 1;
            """
        ).fetchall()

        if not all_active:
            return None, "No active radiologists in the system"

        # Separate into matching specialists vs generalists
        specialists = [r for r in all_active if r["subspecialty"] == target_subspecialty]
        generalists = [r for r in all_active if r["subspecialty"] == ROUTING_FALLBACK_SUBSPECIALTY]

        # Calculate live load for candidate radiologists
        def evaluate_candidates(candidates: List[Any]):
            viable = []
            for r in candidates:
                rad_id = r["rad_id"]
                active_count, weighted_load = get_radiologist_current_load(rad_id, db_path=db_path)
                if active_count < r["max_capacity"]:
                    viable.append({
                        "rad_id": rad_id,
                        "name": r["name"],
                        "subspecialty": r["subspecialty"],
                        "max_capacity": r["max_capacity"],
                        "active_count": active_count,
                        "weighted_load": weighted_load,
                        "avg_report_minutes": r["avg_report_minutes"],
                    })
            # Sort primarily by lowest weighted_load, then lowest avg_report_minutes
            viable.sort(key=lambda x: (x["weighted_load"], x["avg_report_minutes"]))
            return viable

        # Step 1: Try matching subspecialty first
        viable_specialists = evaluate_candidates(specialists)
        if viable_specialists:
            best = viable_specialists[0]
            reason = (
                f"{best['subspecialty']} specialist {best['name']} assigned with lowest load "
                f"({best['active_count']}/{best['max_capacity']} active cases, "
                f"{best['weighted_load']} load pts, avg {best['avg_report_minutes']}m)"
            )
            return best["rad_id"], reason

        # Step 2: Fall back to Generalists if subspecialty is not General and no specialist was available
        if target_subspecialty != ROUTING_FALLBACK_SUBSPECIALTY:
            viable_generalists = evaluate_candidates(generalists)
            if viable_generalists:
                best = viable_generalists[0]
                reason = (
                    f"No {target_subspecialty} specialist available below capacity. "
                    f"Fell back to Generalist {best['name']} with lowest load "
                    f"({best['active_count']}/{best['max_capacity']} active cases, "
                    f"{best['weighted_load']} load pts)"
                )
                return best["rad_id"], reason

        # Step 3: No eligible radiologist below max_capacity
        return None, f"All eligible radiologists for {target_subspecialty} are at maximum capacity"

    finally:
        conn.close()


def rebalance(db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """
    Rebalances workload when the disparity between the highest-load and underloaded
    radiologists exceeds ROUTING_REBALANCE_THRESHOLD.
    Only reassigns 'Routine' cases currently in 'Assigned' status.

    Returns:
        List of rebalanced case transfer summaries.
    """
    conn = get_db_connection(db_path)
    rebalanced_transfers = []

    try:
        active_rads = conn.execute(
            "SELECT rad_id, name, subspecialty, max_capacity FROM radiologists WHERE is_active = 1;"
        ).fetchall()

        if len(active_rads) < 2:
            return []

        # Compute load for each active radiologist
        rad_loads = []
        for r in active_rads:
            rid = r["rad_id"]
            cnt, wload = get_radiologist_current_load(rid, db_path=db_path)
            rad_loads.append({
                "rad_id": rid,
                "name": r["name"],
                "subspecialty": r["subspecialty"],
                "max_capacity": r["max_capacity"],
                "active_count": cnt,
                "weighted_load": wload,
            })

        rad_loads.sort(key=lambda x: x["weighted_load"])
        max_rad = rad_loads[-1]

        # Find reassignable cases: 'Routine' cases in 'Assigned' status owned by max_rad
        reassignable_cases = conn.execute(
            """
            SELECT case_id, body_part, priority_label, upload_time
            FROM cases
            WHERE assigned_rad_id = ?
              AND status = 'Assigned'
              AND priority_label = 'Routine'
            ORDER BY upload_time DESC;
            """,
            (max_rad["rad_id"],)
        ).fetchall()

        if not reassignable_cases:
            logger.info(f"Overloaded radiologist {max_rad['name']} has no reassignable Routine cases.")
            return []

        now_iso = datetime.now(timezone.utc).isoformat()

        # Iterate through reassignable cases and find an eligible underloaded recipient
        for case in reassignable_cases:
            case_id = case["case_id"]
            body_part = case["body_part"]
            target_subspec = determine_target_subspecialty(body_part)

            # Find an underloaded radiologist whose load difference >= threshold
            recipient = None
            for candidate in rad_loads:
                if candidate["rad_id"] == max_rad["rad_id"]:
                    continue
                load_diff = max_rad["weighted_load"] - candidate["weighted_load"]
                if load_diff < ROUTING_REBALANCE_THRESHOLD:
                    continue
                # Eligibility check: matching subspecialty OR Generalist
                is_eligible = (
                    candidate["subspecialty"] == target_subspec or
                    candidate["subspecialty"] == ROUTING_FALLBACK_SUBSPECIALTY
                )
                has_capacity = candidate["active_count"] < candidate["max_capacity"]
                if is_eligible and has_capacity:
                    recipient = candidate
                    break

            if recipient:
                with conn:
                    # Legal transition: Assigned -> Reassigned -> Assigned
                    conn.execute(
                        "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
                        (f"EVT-REB-{case_id[:8]}-1", case_id, "Reassigned", now_iso, "WorkflowEngine")
                    )
                    conn.execute(
                        "INSERT INTO events (event_id, case_id, status, timestamp, actor) VALUES (?, ?, ?, ?, ?);",
                        (f"EVT-REB-{case_id[:8]}-2", case_id, "Assigned", now_iso, "WorkflowEngine")
                    )
                    # Update case assigned_rad_id
                    conn.execute(
                        "UPDATE cases SET assigned_rad_id = ?, status = 'Assigned' WHERE case_id = ?;",
                        (recipient["rad_id"], case_id)
                    )
                    # Insert assignment audit
                    reason = f"Rebalanced from {max_rad['name']} (load {max_rad['weighted_load']}) to balance queue"
                    conn.execute(
                        """
                        INSERT INTO assignments (assignment_id, case_id, rad_id, assigned_time, assignment_reason)
                        VALUES (?, ?, ?, ?, ?);
                        """,
                        (f"ASG-REB-{case_id[:8]}", case_id, recipient["rad_id"], now_iso, reason)
                    )

                rebalanced_transfers.append({
                    "case_id": case_id,
                    "from_rad": max_rad["rad_id"],
                    "to_rad": recipient["rad_id"],
                    "reason": reason,
                })
                logger.info(f"Rebalanced {case_id} from {max_rad['rad_id']} to {recipient['rad_id']}.")
                break  # Rebalance one case per run to prevent oscillation

        return rebalanced_transfers

    finally:
        conn.close()
