from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .security import token_digest


ACTIVE_JOB_STATUSES = ("queued", "running", "cancelling")
TERMINAL_JOB_STATUSES = ("success", "failed", "cancelled", "rejected")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


class RemoteStore:
    def __init__(self, database_file: Path):
        self.database_file = Path(database_file)
        self.database_file.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_file,
            timeout=10,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    csrf_token TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    remote_address TEXT,
                    user_agent TEXT
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    remote_address TEXT,
                    idempotency_key TEXT,
                    parameters_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    exit_code INTEGER,
                    message TEXT NOT NULL DEFAULT '',
                    failure_category TEXT NOT NULL DEFAULT '',
                    log_file TEXT NOT NULL DEFAULT '',
                    pid INTEGER,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS jobs_created_at_idx
                    ON jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS jobs_status_idx
                    ON jobs(status);
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_idx
                    ON jobs(idempotency_key)
                    WHERE idempotency_key IS NOT NULL;

                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    username TEXT NOT NULL,
                    remote_address TEXT,
                    action TEXT NOT NULL,
                    object_id TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS audit_occurred_at_idx
                    ON audit_events(occurred_at DESC);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "failure_category" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN failure_category TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_session(
        self,
        token: str,
        username: str,
        csrf_token: str,
        lifetime_hours: int,
        remote_address: str,
        user_agent: str,
    ) -> None:
        created = utc_now()
        expires = created + timedelta(hours=lifetime_hours)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    token_hash, username, csrf_token, created_at, expires_at,
                    remote_address, user_agent
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_digest(token),
                    username,
                    csrf_token,
                    created.isoformat(timespec="seconds"),
                    expires.isoformat(timespec="seconds"),
                    remote_address,
                    user_agent[:500],
                ),
            )

    def get_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        now_text = iso_now()
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (now_text,)
            )
            row = connection.execute(
                "SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?",
                (token_digest(token), now_text),
            ).fetchone()
        return self._row(row)

    def delete_session(self, token: str) -> None:
        if not token:
            return
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (token_digest(token),),
            )

    def active_job(self) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in ACTIVE_JOB_STATUSES)
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM jobs
                WHERE status IN ({placeholders})
                ORDER BY created_at ASC LIMIT 1
                """,
                ACTIVE_JOB_STATUSES,
            ).fetchone()
        return self._row(row)

    def recover_interrupted_jobs(self) -> int:
        placeholders = ",".join("?" for _ in ACTIVE_JOB_STATUSES)
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE jobs
                SET status='failed', finished_at=?, exit_code=125,
                    message='Remote service restarted before the job completed.',
                    failure_category='service_restart',
                    pid=NULL
                WHERE status IN ({placeholders})
                """,
                (iso_now(), *ACTIVE_JOB_STATUSES),
            )
        return cursor.rowcount

    def recent_duplicate(self, kind: str, seconds: int) -> dict[str, Any] | None:
        threshold = (utc_now() - timedelta(seconds=seconds)).isoformat(
            timespec="seconds"
        )
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE kind = ? AND created_at >= ?
                  AND status IN ('queued','running','cancelling','success')
                ORDER BY created_at DESC LIMIT 1
                """,
                (kind, threshold),
            ).fetchone()
        return self._row(row)

    def job_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return self._row(row)

    def create_job(
        self,
        kind: str,
        requested_by: str,
        remote_address: str,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        created = iso_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, kind, status, requested_by, remote_address,
                    idempotency_key, parameters_json, created_at
                ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    requested_by,
                    remote_address,
                    idempotency_key,
                    json.dumps(parameters or {}, ensure_ascii=False),
                    created,
                ),
            )
        return self.get_job(job_id) or {}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return self._row(row)

    def list_jobs(self, limit: int = 30) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def set_job_running(self, job_id: str, pid: int, log_file: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status='running', started_at=?, pid=?, log_file=?, message=?
                WHERE id=? AND status='queued'
                """,
                (iso_now(), pid, log_file, "Automation process is running.", job_id),
            )

    def finish_job(
        self,
        job_id: str,
        status: str,
        message: str,
        exit_code: int | None,
        failure_category: str = "",
    ) -> None:
        if status not in TERMINAL_JOB_STATUSES:
            raise ValueError(f"Invalid terminal job status: {status}")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status=?, finished_at=?, exit_code=?, message=?,
                    failure_category=?, pid=NULL
                WHERE id=?
                """,
                (status, iso_now(), exit_code, message, failure_category, job_id),
            )

    def request_cancel(self, job_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET cancel_requested=1,
                    status=CASE WHEN status='running' THEN 'cancelling' ELSE status END,
                    message='Cancellation requested.'
                WHERE id=? AND status IN ('queued','running','cancelling')
                """,
                (job_id,),
            )
        return cursor.rowcount > 0

    def cancel_requested(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return bool(job and job.get("cancel_requested"))

    def add_audit(
        self,
        username: str,
        remote_address: str,
        action: str,
        object_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (
                    occurred_at, username, remote_address, action,
                    object_id, details_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    iso_now(),
                    username,
                    remote_address,
                    action,
                    object_id,
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_events
                ORDER BY occurred_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
