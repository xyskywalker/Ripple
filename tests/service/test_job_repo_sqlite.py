from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ripple.service.job_repo_sqlite import JobRepoSQLite


def _columns(db_path: Path) -> set[str]:
    """读取 jobs 表列集合。 / Read the set of jobs-table columns."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("PRAGMA table_info(jobs)").fetchall()
    return {str(row[1]) for row in rows}


def test_init_schema_migrates_legacy_jobs_table(tmp_path: Path) -> None:
    """旧 jobs 表应被幂等扩展。 / Legacy jobs table should be migrated idempotently."""
    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE jobs(
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT
            )
            """
        )

    repo = JobRepoSQLite(db_path)
    repo.init_schema()

    columns = _columns(db_path)
    assert {
        "source",
        "created_at",
        "started_at",
        "completed_at",
        "updated_at",
        "heartbeat_at",
        "worker_pid",
        "cancel_requested",
        "phase",
        "progress",
        "current_wave",
        "total_waves",
        "status_snapshot_json",
        "job_brief",
        "job_brief_source",
    }.issubset(columns)


def test_create_job_persists_extended_metadata(tmp_path: Path) -> None:
    """创建任务时应写入扩展元数据。 / Job creation should persist extended metadata."""
    repo = JobRepoSQLite(tmp_path / "jobs.db")
    repo.init_schema()

    repo.create_job(
        "job_demo",
        {"event": {"title": "demo"}},
        source="cli",
        created_at="2026-03-14T10:00:00+00:00",
        job_brief="demo brief",
        job_brief_source="llm",
    )

    row = repo.get_job("job_demo")
    assert row["status"] == "queued"
    assert row["source"] == "cli"
    assert row["job_brief"] == "demo brief"
    assert row["job_brief_source"] == "llm"
    assert json.loads(row["request_json"]) == {"event": {"title": "demo"}}


def test_try_acquire_active_job_lock_rejects_live_job_and_recovers_stale_job(tmp_path: Path) -> None:
    """活动锁应拒绝活跃任务并回收陈旧任务。 / Active lock should reject live jobs and recover stale ones."""
    repo = JobRepoSQLite(tmp_path / "jobs.db")
    repo.init_schema()
    repo.create_job("job_live", {"event": {"title": "live"}}, source="cli")
    repo.create_job("job_next", {"event": {"title": "next"}}, source="cli")

    conflict = repo.try_acquire_active_job_lock(
        "job_live",
        now_iso="2026-03-14T10:00:00+00:00",
        worker_pid=101,
        stale_threshold_seconds=90,
    )
    assert conflict is None

    conflict = repo.try_acquire_active_job_lock(
        "job_next",
        now_iso="2026-03-14T10:00:30+00:00",
        worker_pid=202,
        stale_threshold_seconds=90,
    )
    assert conflict == "job_live"
    assert repo.get_job("job_next")["status"] == "queued"

    conflict = repo.try_acquire_active_job_lock(
        "job_next",
        now_iso="2026-03-14T10:02:10+00:00",
        worker_pid=202,
        stale_threshold_seconds=90,
    )
    assert conflict is None
    assert repo.get_job("job_live")["status"] == "failed"
    assert repo.get_job("job_next")["status"] == "running"


def test_update_runtime_snapshot_and_list_jobs(tmp_path: Path) -> None:
    """运行时快照与列表筛选应可用。 / Runtime snapshot and list filtering should work."""
    repo = JobRepoSQLite(tmp_path / "jobs.db")
    repo.init_schema()
    repo.create_job(
        "job_done",
        {"event": {"title": "done"}},
        source="cli",
        created_at="2026-03-14T10:00:00+00:00",
        job_brief="done brief",
    )
    repo.try_acquire_active_job_lock(
        "job_done",
        now_iso="2026-03-14T10:00:05+00:00",
        worker_pid=301,
        stale_threshold_seconds=90,
    )
    repo.update_runtime(
        "job_done",
        phase="RIPPLE",
        progress=0.45,
        current_wave=3,
        total_waves=8,
        heartbeat_at="2026-03-14T10:00:30+00:00",
        snapshot={
            "headline": "🌊 Wave 3/8 in progress",
            "phase_label": "Ripple propagation",
            "highlights": ["45% completed"],
            "recent_events": [{"emoji": "🌊", "text": "Wave 3 started"}],
        },
    )
    repo.set_result("job_done", {"output_file": "/tmp/out.json"})
    repo.update_status(
        "job_done",
        "completed",
        completed_at="2026-03-14T10:02:00+00:00",
    )

    row = repo.get_job("job_done")
    assert row["phase"] == "RIPPLE"
    assert row["progress"] == 0.45
    assert row["current_wave"] == 3
    assert row["total_waves"] == 8
    assert json.loads(row["status_snapshot_json"])["headline"] == "🌊 Wave 3/8 in progress"

    page = repo.list_jobs(status="completed", source="cli", limit=10, offset=0)
    assert page["total"] == 1
    assert page["jobs"][0]["job_id"] == "job_done"
    assert page["jobs"][0]["job_brief"] == "done brief"


def test_select_cleanup_jobs_and_delete_rows(tmp_path: Path) -> None:
    """清理候选与删除应受时间和状态控制。 / Cleanup selection and deletion should honor time and status."""
    repo = JobRepoSQLite(tmp_path / "jobs.db")
    repo.init_schema()

    repo.create_job(
        "job_old",
        {"event": {"title": "old"}},
        source="cli",
        created_at="2026-03-01T10:00:00+00:00",
    )
    repo.try_acquire_active_job_lock(
        "job_old",
        now_iso="2026-03-01T10:00:05+00:00",
        worker_pid=401,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_old", "completed", completed_at="2026-03-01T11:00:00+00:00")

    repo.create_job(
        "job_new",
        {"event": {"title": "new"}},
        source="cli",
        created_at="2026-03-14T10:00:00+00:00",
    )
    repo.try_acquire_active_job_lock(
        "job_new",
        now_iso="2026-03-14T10:00:05+00:00",
        worker_pid=402,
        stale_threshold_seconds=90,
    )
    repo.update_status("job_new", "completed", completed_at="2026-03-14T11:00:00+00:00")

    candidates = repo.select_jobs_for_cleanup(
        before_iso="2026-03-07T00:00:00+00:00",
        status="completed",
    )
    assert [row["job_id"] for row in candidates] == ["job_old"]

    repo.delete_jobs(["job_old"])
    assert repo.list_jobs(limit=10, offset=0)["total"] == 1

