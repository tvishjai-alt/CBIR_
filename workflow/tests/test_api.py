"""
tests/test_api.py
Integration tests for Flask API routes and Webhook Endpoints.
"""

import json
import pytest
from app import app
from database import get_db_connection, execute_write


@pytest.fixture
def client():
    """Provides a test client with testing mode enabled and cleans up created test cases."""
    app.config["TESTING"] = True
    test_cases_to_clean = ["CASE-WEBHOOK-99", "CASE-API-CYCLE-1"]
    
    with app.test_client() as client:
        yield client

    # Cleanup test-created records from main database
    conn = get_db_connection()
    with conn:
        for cid in test_cases_to_clean:
            conn.execute("DELETE FROM cases WHERE case_id = ?;", (cid,))
            conn.execute("DELETE FROM events WHERE case_id = ?;", (cid,))
            conn.execute("DELETE FROM assignments WHERE case_id = ?;", (cid,))
            conn.execute("DELETE FROM reports WHERE case_id = ?;", (cid,))
            conn.execute("DELETE FROM retrieval_log WHERE case_id = ?;", (cid,))
    conn.close()


def test_api_health(client):
    """Verifies GET /api/health returns 200 and healthy status."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "healthy"
    assert data["database_connected"] is True


def test_api_cases_new_webhook_success(client):
    """Verifies that Member 2's upload portal webhook creates, prioritizes, and routes a case."""
    payload = {
        "case_id": "CASE-WEBHOOK-99",
        "clinic_id": "CLN-001",
        "patient_ref": "PT-WH-99",
        "modality": "CT",
        "body_part": "Head",
        "urgency_flag": 1,
        "image_path": "static/images/CASE-0001.jpg",
    }
    resp = client.post("/api/cases/new", json=payload)
    assert resp.status_code == 201
    res_data = resp.get_json()
    assert res_data["status"] == "success"
    case = res_data["data"]
    assert case["case_id"] == "CASE-WEBHOOK-99"
    assert case["priority_label"] in ["High", "Critical"]
    assert case["status"] == "Assigned"
    assert case["assigned_rad_id"] is not None


def test_api_cases_new_validation_errors(client):
    """Verifies that invalid requests return 400 with helpful error messages."""
    # Missing required clinic_id
    resp = client.post("/api/cases/new", json={"modality": "CT", "body_part": "Chest"})
    assert resp.status_code == 400
    assert "clinic_id" in resp.get_json()["message"]

    # Non-existent clinic_id
    resp2 = client.post("/api/cases/new", json={
        "clinic_id": "CLN-DOES-NOT-EXIST",
        "modality": "CT",
        "body_part": "Head"
    })
    assert resp2.status_code == 400
    assert "does not exist" in resp2.get_json()["message"]


def test_api_stats(client):
    """Verifies GET /api/stats returns accurate KPI aggregates."""
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()["stats"]
    assert "total_cases" in data
    assert "pending_cases" in data
    assert "completed_cases" in data
    assert "avg_turnaround_minutes" in data
    assert "sla_breach_rate_pct" in data
    assert data["total_cases"] >= 100


def test_api_worklist(client):
    """Verifies GET /api/worklist/<rad_id> returns sorted worklist and SLA metrics."""
    resp = client.get("/api/worklist/RAD-001")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert "worklist" in data
    assert "radiologist" in data
    assert data["radiologist"]["rad_id"] == "RAD-001"

    # Non-existent radiologist returns 404
    resp_404 = client.get("/api/worklist/RAD-NONEXISTENT")
    assert resp_404.status_code == 404


def test_api_case_review_and_reporting_lifecycle(client):
    """
    Tests end-to-end API cycle:
    1. Ingest case via /api/cases/new
    2. Start review via POST /api/case/<id>/start
    3. Submit report via POST /api/case/<id>/report
    """
    # 1. Ingest case
    ingest_payload = {
        "case_id": "CASE-API-CYCLE-1",
        "clinic_id": "CLN-002",
        "patient_ref": "PT-CYCLE-1",
        "modality": "X-ray",
        "body_part": "Chest",
        "urgency_flag": 0,
    }
    resp1 = client.post("/api/cases/new", json=ingest_payload)
    assert resp1.status_code == 201
    assigned_rad = resp1.get_json()["data"]["assigned_rad_id"]

    # 2. Start review
    resp2 = client.post("/api/case/CASE-API-CYCLE-1/start", json={"rad_id": assigned_rad})
    assert resp2.status_code == 200
    assert resp2.get_json()["data"]["status"] == "In_Review"

    # 3. Validation failure: empty report text
    resp_empty = client.post("/api/case/CASE-API-CYCLE-1/report", json={"rad_id": assigned_rad, "report_text": ""})
    assert resp_empty.status_code == 400

    # 4. Valid submit report
    resp3 = client.post(
        "/api/case/CASE-API-CYCLE-1/report",
        json={
            "rad_id": assigned_rad,
            "report_text": "Normal cardio-thoracic ratio. Clear lung fields.",
            "similar_cases_viewed": 3,
            "used_similar_cases": 1,
        }
    )
    assert resp3.status_code == 200
    assert resp3.get_json()["data"]["status"] == "Reported"


def test_api_rebalance(client):
    """Verifies POST /api/rebalance triggers rebalancing endpoint safely."""
    resp = client.post("/api/rebalance")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert "transfers" in data
