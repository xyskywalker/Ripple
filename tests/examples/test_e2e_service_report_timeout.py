from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest


EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import e2e_simulation_xiaohongshu_service as service_example


class _FakeResponse:
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"report": "报告正文"}


@pytest.mark.asyncio
async def test_request_report_uses_explicit_extended_timeout(monkeypatch) -> None:
    captured: dict = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, *, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr(service_example.httpx, "AsyncClient", _FakeClient)

    report = await service_example._request_report(
        base_url="http://127.0.0.1:8080",
        api_token="",
        job_id="job_demo",
        rounds=[],
        report_timeout=321.0,
    )

    assert report == "报告正文"
    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 30.0
    assert timeout.read == 321.0
    assert timeout.write == 321.0
    assert timeout.pool == 321.0
