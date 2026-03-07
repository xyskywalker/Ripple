import pytest
from fastapi import HTTPException

from ripple.service.auth import require_bearer


def test_require_bearer_allows_request_when_api_token_is_unset(monkeypatch):
    monkeypatch.delenv("RIPPLE_API_TOKEN", raising=False)

    assert require_bearer(None) is None


def test_require_bearer_allows_request_when_api_token_is_blank(monkeypatch):
    monkeypatch.setenv("RIPPLE_API_TOKEN", "   ")

    assert require_bearer(None) is None


def test_require_bearer_rejects_missing_header_when_api_token_is_configured(monkeypatch):
    monkeypatch.setenv("RIPPLE_API_TOKEN", "secret")

    with pytest.raises(HTTPException) as excinfo:
        require_bearer(None)

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Missing bearer token"


def test_require_bearer_rejects_wrong_token_when_api_token_is_configured(monkeypatch):
    monkeypatch.setenv("RIPPLE_API_TOKEN", "secret")

    with pytest.raises(HTTPException) as excinfo:
        require_bearer("Bearer wrong")

    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Invalid bearer token"


def test_require_bearer_accepts_matching_token_when_api_token_is_configured(monkeypatch):
    monkeypatch.setenv("RIPPLE_API_TOKEN", "secret")

    assert require_bearer("Bearer secret") is None
