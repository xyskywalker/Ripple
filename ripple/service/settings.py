from __future__ import annotations

import os

from pydantic import BaseModel


class ServiceSettings(BaseModel):
    api_token: str = ""
    db_path: str = "data/ripple-service/ripple_service.db"
    output_dir: str = "/data/ripple_outputs"
    llm_config_path: str = "/app/llm_config.yaml"
    cancel_ttl_seconds: int = 60

    @classmethod
    def from_env(cls) -> "ServiceSettings":
        return cls(
            api_token=os.getenv("RIPPLE_API_TOKEN", ""),
            db_path=os.getenv("RIPPLE_DB_PATH", "data/ripple-service/ripple_service.db"),
            output_dir=os.getenv("RIPPLE_OUTPUT_DIR", "/data/ripple_outputs"),
            llm_config_path=os.getenv("RIPPLE_LLM_CONFIG_PATH", "/app/llm_config.yaml"),
            cancel_ttl_seconds=int(os.getenv("RIPPLE_CANCEL_TTL_SECONDS", "60")),
        )
