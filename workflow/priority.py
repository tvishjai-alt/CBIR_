"""
priority.py
Dynamic Priority Scoring Engine with Starvation Prevention and Explainability.

Calculates multi-criteria priority scores based on:
1. Urgency Flag (Clinical referral source)
2. Modality & Anatomical Region (Risk-stratified weighting)
3. AI Triage / Confidence Score (Optional predictive screening)
4. Waiting-Time Term (Dynamic ramp preventing routine case starvation)

Returns (priority_score, priority_label, breakdown_dict).
"""

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple, Union
import dateutil.parser

from config import PRIORITY_WEIGHTS, PRIORITY_THRESHOLDS


def _parse_timestamp(ts: Union[str, datetime]) -> datetime:
    """Safely converts string or datetime timestamp to a UTC-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    parsed = dateutil.parser.isoparse(str(ts))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def compute_priority(
    case: Union[Dict[str, Any], Mapping[str, Any]],
    now: Optional[datetime] = None
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Computes priority score, bucket label, and explainability breakdown.

    Parameters:
        case: Dictionary or row containing:
              - urgency_flag (int or bool)
              - modality (str)
              - body_part (str)
              - upload_time (str or datetime)
              - optional: ai_triage_score (float 0.0-1.0)
        now: Optional current datetime (defaults to datetime.now(timezone.utc))

    Returns:
        (total_score, priority_label, breakdown_dict)
    """
    current_time = _parse_timestamp(now) if now else datetime.now(timezone.utc)

    # 1. Urgency Flag Component (Clinic Emergency / Stat referral)
    urgency_raw = case.get("urgency_flag", 0)
    is_urgent = bool(int(urgency_raw)) if urgency_raw is not None else False
    urgency_score = PRIORITY_WEIGHTS["urgency_flag"] if is_urgent else 0.0

    # 2. Modality & Anatomical Body Part Component
    modality = str(case.get("modality", "")).strip()
    body_part = str(case.get("body_part", "")).strip().title()

    mod_dict = PRIORITY_WEIGHTS["modality_body_part"]
    # Exact tuple match or default fallback
    modality_body_score = mod_dict.get(
        (modality, body_part),
        mod_dict.get("default", 5.0)
    )

    # 3. AI Triage Confidence Term (Optional automated detection score)
    ai_raw = case.get("ai_triage_score", 0.0)
    try:
        ai_conf = float(ai_raw) if ai_raw is not None else 0.0
    except (ValueError, TypeError):
        ai_conf = 0.0
    ai_conf = max(0.0, min(1.0, ai_conf))
    ai_triage_score = round(ai_conf * PRIORITY_WEIGHTS["ai_triage_max"], 2)

    # 4. Waiting-Time Term (Starvation Prevention)
    upload_time_raw = case.get("upload_time")
    if upload_time_raw:
        upload_dt = _parse_timestamp(upload_time_raw)
        elapsed_seconds = max(0.0, (current_time - upload_dt).total_seconds())
        waiting_minutes = elapsed_seconds / 60.0
    else:
        waiting_minutes = 0.0

    rate = PRIORITY_WEIGHTS["waiting_rate_per_min"]
    cap = PRIORITY_WEIGHTS["waiting_max_cap"]
    waiting_score = min(waiting_minutes * rate, cap)
    waiting_score = round(waiting_score, 2)

    # Total Score Calculation
    total_score = round(urgency_score + modality_body_score + ai_triage_score + waiting_score, 2)

    # Classification into Priority Buckets
    if total_score >= PRIORITY_THRESHOLDS["CRITICAL"]:
        priority_label = "Critical"
    elif total_score >= PRIORITY_THRESHOLDS["HIGH"]:
        priority_label = "High"
    else:
        priority_label = "Routine"

    # Full Explainability Breakdown
    breakdown = {
        "case_id": case.get("case_id"),
        "urgency_score": urgency_score,
        "urgency_flag": 1 if is_urgent else 0,
        "modality_body_score": modality_body_score,
        "modality": modality,
        "body_part": body_part,
        "ai_triage_score": ai_triage_score,
        "ai_triage_confidence": ai_conf,
        "waiting_minutes": round(waiting_minutes, 1),
        "waiting_score": waiting_score,
        "total_score": total_score,
        "priority_label": priority_label,
        "explanation": (
            f"Urgency (+{urgency_score:.0f}) + Modality/Body (+{modality_body_score:.0f}) "
            f"+ AI Triage (+{ai_triage_score:.1f}) + Waiting ramp (+{waiting_score:.1f} for {waiting_minutes:.0f}m) "
            f"= Total {total_score:.1f} ({priority_label})"
        )
    }

    return total_score, priority_label, breakdown
