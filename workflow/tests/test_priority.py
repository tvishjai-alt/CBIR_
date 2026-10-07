"""
tests/test_priority.py
Unit tests for Dynamic Priority Scoring Engine and Starvation Prevention.
"""

from datetime import datetime, timedelta, timezone
import pytest

from priority import compute_priority
from config import PRIORITY_WEIGHTS, PRIORITY_THRESHOLDS


def test_urgency_flag_weight():
    """Verifies that urgency_flag=1 adds precisely 40 points."""
    now = datetime.now(timezone.utc)
    case_non_urgent = {
        "modality": "X-ray",
        "body_part": "Chest",
        "urgency_flag": 0,
        "upload_time": now.isoformat(),
    }
    case_urgent = {
        "modality": "X-ray",
        "body_part": "Chest",
        "urgency_flag": 1,
        "upload_time": now.isoformat(),
    }

    score_non_urgent, _, bd_non_urgent = compute_priority(case_non_urgent, now=now)
    score_urgent, _, bd_urgent = compute_priority(case_urgent, now=now)

    assert bd_non_urgent["urgency_score"] == 0.0
    assert bd_urgent["urgency_score"] == 40.0
    assert round(score_urgent - score_non_urgent, 1) == 40.0


def test_modality_body_part_weights():
    """Verifies risk-stratified points for anatomical regions."""
    now = datetime.now(timezone.utc)

    # CT Head = 20
    _, _, bd_ct_head = compute_priority({"modality": "CT", "body_part": "Head", "upload_time": now}, now=now)
    assert bd_ct_head["modality_body_score"] == 20.0

    # MRI Brain = 15
    _, _, bd_mri_brain = compute_priority({"modality": "MRI", "body_part": "Brain", "upload_time": now}, now=now)
    assert bd_mri_brain["modality_body_score"] == 15.0

    # X-ray Chest = 10
    _, _, bd_xray_chest = compute_priority({"modality": "X-ray", "body_part": "Chest", "upload_time": now}, now=now)
    assert bd_xray_chest["modality_body_score"] == 10.0

    # Default / Other = 5
    _, _, bd_other = compute_priority({"modality": "Ultrasound", "body_part": "Abdomen", "upload_time": now}, now=now)
    assert bd_other["modality_body_score"] == 5.0


def test_ai_triage_confidence_term():
    """Verifies that AI triage confidence (0.0 - 1.0) scales up to max 20 points."""
    now = datetime.now(timezone.utc)

    _, _, bd_half = compute_priority(
        {"modality": "CT", "body_part": "Head", "ai_triage_score": 0.5, "upload_time": now},
        now=now
    )
    assert bd_half["ai_triage_score"] == 10.0

    _, _, bd_full = compute_priority(
        {"modality": "CT", "body_part": "Head", "ai_triage_score": 1.0, "upload_time": now},
        now=now
    )
    assert bd_full["ai_triage_score"] == 20.0


def test_waiting_time_starvation_prevention():
    """
    Verifies that waiting time increases priority score at 0.05 pts/min
    and caps at 20.0 points (~400 mins) to prevent Routine cases from starving.
    """
    now = datetime.now(timezone.utc)

    # 100 minutes waiting = 5.0 points
    t_100m_ago = now - timedelta(minutes=100)
    _, _, bd_100 = compute_priority(
        {"modality": "X-ray", "body_part": "Knee", "urgency_flag": 0, "upload_time": t_100m_ago},
        now=now
    )
    assert bd_100["waiting_score"] == 5.0
    assert bd_100["waiting_minutes"] == 100.0

    # 500 minutes waiting = capped at 20.0 points
    t_500m_ago = now - timedelta(minutes=500)
    _, _, bd_500 = compute_priority(
        {"modality": "X-ray", "body_part": "Knee", "urgency_flag": 0, "upload_time": t_500m_ago},
        now=now
    )
    assert bd_500["waiting_score"] == 20.0


def test_priority_bucket_thresholds():
    """Verifies classification into Critical (>=70), High (40-69), and Routine (<40)."""
    now = datetime.now(timezone.utc)

    # Critical: Urgency(40) + CT Head(20) + AI Triage(15) = 75 >= 70
    score, label, _ = compute_priority({
        "urgency_flag": 1,
        "modality": "CT",
        "body_part": "Head",
        "ai_triage_score": 0.75,
        "upload_time": now,
    }, now=now)
    assert score >= 70.0
    assert label == "Critical"

    # High: Urgency(0) + CT Head(20) + AI Triage(10) + Waiting 250m (12.5) = 42.5 (High)
    score, label, _ = compute_priority({
        "urgency_flag": 0,
        "modality": "CT",
        "body_part": "Head",
        "ai_triage_score": 0.5,
        "upload_time": now - timedelta(minutes=250),
    }, now=now)
    assert 40.0 <= score < 70.0
    assert label == "High"

    # Routine: Urgency(0) + X-ray Knee(5) + Waiting 0m = 5 (< 40)
    score, label, _ = compute_priority({
        "urgency_flag": 0,
        "modality": "X-ray",
        "body_part": "Knee",
        "upload_time": now,
    }, now=now)
    assert score < 40.0
    assert label == "Routine"


def test_explainability_breakdown_contains_all_keys():
    """Verifies that breakdown contains complete explainability metadata for the UI."""
    now = datetime.now(timezone.utc)
    _, _, breakdown = compute_priority({
        "case_id": "CASE-9999",
        "urgency_flag": 1,
        "modality": "CT",
        "body_part": "Head",
        "upload_time": now,
    }, now=now)

    expected_keys = {
        "case_id", "urgency_score", "urgency_flag", "modality_body_score",
        "modality", "body_part", "ai_triage_score", "ai_triage_confidence",
        "waiting_minutes", "waiting_score", "total_score", "priority_label", "explanation"
    }
    assert expected_keys.issubset(breakdown.keys())
    assert "Urgency (+40)" in breakdown["explanation"]
