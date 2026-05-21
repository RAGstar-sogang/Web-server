import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "diagnosis.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS diagnoses (
    diagnosis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_log TEXT NOT NULL,
    metadata_json TEXT,
    source TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    oom_type TEXT,
    constraint_type TEXT,
    confidence REAL,
    root_cause TEXT,
    action_guide_json TEXT,
    intermediate_results_json TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_status ON diagnoses(status, created_at);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)


def create_diagnosis(raw_log: str, metadata: Optional[dict], source: Optional[str]) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO diagnoses (raw_log, metadata_json, source) VALUES (?, ?, ?)",
            (raw_log, json.dumps(metadata) if metadata else None, source),
        )
        return cur.lastrowid


def get_diagnosis(diagnosis_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM diagnoses WHERE diagnosis_id = ?", (diagnosis_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def claim_next_pending() -> Optional[dict]:
    """Atomically pick the oldest pending task and flip it to running."""
    with _connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM diagnoses WHERE status = 'pending' "
                "ORDER BY created_at, diagnosis_id LIMIT 1"
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE diagnoses SET status='running', updated_at=CURRENT_TIMESTAMP "
                "WHERE diagnosis_id = ?",
                (row["diagnosis_id"],),
            )
            conn.execute("COMMIT")
            return _row_to_dict(row)
        except Exception:
            conn.execute("ROLLBACK")
            raise


def update_status(diagnosis_id: int, status: str, error: Optional[str] = None) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE diagnoses SET status=?, "
            "error_message=COALESCE(?, error_message), "
            "updated_at=CURRENT_TIMESTAMP "
            "WHERE diagnosis_id = ?",
            (status, error, diagnosis_id),
        )
        return cur.rowcount > 0


def save_result(diagnosis_id: int, result: dict, intermediate: Optional[dict]) -> bool:
    confidence = result.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None

    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE diagnoses SET
                status='success',
                oom_type=?,
                constraint_type=?,
                confidence=?,
                root_cause=?,
                action_guide_json=?,
                intermediate_results_json=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE diagnosis_id = ?
            """,
            (
                result.get("oom_type"),
                result.get("constraint_type"),
                confidence,
                result.get("root_cause"),
                json.dumps(result.get("action_guide", [])),
                json.dumps(intermediate or {}),
                diagnosis_id,
            ),
        )
        return cur.rowcount > 0


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["metadata"] = json.loads(d.pop("metadata_json")) if d.get("metadata_json") else None
    d["action_guide"] = json.loads(d.pop("action_guide_json")) if d.get("action_guide_json") else []
    d["intermediate_results"] = (
        json.loads(d.pop("intermediate_results_json"))
        if d.get("intermediate_results_json")
        else {}
    )
    return d
