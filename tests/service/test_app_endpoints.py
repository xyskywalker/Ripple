from fastapi.testclient import TestClient

from ripple.service.app import create_app


def test_ping_allows_request_without_authorization_when_api_token_is_unset(monkeypatch):
    monkeypatch.delenv("RIPPLE_API_TOKEN", raising=False)
    monkeypatch.setenv("RIPPLE_DB_PATH", ":memory:")

    client = TestClient(create_app())
    response = client.get("/v1/ping")

    assert response.status_code == 200
    assert response.json() == {"ok": "true"}


def test_ping_requires_authorization_when_api_token_is_configured(monkeypatch):
    monkeypatch.setenv("RIPPLE_API_TOKEN", "secret")
    monkeypatch.setenv("RIPPLE_DB_PATH", ":memory:")

    client = TestClient(create_app())
    response = client.get("/v1/ping")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"
