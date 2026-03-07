from __future__ import annotations

from fastapi import Header, HTTPException

from .settings import ServiceSettings


def require_bearer(authorization: str | None = Header(default=None)) -> None:
    expected = ServiceSettings.from_env().api_token.strip()
    if not expected:
        return

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
