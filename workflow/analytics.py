"""
analytics.py
Comprehensive SQL-based Analytics and Turnaround Monitoring for Tele-Radiology.

All metrics are SQL-derived and optionally filterable by ISO date range (start_date, end_date):
1. Report turnaround time (TAT = Reported timestamp - Uploaded timestamp) from the events table.
   Aggregated into mean, median, and 90th percentile, grouped by priority and by radiologist.
2. Direct reading review duration (report_submit_time - review_start_time from reports).
3. SLA breach rate per priority against sla_config + impending at-risk queue (< 25% margin).
4. Radiologist workload distribution (open assigned cases vs completed today vs capacity).
5. Throughput trends per hour and per day.
6. Real-time prioritized pending reports queue.
"""

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config import DATABASE_PATH, SLA_LIMITS_MINUTES, SLA_AT_RISK_RATIO
from database import get_db_connection, query_all, query_one


def _percentile(values: List[float], p: float) -> float:
    """Computes the p-th percentile from a list of numerical values."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return round(sorted_vals[int(k)], 1)
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return round(d0 + d1, 1)


def get_turnaround_statistics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Calculates case turnaround time (Reported time minus Uploaded time from events table).
    Returns overall mean, median, p90, and groupings by priority and by radiologist.
    Optionally filterable by date range on upload_time.
    """
    conn = get_db_connection(db_path)
    try:
        where_clauses = ["c.status IN ('Reported', 'Returned_To_Clinic')"]
        params: List[Any] = []

        if start_date:
            where_clauses.append("e_up.timestamp >= ?")
            params.append(start_date)
        if end_date:
            where_clauses.append("e_up.timestamp <= ?")
            params.append(end_date)

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                c.case_id,
                c.priority_label,
                c.assigned_rad_id,
                r.name as rad_name,
                s.max_minutes as sla_max,
                (julianday(e_rep.timestamp) - julianday(e_up.timestamp)) * 1440.0 as tat_mins
            FROM cases c
            JOIN events e_up ON c.case_id = e_up.case_id AND e_up.status = 'Uploaded'
            JOIN events e_rep ON c.case_id = e_rep.case_id AND e_rep.status = 'Reported'
            JOIN sla_config s ON c.priority_label = s.priority_label
            LEFT JOIN radiologists r ON c.assigned_rad_id = r.rad_id
            WHERE {where_sql};
        """
        rows = conn.execute(sql, params).fetchall()

        all_tats = [r["tat_mins"] for r in rows if r["tat_mins"] is not None]
        overall = {
            "count": len(all_tats),
            "mean": round(sum(all_tats) / len(all_tats), 1) if all_tats else 0.0,
            "median": _percentile(all_tats, 50),
            "p90": _percentile(all_tats, 90),
        }

        # 1. Group by Priority Label
        by_priority = {}
        for priority in ["Critical", "High", "Routine"]:
            tats = [r["tat_mins"] for r in rows if r["priority_label"] == priority and r["tat_mins"] is not None]
            by_priority[priority] = {
                "count": len(tats),
                "mean": round(sum(tats) / len(tats), 1) if tats else 0.0,
                "median": _percentile(tats, 50),
                "p90": _percentile(tats, 90),
            }

        # 2. Group by Radiologist
        rad_ids = sorted(list({r["assigned_rad_id"] for r in rows if r["assigned_rad_id"]}))
        by_radiologist = {}
        for rid in rad_ids:
            rad_rows = [r for r in rows if r["assigned_rad_id"] == rid and r["tat_mins"] is not None]
            rad_tats = [r["tat_mins"] for r in rad_rows]
            rad_name = rad_rows[0]["rad_name"] if rad_rows else rid
            by_radiologist[rid] = {
                "name": rad_name,
                "count": len(rad_tats),
                "mean": round(sum(rad_tats) / len(rad_tats), 1) if rad_tats else 0.0,
                "median": _percentile(rad_tats, 50),
                "p90": _percentile(rad_tats, 90),
            }

        return {
            "overall": overall,
            "by_priority": by_priority,
            "by_radiologist": by_radiologist,
        }
    finally:
        conn.close()


def get_review_time_statistics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Calculates pure radiologist diagnostic review time:
    review_mins = (report_submit_time - review_start_time) from the reports table.
    Returns overall metrics and breakdown by modality and radiologist.
    """
    conn = get_db_connection(db_path)
    try:
        where_clauses = ["1=1"]
        params: List[Any] = []

        if start_date:
            where_clauses.append("rep.report_submit_time >= ?")
            params.append(start_date)
        if end_date:
            where_clauses.append("rep.report_submit_time <= ?")
            params.append(end_date)

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                rep.report_id,
                rep.case_id,
                rep.rad_id,
                r.name as rad_name,
                c.modality,
                c.priority_label,
                rep.used_similar_cases,
                (julianday(rep.report_submit_time) - julianday(rep.review_start_time)) * 1440.0 as review_mins
            FROM reports rep
            JOIN cases c ON rep.case_id = c.case_id
            JOIN radiologists r ON rep.rad_id = r.rad_id
            WHERE {where_sql};
        """
        rows = conn.execute(sql, params).fetchall()
        times = [r["review_mins"] for r in rows if r["review_mins"] is not None]

        overall = {
            "count": len(times),
            "mean": round(sum(times) / len(times), 1) if times else 0.0,
            "median": _percentile(times, 50),
            "p90": _percentile(times, 90),
        }

        # By Modality
        by_modality = {}
        for mod in ["X-ray", "CT", "MRI"]:
            mod_times = [r["review_mins"] for r in rows if r["modality"] == mod]
            by_modality[mod] = {
                "count": len(mod_times),
                "mean": round(sum(mod_times) / len(mod_times), 1) if mod_times else 0.0,
                "median": _percentile(mod_times, 50),
            }

        # By CBIR Usage
        with_cbir = [r["review_mins"] for r in rows if r["used_similar_cases"] == 1]
        without_cbir = [r["review_mins"] for r in rows if r["used_similar_cases"] == 0]

        return {
            "overall": overall,
            "by_modality": by_modality,
            "with_cbir": {
                "count": len(with_cbir),
                "mean": round(sum(with_cbir) / len(with_cbir), 1) if with_cbir else 0.0,
                "median": _percentile(with_cbir, 50),
            },
            "without_cbir": {
                "count": len(without_cbir),
                "mean": round(sum(without_cbir) / len(without_cbir), 1) if without_cbir else 0.0,
                "median": _percentile(without_cbir, 50),
            }
        }
    finally:
        conn.close()


def get_sla_performance(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """
    Computes SLA compliance and breach rates per priority label.
    Identifies currently at-risk pending cases (< 25% of SLA window remaining).
    """
    conn = get_db_connection(db_path)
    try:
        where_clauses = ["c.status IN ('Reported', 'Returned_To_Clinic')"]
        params: List[Any] = []

        if start_date:
            where_clauses.append("e_rep.timestamp >= ?")
            params.append(start_date)
        if end_date:
            where_clauses.append("e_rep.timestamp <= ?")
            params.append(end_date)

        where_sql = " AND ".join(where_clauses)

        sql_completed = f"""
            SELECT
                c.case_id,
                c.priority_label,
                s.max_minutes as sla_max,
                (julianday(e_rep.timestamp) - julianday(e_up.timestamp)) * 1440.0 as tat_mins
            FROM cases c
            JOIN events e_up ON c.case_id = e_up.case_id AND e_up.status = 'Uploaded'
            JOIN events e_rep ON c.case_id = e_rep.case_id AND e_rep.status = 'Reported'
            JOIN sla_config s ON c.priority_label = s.priority_label
            WHERE {where_sql};
        """
        rows_completed = conn.execute(sql_completed, params).fetchall()

        by_priority = {}
        total_breaches = 0
        total_completed = len(rows_completed)

        for p in ["Critical", "High", "Routine"]:
            p_rows = [r for r in rows_completed if r["priority_label"] == p]
            p_total = len(p_rows)
            p_breaches = sum(1 for r in p_rows if r["tat_mins"] > r["sla_max"])
            total_breaches += p_breaches
            rate = round((p_breaches / p_total * 100.0), 1) if p_total > 0 else 0.0
            by_priority[p] = {
                "total": p_total,
                "breaches": p_breaches,
                "compliant": p_total - p_breaches,
                "breach_rate_pct": rate,
            }

        overall_breach_rate = round((total_breaches / total_completed * 100.0), 1) if total_completed > 0 else 0.0

        # Currently at-risk pending cases
        sql_pending = """
            SELECT
                c.case_id, c.patient_ref, c.modality, c.body_part,
                c.priority_label, c.priority_score, c.upload_time,
                c.assigned_rad_id, r.name as rad_name,
                s.max_minutes as sla_max
            FROM cases c
            LEFT JOIN radiologists r ON c.assigned_rad_id = r.rad_id
            JOIN sla_config s ON c.priority_label = s.priority_label
            WHERE c.status NOT IN ('Reported', 'Returned_To_Clinic');
        """
        rows_pending = conn.execute(sql_pending).fetchall()
        now = datetime.now(timezone.utc)

        at_risk_cases = []
        for r in rows_pending:
            up_dt = datetime.fromisoformat(r["upload_time"])
            if up_dt.tzinfo is None:
                up_dt = up_dt.replace(tzinfo=timezone.utc)
            waiting_mins = (now - up_dt).total_seconds() / 60.0
            sla_max = r["sla_max"]
            rem_mins = sla_max - waiting_mins
            is_at_risk = rem_mins <= (sla_max * SLA_AT_RISK_RATIO)

            if is_at_risk:
                at_risk_cases.append({
                    "case_id": r["case_id"],
                    "patient_ref": r["patient_ref"],
                    "priority_label": r["priority_label"],
                    "waiting_minutes": round(waiting_mins, 1),
                    "remaining_minutes": round(rem_mins, 1),
                    "sla_max": sla_max,
                    "rad_name": r["rad_name"] or "Unassigned",
                    "is_breached": rem_mins <= 0,
                })

        return {
            "total_completed": total_completed,
            "total_breaches": total_breaches,
            "overall_breach_rate_pct": overall_breach_rate,
            "by_priority": by_priority,
            "at_risk_count": len(at_risk_cases),
            "at_risk_cases": at_risk_cases,
        }
    finally:
        conn.close()


def get_radiologist_workloads(db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """
    Computes active case load vs capacity and reports completed today for each radiologist.
    """
    conn = get_db_connection(db_path)
    try:
        rads = conn.execute(
            """
            SELECT rad_id, name, subspecialty, max_capacity, is_active, avg_report_minutes
            FROM radiologists
            ORDER BY subspecialty, name;
            """
        ).fetchall()

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        workloads = []
        for r in rads:
            rid = r["rad_id"]
            open_count = conn.execute(
                """
                SELECT COUNT(*) as c FROM cases
                WHERE assigned_rad_id = ? AND status IN ('Assigned', 'In_Review');
                """,
                (rid,)
            ).fetchone()["c"]

            total_completed = conn.execute(
                """
                SELECT COUNT(*) as c FROM cases
                WHERE assigned_rad_id = ? AND status IN ('Reported', 'Returned_To_Clinic');
                """,
                (rid,)
            ).fetchone()["c"]

            completed_today = conn.execute(
                """
                SELECT COUNT(*) as c FROM reports
                WHERE rad_id = ? AND strftime('%Y-%m-%d', report_submit_time) = ?;
                """,
                (rid, now_utc)
            ).fetchone()["c"]

            workloads.append({
                "rad_id": rid,
                "name": r["name"],
                "subspecialty": r["subspecialty"],
                "max_capacity": r["max_capacity"],
                "active_cases": open_count,
                "completed_total": total_completed,
                "completed_today": completed_today,
                "utilization_pct": round((open_count / r["max_capacity"]) * 100.0, 1),
            })

        return workloads
    finally:
        conn.close()


def get_daily_throughput(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> List[Dict[str, Any]]:
    """
    Calculates completed case volume grouped by day (YYYY-MM-DD).
    """
    conn = get_db_connection(db_path)
    try:
        where_clauses = ["status = 'Reported'"]
        params: List[Any] = []

        if start_date:
            where_clauses.append("timestamp >= ?")
            params.append(start_date)
        if end_date:
            where_clauses.append("timestamp <= ?")
            params.append(end_date)

        where_sql = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                strftime('%Y-%m-%d', timestamp) as report_date,
                COUNT(*) as count
            FROM events
            WHERE {where_sql}
            GROUP BY strftime('%Y-%m-%d', timestamp)
            ORDER BY report_date ASC;
        """
        rows = conn.execute(sql, params).fetchall()
        return [{"date": r["report_date"], "count": r["count"]} for r in rows]
    finally:
        conn.close()


