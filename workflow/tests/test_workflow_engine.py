"""
tests/test_workflow_engine.py
Unit tests for Central Transactional Workflow Orchestrator and State Machine.
"""

import pytest
from database import init_db, get_db_connection
from workflow_engine import (
    create_case,
    start_review,
    submit_report,
    return_to_clinic,
    validate_transition,
    IllegalStateTransitionError,
)


@pytest.fixture
def clean_engine_db(tmp_path):
    """Initializes an isolated test database with clinics and radiologists."""
    db_file = tmp_path / "engine_test.db"
    init_db(reset=True, db_path=db_file)
    conn = get_db_connection(db_file)
    with conn:
        conn.execute("INSERT INTO clinics VALUES ('CLN-ENG-1', 'Engine Test Clinic', 'Metro Hub');")
        conn.execute("INSERT INTO radiologists VALUES ('RAD-ENG-1', 'Dr. Engine Rad', 'Chest', 5, 1, 12.0);")
    conn.close()
    return db_file


def test_illegal_state_machine_transitions():
    """Verifies that unauthorized state transitions raise IllegalStateTransitionError."""
    # Cannot jump from Uploaded directly to Reported
    with pytest.raises(IllegalStateTransitionError):
        validate_transition("Uploaded", "Reported")

    # Cannot transition backward from Returned_To_Clinic
    with pytest.raises(IllegalStateTransitionError):
        validate_transition("Returned_To_Clinic", "Assigned")

    # Cannot skip In_Review
    with pytest.raises(IllegalStateTransitionError):
        validate_transition("Assigned", "Reported")


def test_complete_end_to_end_workflow(clean_engine_db):
    """
    Verifies full lifecycle of a case:
    create_case -> start_review -> submit_report -> return_to_clinic
    and confirms that all 7 audit events are created.
    """
    case_payload = {
        "case_id": "CASE-TEST-0001",
        "clinic_id": "CLN-ENG-1",
        "patient_ref": "PT-TEST-01",
        "modality": "X-ray",
        "body_part": "Chest",
        "urgency_flag": 1,
        "image_path": "static/images/CASE-TEST-0001.jpg",
    }

    # Step 1: Intake pipeline (Upload -> Store -> CBIR -> Priority -> Route)
    created = create_case(case_payload, db_path=clean_engine_db)
    assert created["case_id"] == "CASE-TEST-0001"
    assert created["status"] == "Assigned"
    assert created["assigned_rad_id"] == "RAD-ENG-1"
    assert created["priority_label"] == "High" or created["priority_label"] == "Critical"
    assert "cbir_top_k" in created

    # Step 2: Start Review
    review_res = start_review("CASE-TEST-0001", "RAD-ENG-1", db_path=clean_engine_db)
    assert review_res["status"] == "In_Review"

    # Step 3: Submit Diagnostic Report
    report_res = submit_report(
        case_id="CASE-TEST-0001",
        rad_id="RAD-ENG-1",
        text="Normal lungs, sharp costophrenic angles. No acute infiltrate.",
        similar_cases_viewed=4,
        used_similar_cases=1,
        db_path=clean_engine_db
    )
    assert report_res["status"] == "Reported"
    assert report_res["used_similar_cases"] == 1
    assert report_res["similar_cases_viewed"] == 4

    # Step 4: Return Case to Referring Clinic
    final_res = return_to_clinic("CASE-TEST-0001", db_path=clean_engine_db)
    assert final_res["status"] == "Returned_To_Clinic"

    # Step 5: Verify Database Records & Event Audit Trail
    conn = get_db_connection(clean_engine_db)
    try:
        # Check case status in cases table
        c_row = conn.execute("SELECT status, assigned_rad_id FROM cases WHERE case_id = ?;", ("CASE-TEST-0001",)).fetchone()
        assert c_row["status"] == "Returned_To_Clinic"

        # Check retrieval log
        ret_row = conn.execute("SELECT * FROM retrieval_log WHERE case_id = ?;", ("CASE-TEST-0001",)).fetchone()
        assert ret_row is not None
        assert ret_row["retrieval_ms"] > 0

        # Check report
        rep_row = conn.execute("SELECT * FROM reports WHERE case_id = ?;", ("CASE-TEST-0001",)).fetchone()
        assert rep_row is not None
        assert "Normal lungs" in rep_row["report_text"]

        # Check full event audit trail
        events = conn.execute(
            "SELECT status, actor FROM events WHERE case_id = ? ORDER BY timestamp ASC;",
            ("CASE-TEST-0001",)
        ).fetchall()

        expected_sequence = [
            "Uploaded",
            "Stored",
            "Retrieved",
            "Assigned",
            "In_Review",
            "Reported",
            "Returned_To_Clinic"
        ]
        assert [e["status"] for e in events] == expected_sequence
    finally:
        conn.close()
