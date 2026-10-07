"""
database.py
Database connection and execution helpers using standard Python sqlite3.
Enforces foreign keys, row dict factories, and transactional execution.
"""

import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from config import DATABASE_PATH, BASE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("database")


def get_db_connection(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """
    Creates and returns a SQLite database connection with row factory
    and enforced foreign key constraints.
    """
    target_path = Path(db_path) if db_path else DATABASE_PATH
    conn = sqlite3.connect(target_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    # Enable foreign keys in SQLite for referential integrity
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_db_transaction(db_path: Optional[Union[str, Path]] = None):
    """
    Context manager for atomic database transactions.
    Automatically commits on success or rolls back on exception.
    """
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error(f"Transaction failed and was rolled back: {exc}")
        raise exc
    finally:
        conn.close()


def init_db(reset: bool = False, db_path: Optional[Union[str, Path]] = None) -> None:
    """
    Initializes the database schema using schema.sql.
    If reset=True, removes the target database file if it exists and rebuilds fresh.
    """
    target_path = Path(db_path) if db_path else DATABASE_PATH
    schema_file = BASE_DIR / "schema.sql"

    if reset and target_path.exists():
        logger.warning(f"Resetting database at {target_path}...")
        try:
            target_path.unlink()
            logger.info("Existing database file removed.")
        except Exception as e:
            logger.error(f"Could not remove database file directly: {e}. Dropping tables instead.")
            # Fallback if file is locked
            conn = get_db_connection(target_path)
            with conn:
                conn.execute("PRAGMA foreign_keys = OFF;")
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
                for table in tables:
                    name = table["name"]
                    if not name.startswith("sqlite_"):
                        conn.execute(f"DROP TABLE IF EXISTS {name};")
                conn.execute("PRAGMA foreign_keys = ON;")
            conn.close()

    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found at {schema_file}")

    with open(schema_file, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_db_connection(target_path)
    try:
        with conn:
            conn.executescript(schema_sql)
        logger.info(f"Database successfully initialized at {target_path}")
    finally:
        conn.close()


def query_all(
    sql: str,
    params: Sequence[Any] = (),
    db_path: Optional[Union[str, Path]] = None
) -> List[sqlite3.Row]:
    """
    Executes a SELECT query and returns all matching rows.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        return rows
    finally:
        conn.close()


def query_one(
    sql: str,
    params: Sequence[Any] = (),
    db_path: Optional[Union[str, Path]] = None
) -> Optional[sqlite3.Row]:
    """
    Executes a SELECT query and returns the first row or None.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return row
    finally:
        conn.close()


def execute_write(
    sql: str,
    params: Sequence[Any] = (),
    db_path: Optional[Union[str, Path]] = None
) -> int:
    """
    Executes an INSERT, UPDATE, or DELETE query and returns the rowcount.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            return cursor.rowcount
    finally:
        conn.close()


def execute_many(
    sql: str,
    seq_of_params: Sequence[Sequence[Any]],
    db_path: Optional[Union[str, Path]] = None
) -> int:
    """
    Executes a batch parameterized query and returns total affected rows.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.executemany(sql, seq_of_params)
            return cursor.rowcount
    finally:
        conn.close()
