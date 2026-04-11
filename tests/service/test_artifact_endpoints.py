from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ripple.service.app import create_app


class _FakeManager:
    def __init__(self, row: dict):
        self._row = row

    def get_job(self, job_id: str) -> dict:
        assert job_id == self._row["job_id"]
        return self._row


def test_get_compact_log_returns_markdown(monkeypatch, tmp_path: Path) -> None:
    compact_log = tmp_path / "demo.md"
    compact_log.write_text("# compact log", encoding="utf-8")

    row = {
        "job_id": "job_demo",
        "status": "completed",
        "request_json": json.dumps({}),
        "result_json": json.dumps({"compact_log_file": str(compact_log)}),
        "error_json": None,
    }
    monkeypatch.delenv("RIPPLE_API_TOKEN", raising=False)
    monkeypatch.setenv("RIPPLE_DB_PATH", ":memory:")
    monkeypatch.setattr("ripple.service.app.JobManager", lambda db_path, output_dir=None: _FakeManager(row))

    client = TestClient(create_app())
    response = client.get("/v1/simulations/job_demo/artifacts/compact-log")

    assert response.status_code == 200
    assert response.text == "# compact log"
    assert response.headers["content-type"].startswith("text/plain")


def test_get_output_json_returns_json_document(monkeypatch, tmp_path: Path) -> None:
    output_json = tmp_path / "demo.json"
    output_json.write_text(json.dumps({"prediction": {"impact": "ok"}}), encoding="utf-8")

    row = {
        "job_id": "job_demo",
        "status": "completed",
        "request_json": json.dumps({}),
        "result_json": json.dumps({"output_file": str(output_json)}),
        "error_json": None,
    }
    monkeypatch.delenv("RIPPLE_API_TOKEN", raising=False)
    monkeypatch.setenv("RIPPLE_DB_PATH", ":memory:")
    monkeypatch.setattr("ripple.service.app.JobManager", lambda db_path, output_dir=None: _FakeManager(row))

    client = TestClient(create_app())
    response = client.get("/v1/simulations/job_demo/artifacts/output-json")

    assert response.status_code == 200
    assert response.json() == {"prediction": {"impact": "ok"}}


def test_generate_report_endpoint_returns_service_report(monkeypatch) -> None:
    request_llm_config = {
        "_default": {
            "model_platform": "openai",
            "model_name": "demo-model",
            "api_key": "sk-demo",
            "url": "https://example.test/v1",
        }
    }
    row = {
        "job_id": "job_demo",
        "status": "completed",
        "request_json": json.dumps({"llm_config": request_llm_config}),
        "result_json": json.dumps({"compact_log_file": "/data/ripple_outputs/demo.md"}),
        "error_json": None,
    }

    async def fake_generate_report_from_result(*, result, rounds, role, max_llm_calls, config_file, llm_config=None, stream=None, llm_timeout=None):
        assert result["compact_log_file"] == "/data/ripple_outputs/demo.md"
        assert rounds == [{"label": "r1", "system_prompt": "sys", "extra_user_context": ""}]
        assert role == "omniscient"
        assert max_llm_calls == 12
        assert config_file == "/app/llm_config.yaml"
        assert llm_config == request_llm_config
        return "报告正文"

    monkeypatch.delenv("RIPPLE_API_TOKEN", raising=False)
    monkeypatch.setenv("RIPPLE_DB_PATH", ":memory:")
    monkeypatch.setattr("ripple.service.app.JobManager", lambda db_path, output_dir=None: _FakeManager(row))
    monkeypatch.setattr("ripple.service.app.generate_report_from_result", fake_generate_report_from_result)

    client = TestClient(create_app())
    response = client.post(
        "/v1/simulations/job_demo/report",
        json={
            "rounds": [{"label": "r1", "system_prompt": "sys", "extra_user_context": ""}],
            "role": "omniscient",
            "max_llm_calls": 12,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"job_id": "job_demo", "report": "报告正文"}
