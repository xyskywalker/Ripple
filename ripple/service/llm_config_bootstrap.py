from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

import yaml

REQUIRED_ENV_VARS = (
    "RIPPLE_LLM_MODEL_PLATFORM",
    "RIPPLE_LLM_MODEL_NAME",
    "RIPPLE_LLM_API_KEY",
)


def _clean(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key, default) or "").strip()


def _read_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _clean(env, key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be a float, got: {raw!r}") from exc


def _read_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _clean(env, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer, got: {raw!r}") from exc


def ensure_llm_config(
    config_path: str | Path = "/app/llm_config.yaml",
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = environ or os.environ
    path = Path(config_path)

    if path.exists():
        return False

    provided = [name for name in REQUIRED_ENV_VARS if _clean(env, name)]
    if not provided:
        # Startup without LLM env is valid: caller may pass llm_config in each API request.
        return False

    missing = [name for name in REQUIRED_ENV_VARS if not _clean(env, name)]
    if missing:
        raise RuntimeError(
            "llm_config.yaml is missing and startup LLM env is incomplete. "
            "Either set all required vars or set none and pass llm_config per request. "
            "Missing: "
            + ", ".join(missing)
        )

    default = {
        "model_platform": _clean(env, "RIPPLE_LLM_MODEL_PLATFORM"),
        "model_name": _clean(env, "RIPPLE_LLM_MODEL_NAME"),
        "api_key": _clean(env, "RIPPLE_LLM_API_KEY"),
        "temperature": _read_float(env, "RIPPLE_LLM_TEMPERATURE", 0.7),
        "max_retries": _read_int(env, "RIPPLE_LLM_MAX_RETRIES", 3),
    }

    url = _clean(env, "RIPPLE_LLM_URL")
    if url:
        default["url"] = url

    api_mode = _clean(env, "RIPPLE_LLM_API_MODE")
    if api_mode:
        default["api_mode"] = api_mode

    config = {"_default": default}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return True


def main() -> int:
    path = os.getenv("RIPPLE_LLM_CONFIG_PATH", "/app/llm_config.yaml")
    try:
        created = ensure_llm_config(path)
    except Exception as exc:
        print(f"[ripple-entrypoint] failed to bootstrap LLM config: {exc}", file=sys.stderr)
        return 1

    exists = Path(path).exists()
    if created:
        print(f"[ripple-entrypoint] generated LLM config at {path}")
    elif exists:
        print(f"[ripple-entrypoint] using existing LLM config at {path}")
    else:
        print(
            "[ripple-entrypoint] startup LLM config not provided; "
            "expecting llm_config in API requests"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
