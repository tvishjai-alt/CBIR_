"""
tests/test_pages.py
Tests verifying Flask HTML template rendering and error states for UI pages.
"""

import pytest
from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_role_selector_page_renders(client):
    """Verifies that the role selector landing page renders with radiologists and clinics."""
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Tele-Radiology System Portal" in html
    assert "Dr. Sarah Chen" in html
    assert "Metro Urgent Care Center" in html


def test_worklist_page_renders(client):
    """Verifies that radiologist worklist page renders for an existing radiologist."""
    resp = client.get("/worklist/RAD-001")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Dr. Sarah Chen" in html
    assert "Neuro Specialist" in html
    assert "worklist-table" in html


def test_worklist_page_404_for_invalid_rad(client):
    """Verifies that non-existent radiologist returns 404."""
    resp = client.get("/worklist/RAD-NONEXISTENT")
    assert resp.status_code == 404


def test_case_view_page_renders_with_cbir(client):
    """Verifies that case view page renders PACS viewer, metadata, and Top-10 CBIR results."""
    resp = client.get("/case/CASE-0001")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "CASE-0001" in html
    assert "PRIMARY STUDY SCAN" in html
    assert "Top-10 Similar Historical Cases" in html
    assert "Diagnostic Report Console" in html


def test_case_view_page_404_for_invalid_case(client):
    """Verifies that non-existent case ID returns 404."""
    resp = client.get("/case/CASE-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_hospital_dashboard_renders(client):
    """Verifies that /hospital renders with Chart.js canvas elements and KPI cards."""
    resp = client.get("/hospital")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Hospital Operational Analytics" in html
    assert "chart-workload" in html
    assert "chart-pending-priority" in html
    assert "chart-throughput" in html
    assert "chart-sla-breach" in html


def test_hospital_analytics_api(client):
    """Verifies GET /api/hospital/analytics returns complete data for dashboard charts."""
    resp = client.get("/api/hospital/analytics")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert "workloads" in data
    assert "sla" in data
    assert "turnaround" in data
    assert "throughput" in data
    assert "pending_priority" in data
    assert len(data["workloads"]) == 6


def test_clinic_status_page_renders(client):
    """Verifies that /clinic/<clinic_id> renders clinic info, KPI cards, and case table."""
    resp = client.get("/clinic/CLN-001")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Metro Urgent Care Center" in html
    assert "CLN-001" in html
    assert "Total Cases Uploaded" in html


def test_clinic_status_404_for_invalid_clinic(client):
    """Verifies that non-existent clinic returns 404."""
    resp = client.get("/clinic/CLN-NONEXISTENT")
    assert resp.status_code == 404


def test_case_info_api(client):
    """Verifies GET /api/case/<case_id>/info returns detailed case metadata."""
    resp = client.get("/api/case/CASE-0001/info")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["case"]["case_id"] == "CASE-0001"
