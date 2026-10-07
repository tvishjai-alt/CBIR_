"""
tests/test_routing.py
Unit tests for Intelligent Radiologist Routing and Workload Balancing Engine.
"""

import pytest
from database import init_db, get_db_connection, execute_write
from routing import assign_radiologist, rebalance, determine_target_subspecialty


@pytest.fixture
def clean_test_db(tmp_path):
    """Initializes a fresh isolated SQLite database with test clinics and radiologists."""
    db_file = tmp_path / "routing_test.db"
    init_db(reset=True, db_path=db_file)
    conn = get_db_connection(db_file)
    with conn:
        conn.execute("INSERT INTO clinics VALUES ('CLN-01', 'Test Clinic', 'Test City');")
        # Radiologists:
        # RAD-01: Neuro, cap 3, avg 15m
        # RAD-02: Neuro, cap 3, avg 10m
        # RAD-03: Chest, cap 3, avg 12m
        # RAD-04: General, cap 5, avg 14m
        conn.execute("INSERT INTO radiologists VALUES ('RAD-01', 'Dr. Neuro A', 'Neuro', 3, 1, 15.0);")
        conn.execute("INSERT INTO radiologists VALUES ('RAD-02', 'Dr. Neuro B', 'Neuro', 3, 1, 10.0);")
        conn.execute("INSERT INTO radiologists VALUES ('RAD-03', 'Dr. Chest A', 'Chest', 3, 1, 12.0);")
        conn.execute("INSERT INTO radiologists VALUES ('RAD-04', 'Dr. General A', 'General', 5, 1, 14.0);")
    conn.close()
    return db_file


def test_subspecialty_detection():
    """Verifies anatomical keyword to subspecialty mapping."""
    assert determine_target_subspecialty("Brain CT") == "Neuro"
    assert determine_target_subspecialty("Cervical Spine") == "Neuro"
    assert determine_target_subspecialty("Chest X-ray") == "Chest"
    assert determine_target_subspecialty("Right Knee") == "MSK"
    assert determine_target_subspecialty("Abdomen") == "General"


def test_specialist_matching_and_tiebreaking(clean_test_db):
    """
    Verifies that a Brain case matches Neuro specialists.
    Between RAD-01 and RAD-02 (both at 0 load), RAD-02 is chosen due to lower avg_report_minutes (10m vs 15m).
    """
    case = {"body_part": "Brain", "priority_label": "Routine"}
    rad_id, reason = assign_radiologist(case, db_path=clean_test_db)
    assert rad_id == "RAD-02"
    assert "Dr. Neuro B" in reason
    assert "avg 10.0m" in reason


def test_lowest_weighted_load_preference(clean_test_db):
    """
    If RAD-02 has an open Critical case (load 3) and RAD-01 has 0 load,
    next Neuro case routes to RAD-01 despite higher avg_report_minutes.
    """
    conn = get_db_connection(clean_test_db)
    with conn:
        conn.execute(
            """
            INSERT INTO cases (
                case_id, clinic_id, patient_ref, modality, body_part,
                urgency_flag, image_path, upload_time, status,
                priority_label, priority_score, assigned_rad_id
            ) VALUES (
                'CASE-EXISTING-1', 'CLN-01', 'PT-01', 'CT', 'Brain',
                1, 'static/test.jpg', '2026-10-01T00:00:00Z', 'Assigned',
                'Critical', 80.0, 'RAD-02'
            );
            """
        )
    conn.close()

    case = {"body_part": "Brain", "priority_label": "Routine"}
    rad_id, reason = assign_radiologist(case, db_path=clean_test_db)
    assert rad_id == "RAD-01"
    assert "Dr. Neuro A" in reason


def test_capacity_limit_and_fallback_to_general(clean_test_db):
    """
    When all matching specialists (RAD-01 & RAD-02) reach max_capacity (3 cases),
    routing should gracefully fall back to the active Generalist (RAD-04).
    """
    conn = get_db_connection(clean_test_db)
    with conn:
        for i in range(3):
            conn.execute(
                f"""
                INSERT INTO cases VALUES (
                    'C-N1-{i}', 'CLN-01', 'PT-01', 'CT', 'Brain',
                    0, 'img.jpg', '2026-10-01T00:00:00Z', 'Assigned',
                    'Routine', 20.0, 'RAD-01'
                );
                """
            )
            conn.execute(
                f"""
                INSERT INTO cases VALUES (
                    'C-N2-{i}', 'CLN-01', 'PT-01', 'CT', 'Brain',
                    0, 'img.jpg', '2026-10-01T00:00:00Z', 'Assigned',
                    'Routine', 20.0, 'RAD-02'
                );
                """
            )
    conn.close()

    case = {"body_part": "Brain", "priority_label": "High"}
    rad_id, reason = assign_radiologist(case, db_path=clean_test_db)
    # Should fall back to Dr. General A (RAD-04)
    assert rad_id == "RAD-04"
    assert "Fell back to Generalist Dr. General A" in reason


def test_rebalance_workload(clean_test_db):
    """
    Verifies that when RAD-04 is overloaded (e.g. 5 load points) and RAD-03 has 0 load,
    rebalance() transfers a Routine case to balance the queue.
    """
    conn = get_db_connection(clean_test_db)
    with conn:
        # Give RAD-04 five Routine cases (load 5)
        for i in range(5):
            conn.execute(
                f"""
                INSERT INTO cases VALUES (
                    'C-GEN-{i}', 'CLN-01', 'PT-01', 'X-ray', 'Chest',
                    0, 'img.jpg', '2026-10-01T00:00:00Z', 'Assigned',
                    'Routine', 20.0, 'RAD-04'
                );
                """
            )
    conn.close()

    # RAD-04 has load 5, RAD-03 (Chest specialist) has load 0. Disparity = 5 >= threshold 4.
    transfers = rebalance(db_path=clean_test_db)
    assert len(transfers) >= 1
    assert transfers[0]["from_rad"] == "RAD-04"
    assert transfers[0]["to_rad"] == "RAD-03"

    # Verify database state after rebalancing
    conn = get_db_connection(clean_test_db)
    transferred_case = conn.execute(
        "SELECT assigned_rad_id, status FROM cases WHERE case_id = ?;",
        (transfers[0]["case_id"],)
    ).fetchone()
    conn.close()

    assert transferred_case["assigned_rad_id"] == "RAD-03"
    assert transferred_case["status"] == "Assigned"