def get_hourly_throughput(
    target_date: Optional[str] = None,
    db_path: Optional[Union[str, Path]] = None
) -> List[Dict[str, Any]]:
    """
    Calculates completed case volume grouped by hour (00 to 23).
    """
    conn = get_db_connection(db_path)
    try:
        where_sql = "status = 'Reported'"
        params: List[Any] = []
        if target_date:
            where_sql += " AND strftime('%Y-%m-%d', timestamp) = ?"
            params.append(target_date)

        sql = f"""
            SELECT
                strftime('%H', timestamp) as report_hour,
                COUNT(*) as count
            FROM events
            WHERE {where_sql}
            GROUP BY strftime('%H', timestamp)
            ORDER BY report_hour ASC;
        """
        rows = conn.execute(sql, params).fetchall()
        return [{"hour": int(r["report_hour"]), "count": r["count"]} for r in rows]
    finally:
        conn.close()


def get_pending_queue_by_priority(db_path: Optional[Union[str, Path]] = None) -> Dict[str, int]:
    """
    Returns counts of open pending cases grouped by priority label.
    """
    conn = get_db_connection(db_path)
    try:
        sql = """
            SELECT priority_label, COUNT(*) as count
            FROM cases
            WHERE status NOT IN ('Reported', 'Returned_To_Clinic')
            GROUP BY priority_label;
        """
        rows = conn.execute(sql).fetchall()
        res = {"Critical": 0, "High": 0, "Routine": 0}
        for r in rows:
            res[r["priority_label"]] = r["count"]
        return res
    finally:
        conn.close()


