from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ALLOWED_TRANSITIONS = {
    "queued": {"running"},
    "running": {"completed", "failed", "cancel_pending", "cancelled"},
    "cancel_pending": {"cancelling", "running"},
    "cancelling": {"cancelled", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class JobRepoSQLite:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs(
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT
                )
                """
            )

    def create_job(self, job_id: str, request: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(job_id, status, request_json, result_json, error_json)
                VALUES (?, 'queued', ?, NULL, NULL)
                """,
                (job_id, json.dumps(request, ensure_ascii=False)),
            )

    def get_job(self, job_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT job_id, status, request_json, result_json, error_json
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if not row:
            raise KeyError(job_id)
        return {
            "job_id": row[0],
            "status": row[1],
            "request_json": row[2],
            "result_json": row[3],
            "error_json": row[4],
        }

    def update_status(self, job_id: str, status: str) -> None:
        current = self.get_job(job_id)["status"]
        if status not in ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid transition: {current}->{status}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ? WHERE job_id = ?",
                (status, job_id),
            )

    def set_result(self, job_id: str, result: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET result_json = ? WHERE job_id = ?",
                (json.dumps(result, ensure_ascii=False), job_id),
            )

    def set_error(self, job_id: str, error: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET error_json = ? WHERE job_id = ?",
                (json.dumps(error, ensure_ascii=False), job_id),
            )
