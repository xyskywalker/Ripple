from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


ALLOWED_TRANSITIONS = {
    "queued": {"running", "failed", "cancelled"},
    "running": {"completed", "failed", "cancel_pending", "cancelled"},
    "cancel_pending": {"cancelling", "running"},
    "cancelling": {"cancelled", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


class JobRepoSQLite:
    """SQLite 作业仓储。 / SQLite-backed job repository.

    这里同时服务 HTTP 与 CLI，接口尽量保持向后兼容。
    / This serves both HTTP and CLI while staying backward compatible.
    """

    _SCHEMA_COLUMNS = {
        "source": "TEXT DEFAULT 'http'",
        "created_at": "TEXT",
        "started_at": "TEXT",
        "completed_at": "TEXT",
        "updated_at": "TEXT",
        "heartbeat_at": "TEXT",
        "worker_pid": "INTEGER",
        "cancel_requested": "INTEGER DEFAULT 0",
        "phase": "TEXT",
        "progress": "REAL DEFAULT 0.0",
        "current_wave": "INTEGER",
        "total_waves": "INTEGER",
        "status_snapshot_json": "TEXT",
        "job_brief": "TEXT",
        "job_brief_source": "TEXT DEFAULT 'fallback'",
    }
    _ACTIVE_STATUSES = ("running", "cancel_pending", "cancelling")

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _utcnow_iso() -> str:
        """当前 UTC 时间 ISO 字符串。 / Current UTC time in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_iso(value: str | None) -> Optional[datetime]:
        """解析 ISO 时间。 / Parse an ISO timestamp."""
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | tuple | None) -> dict | None:
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return dict(row)
        return dict(row)

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
            existing = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            for name, definition in self._SCHEMA_COLUMNS.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

    def create_job(
        self,
        job_id: str,
        request: dict,
        *,
        source: str = "http",
        status: str = "queued",
        created_at: str | None = None,
        job_brief: str | None = None,
        job_brief_source: str = "fallback",
    ) -> None:
        now_iso = created_at or self._utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs(
                    job_id,
                    status,
                    request_json,
                    result_json,
                    error_json,
                    source,
                    created_at,
                    updated_at,
                    job_brief,
                    job_brief_source
                )
                VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    status,
                    json.dumps(request, ensure_ascii=False),
                    source,
                    now_iso,
                    now_iso,
                    job_brief,
                    job_brief_source,
                ),
            )

    def get_job(self, job_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if not row:
            raise KeyError(job_id)
        return dict(row)

    def update_job_fields(self, job_id: str, **fields: Any) -> None:
        """通用字段更新。 / Generic field update helper."""
        cleaned = {key: value for key, value in fields.items() if value is not None}
        if not cleaned:
            return
        cleaned.setdefault("updated_at", self._utcnow_iso())
        columns = ", ".join(f"{name} = ?" for name in cleaned)
        values = list(cleaned.values()) + [job_id]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {columns} WHERE job_id = ?",
                values,
            )

    def update_status(
        self,
        job_id: str,
        status: str,
        *,
        completed_at: str | None = None,
    ) -> None:
        current = self.get_job(job_id)["status"]
        if current != status and status not in ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid transition: {current}->{status}")
        update_fields: dict[str, Any] = {"status": status}
        if status == "running" and not self.get_job(job_id).get("started_at"):
            update_fields["started_at"] = self._utcnow_iso()
        if status in {"completed", "failed", "cancelled"}:
            update_fields["completed_at"] = completed_at or self._utcnow_iso()
        self.update_job_fields(job_id, **update_fields)

    def set_result(self, job_id: str, result: dict) -> None:
        self.update_job_fields(
            job_id,
            result_json=json.dumps(result, ensure_ascii=False),
        )

    def set_error(self, job_id: str, error: dict) -> None:
        self.update_job_fields(
            job_id,
            error_json=json.dumps(error, ensure_ascii=False),
        )

    def update_runtime(
        self,
        job_id: str,
        *,
        phase: str | None = None,
        progress: float | None = None,
        current_wave: int | None = None,
        total_waves: int | None = None,
        heartbeat_at: str | None = None,
        snapshot: dict | None = None,
    ) -> None:
        """更新运行态字段。 / Update runtime fields."""
        snapshot_json = None
        if snapshot is not None:
            snapshot_json = json.dumps(snapshot, ensure_ascii=False)
        self.update_job_fields(
            job_id,
            phase=phase,
            progress=progress,
            current_wave=current_wave,
            total_waves=total_waves,
            heartbeat_at=heartbeat_at or self._utcnow_iso(),
            status_snapshot_json=snapshot_json,
        )

    def request_cancel(self, job_id: str) -> None:
        """标记取消请求。 / Mark a job as cancel-requested."""
        self.update_job_fields(job_id, cancel_requested=1)

    def try_acquire_active_job_lock(
        self,
        job_id: str,
        *,
        now_iso: str | None = None,
        worker_pid: int | None = None,
        stale_threshold_seconds: int = 90,
    ) -> Optional[str]:
        """尝试获得活动任务锁，失败时返回冲突任务 ID。
        / Try to acquire the active-job lock, returning the conflicting job ID on failure.
        """
        now_text = now_iso or self._utcnow_iso()
        now_dt = self._parse_iso(now_text) or datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"""
                SELECT *
                FROM jobs
                WHERE status IN ({",".join("?" for _ in self._ACTIVE_STATUSES)})
                  AND job_id != ?
                ORDER BY COALESCE(updated_at, created_at, heartbeat_at) DESC, job_id DESC
                LIMIT 1
                """,
                (*self._ACTIVE_STATUSES, job_id),
            ).fetchone()
            active = self._row_to_dict(row)
            if active is not None:
                stamp = (
                    active.get("heartbeat_at")
                    or active.get("updated_at")
                    or active.get("created_at")
                )
                active_dt = self._parse_iso(str(stamp) if stamp else "")
                is_stale = False
                if active_dt is not None:
                    is_stale = (now_dt - active_dt).total_seconds() > stale_threshold_seconds
                if not is_stale:
                    conn.rollback()
                    return str(active["job_id"])

                stale_error = json.dumps(
                    {
                        "code": "STALE_HEARTBEAT",
                        "message": f"Recovered stale job {active['job_id']}",
                    },
                    ensure_ascii=False,
                )
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed',
                        error_json = ?,
                        completed_at = ?,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (stale_error, now_text, now_text, active["job_id"]),
                )
            conn.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?,
                    heartbeat_at = ?,
                    worker_pid = ?,
                    cancel_requested = 0
                WHERE job_id = ?
                """,
                (now_text, now_text, now_text, worker_pid, job_id),
            )
            conn.commit()
        return None

    def list_jobs(
        self,
        *,
        status: str | None = None,
        source: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """列出任务。 / List jobs with optional filters."""
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if source:
            where.append("source = ?")
            params.append(source)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        limit = max(0, int(limit))
        offset = max(0, int(offset))

        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM jobs {where_sql}",
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT *
                FROM jobs
                {where_sql}
                ORDER BY COALESCE(created_at, updated_at, completed_at, heartbeat_at) DESC, job_id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return {
            "jobs": [dict(row) for row in rows],
            "total": int(total),
        }

    def select_jobs_for_cleanup(
        self,
        *,
        before_iso: str | None = None,
        status: str | None = None,
        include_all: bool = False,
    ) -> list[dict]:
        """选择可清理任务。 / Select cleanup candidates."""
        where: list[str] = ["status NOT IN (?, ?, ?)"]
        params: list[Any] = list(self._ACTIVE_STATUSES)
        if status:
            where.append("status = ?")
            params.append(status)
        elif not include_all:
            where.append("status IN ('completed', 'failed', 'cancelled')")
        if before_iso:
            where.append("COALESCE(completed_at, created_at) < ?")
            params.append(before_iso)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM jobs
                WHERE {" AND ".join(where)}
                ORDER BY COALESCE(completed_at, created_at) ASC, job_id ASC
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_jobs(self, job_ids: Iterable[str]) -> None:
        """删除任务记录。 / Delete job rows."""
        ids = [str(job_id) for job_id in job_ids]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(
                f"DELETE FROM jobs WHERE job_id IN ({placeholders})",
                ids,
            )
