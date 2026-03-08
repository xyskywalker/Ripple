from __future__ import annotations

import sys
from pathlib import Path

import pytest


EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import e2e_simulation_xiaohongshu_service as service_example


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


@pytest.mark.asyncio
async def test_fetch_service_artifacts_uses_http_endpoints() -> None:
    captured_urls: list[str] = []

    class _FakeClient:
        async def get(self, url: str, *, headers=None):
            captured_urls.append(url)
            if url.endswith("/artifacts/output-json"):
                return _FakeResponse(json_data={"run_id": "run_demo", "prediction": {"impact": "ok"}})
            if url.endswith("/artifacts/compact-log"):
                return _FakeResponse(text="# compact via api")
            raise AssertionError(f"unexpected url: {url}")

    artifacts = await service_example._fetch_service_artifacts(
        _FakeClient(),
        base_url="http://127.0.0.1:8080",
        headers={"Accept": "application/json"},
        job_id="job_demo",
    )

    assert captured_urls == [
        "http://127.0.0.1:8080/v1/simulations/job_demo/artifacts/output-json",
        "http://127.0.0.1:8080/v1/simulations/job_demo/artifacts/compact-log",
    ]
    assert artifacts["output_json"] == {"run_id": "run_demo", "prediction": {"impact": "ok"}}
    assert artifacts["compact_log_text"] == "# compact via api"


@pytest.mark.asyncio
async def test_run_mode_prints_compact_log_text_from_api_result(capsys) -> None:
    async def fake_run():
        return {
            "service_job_id": "job_demo",
            "run_id": "run_demo",
            "total_waves": 2,
            "wave_records_count": 2,
            "compact_log_text": "# compact via api",
            "artifact_urls": {
                "output_json": "http://127.0.0.1:8080/v1/simulations/job_demo/artifacts/output-json",
                "compact_log": "http://127.0.0.1:8080/v1/simulations/job_demo/artifacts/compact-log",
            },
        }

    await service_example._run_mode(
        label="基础模拟",
        run_coro=fake_run(),
        report_rounds=[],
        base_url="http://127.0.0.1:8080",
        api_token="",
        report_max_llm_calls=10,
        report_timeout=10.0,
        no_report=True,
    )

    out = capsys.readouterr().out
    assert "精简日志" in out
    assert "# compact via api" in out
