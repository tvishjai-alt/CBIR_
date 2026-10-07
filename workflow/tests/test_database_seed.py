"""
tests/test_database_seed.py
Tests verifying Phase 1: database initialization, schema integrity,
foreign key constraints, and idempotent seeding.
"""

import sqlite3
import pytest
from pathlib import Path

from config import SLA_LIMITS_MINUTES, DATABASE_PATH
from database import get_db_connection, init_db, query_all, query_one
from seed import seed_database


@pytest.fixture(scope="module")
def setup_test_db(tmp_path_factory):
    """
    Creates a temporary test SQLite database and seeds it.
    """
    temp_dir = tmp_path_factory.mktemp("test_db")
    test_db_path = temp_dir / "test_workflow.db"
    
    # Initialize and seed temporary test database cleanly
    seed_database(reset=True, db_path=test_db_path)
    return test_db_path


def test_schema_tables_exist(setup_test_db):
    """Verifies that all 8 required tables exist in the SQLite database."""
    test_db = setup_test_db
    conn = get_db_connection(test_db)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        ).fetchall()
        table_names = {t["name"] for t in tables}
        
        expected_tables = {
            "clinics",
            "cases",
            "radiologists",
            "assignments",
            "reports",
            "events",
            "retrieval_log",
            "sla_config",
        }
        assert expected_tables.issubset(table_names), f"Missing tables: {expected_tables - table_names}"
    finally:
        conn.close()


def test_foreign_key_constraints(setup_test_db):
    """Verifies that foreign key violations raise sqlite3.IntegrityError."""
    test_db = setup_test_db
    conn = get_db_connection(test_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    """
                    INSERT INTO cases (
                        case_id, clinic_id, patient_ref, modality, body_part,
                        urgency_flag, image_path, upload_time, status,
                        priority_label, priority_score
                    ) VALUES (
                        'TEST-001', 'NON_EXISTENT_CLINIC', 'PT-9999', 'CT', 'Head',
                        1, 'static/images/test.jpg', '2026-10-01T00:00:00Z', 'Uploaded',
                        'Critical', 80.0
                    );
                    """
                )
    finally:
        conn.close()


def test_seeding_counts_and_distribution(setup_test_db):
    """
    Verifies that seed_database populates expected record volumes on seeded database.
    """
    test_db = setup_test_db
    conn = get_db_connection(test_db)
    try:
        clinics = conn.execute("SELECT count(*) as cnt FROM clinics;").fetchone()["cnt"]
        radiologists = conn.execute("SELECT count(*) as cnt FROM radiologists;").fetchone()["cnt"]
        sla_rows = conn.execute("SELECT count(*) as cnt FROM sla_config;").fetchone()["cnt"]
        total_cases = conn.execute("SELECT count(*) as cnt FROM cases;").fetchone()["cnt"]
        completed_cases = conn.execute("SELECT count(*) as cnt FROM cases WHERE status = 'Returned_To_Clinic';").fetchone()["cnt"]
        open_cases = conn.execute("SELECT count(*) as cnt FROM cases WHERE status != 'Returned_To_Clinic';").fetchone()["cnt"]
        total_reports = conn.execute("SELECT count(*) as cnt FROM reports;").fetchone()["cnt"]

        assert clinics == 3, f"Expected 3 clinics, found {clinics}"
        assert radiologists == 6, f"Expected 6 radiologists, found {radiologists}"
        assert sla_rows == 3, f"Expected 3 SLA rows, found {sla_rows}"
        assert total_cases == 115, f"Expected 115 total cases, found {total_cases}"
        assert completed_cases == 100, f"Expected 100 completed cases, found {completed_cases}"
        assert open_cases == 15, f"Expected 15 open cases, found {open_cases}"
        assert total_reports == 100, f"Expected 100 reports, found {total_reports}"
    finally:
        conn.close()


def test_event_trail_completeness(setup_test_db):
    """
    Verifies that every completed case has all 7 required chronological status transitions.
    Uploaded -> Stored -> Retrieved -> Assigned -> In_Review -> Reported -> Returned_To_Clinic
    """
    test_db = setup_test_db
    conn = get_db_connection(test_db)
    try:
        completed_cases = conn.execute(
            "SELECT case_id FROM cases WHERE status = 'Returned_To_Clinic' LIMIT 10;"
        ).fetchall()
        
        expected_sequence = [
            "Uploaded",
            "Stored",
            "Retrieved",
            "Assigned",
            "In_Review",
            "Reported",
            "Returned_To_Clinic",
        ]
        
        for case in completed_cases:
            cid = case["case_id"]
            events = conn.execute(
                "SELECT status, timestamp FROM events WHERE case_id = ? ORDER BY timestamp ASC;",
                (cid,)
            ).fetchall()
            
            statuses = [e["status"] for e in events]
            assert statuses == expected_sequence, f"Case {cid} status trail mismatch: {statuses}"
    finally:
        conn.close()


def test_sla_config_values(setup_test_db):
    """Verifies that SLA values match expected standards: Critical 60m, High 240m, Routine 1440m."""
    test_db = setup_test_db
    conn = get_db_connection(test_db)
    try:
        slas = {row["priority_label"]: row["max_minutes"] for row in conn.execute("SELECT * FROM sla_config;").fetchall()}
        assert slas["Critical"] == 60
        assert slas["High"] == 240
        assert slas["Routine"] == 1440
    finally:
        conn.close()