def get_pending_reports_queue(db_path: Optional[Union[str, Path]] = None) -> List[Dict[str, Any]]:
    """
    Returns full details for all open pending cases sorted by priority_score descending.
    """
    conn = get_db_connection(db_path)
    try:
        sql = """
            SELECT
                c.case_id, c.patient_ref, c.modality, c.body_part,
                c.urgency_flag, c.upload_time, c.status, c.priority_label,
                c.priority_score, c.assigned_rad_id,
                r.name as rad_name, cl.name as clinic_name,
                s.max_minutes as sla_max
            FROM cases c
            LEFT JOIN radiologists r ON c.assigned_rad_id = r.rad_id
            LEFT JOIN clinics cl ON c.clinic_id = cl.clinic_id
            JOIN sla_config s ON c.priority_label = s.priority_label
            WHERE c.status NOT IN ('Reported', 'Returned_To_Clinic')
            ORDER BY c.priority_score DESC, c.upload_time ASC;
        """
        rows = conn.execute(sql).fetchall()
        now = datetime.now(timezone.utc)
        results = []

        for r in rows:
            up_dt = datetime.fromisoformat(r["upload_time"])
            if up_dt.tzinfo is None:
                up_dt = up_dt.replace(tzinfo=timezone.utc)
            waiting_mins = round((now - up_dt).total_seconds() / 60.0, 1)
            rem_mins = round(r["sla_max"] - waiting_mins, 1)

            results.append({
                "case_id": r["case_id"],
                "patient_ref": r["patient_ref"],
                "modality": r["modality"],
                "body_part": r["body_part"],
                "urgency_flag": r["urgency_flag"],
                "status": r["status"],
                "priority_label": r["priority_label"],
                "priority_score": r["priority_score"],
                "assigned_rad_id": r["assigned_rad_id"],
                "rad_name": r["rad_name"] or "Unassigned",
                "clinic_name": r["clinic_name"],
                "waiting_minutes": waiting_mins,
                "remaining_minutes": rem_mins,
                "is_at_risk": rem_mins <= (r["sla_max"] * SLA_AT_RISK_RATIO) and rem_mins > 0,
                "is_breached": rem_mins <= 0,
            })

        return results
    finally:
        conn.close()
