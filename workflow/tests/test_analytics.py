"""
tests/test_analytics.py
Unit tests for SQL-based Analytics, Turnaround Calculation, SLA Breach Rates, and Queues.
"""

from datetime import datetime, timedelta, timezone
import pytest

from analytics import (
    get_turnaround_statistics,
    get_review_time_statistics,
    get_sla_performance,
    get_radiologist_workloads,
    get_daily_throughput,
    get_hourly_throughput,
    get_pending_reports_queue,
)
from database import get_db_connection


def test_turnaround_statistics():
    """Verifies that turnaround stats calculate mean, median, p90 overall and by priority."""
    res = get_turnaround_statistics()
    assert "overall" in res
    assert "by_priority" in res
    assert "by_radiologist" in res

    overall = res["overall"]
    assert overall["count"] >= 100
    assert overall["mean"] > 0
    assert overall["median"] > 0
    assert overall["p90"] >= overall["median"]

    by_p = res["by_priority"]
    assert "Critical" in by_p
    assert "High" in by_p
    assert "Routine" in by_p
    assert by_p["Critical"]["count"] > 0


def test_turnaround_date_range_filtering():
    """Verifies that date range filtering restricts records correctly."""
    now = datetime.now(timezone.utc)
    t_start = (now - timedelta(days=5)).isoformat()
    t_end = now.isoformat()

    res_filtered = get_turnaround_statistics(start_date=t_start, end_date=t_end)
    res_all = get_turnaround_statistics()

    assert res_filtered["overall"]["count"] <= res_all["overall"]["count"]


def test_review_time_statistics():
    """Verifies pure diagnostic review time calculation from reports."""
    res = get_review_time_statistics()
    assert "overall" in res
    assert "by_modality" in res
    assert "with_cbir" in res
    assert "without_cbir" in res

    overall = res["overall"]
    assert overall["count"] >= 100
    assert overall["mean"] > 0

    # Ensure modalities are tracked
    for mod in ["X-ray", "CT", "MRI"]:
        assert mod in res["by_modality"]


def test_sla_performance_and_at_risk_detection():
    """Verifies SLA breach calculation and detection of at-risk cases."""
    res = get_sla_performance()
    assert "total_completed" in res
    assert "overall_breach_rate_pct" in res
    assert "by_priority" in res
    assert "at_risk_cases" in res

    assert res["total_completed"] >= 100
    assert 0.0 <= res["overall_breach_rate_pct"] <= 100.0

    # Verify at-risk structure
    for c in res["at_risk_cases"]:
        assert "remaining_minutes" in c
        assert "sla_max" in c
        assert c["remaining_minutes"] <= (c["sla_max"] * 0.25)


def test_radiologist_workloads():
    """Verifies active case count vs capacity for all radiologists."""
    workloads = get_radiologist_workloads()
    assert len(workloads) == 6

    for w in workloads:
        assert "name" in w
        assert "subspecialty" in w
        assert "max_capacity" in w
        assert "active_cases" in w
        assert "completed_total" in w
        assert 0.0 <= w["utilization_pct"] <= 150.0


def test_throughput_trends():
    """Verifies daily and hourly throughput aggregations."""
    daily = get_daily_throughput()
    assert len(daily) > 0
    assert "date" in daily[0]
    assert "count" in daily[0]

    hourly = get_hourly_throughput()
    assert isinstance(hourly, list)


def test_pending_reports_queue():
    """Verifies prioritized pending queue retrieval and sorting."""
    queue = get_pending_reports_queue()
    assert len(queue) == 15  # From 15 open seeded cases

    # Check sorted by priority_score descending
    scores = [c["priority_score"] for c in queue]
    assert scores == sorted(scores, reverse=True)
