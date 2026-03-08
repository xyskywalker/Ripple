from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ripple.llm.router import ModelRouter

logger = logging.getLogger(__name__)


RoundSpec = Dict[str, str]


def _load_json(text: str | None) -> dict | None:
    if not text:
        return None
    data = json.loads(text)
    return data if isinstance(data, dict) else None


def load_job_request(row: dict) -> dict | None:
    return _load_json(row.get("request_json"))


def load_job_result(row: dict) -> dict | None:
    return _load_json(row.get("result_json"))


def extract_request_llm_config(request: dict | None) -> dict | None:
    if not isinstance(request, dict):
        return None
    llm_config = request.get("llm_config")
    return llm_config if isinstance(llm_config, dict) else None


def _require_file(path_value: Any, *, name: str) -> Path:
    path_str = str(path_value or "").strip()
    if not path_str:
        raise FileNotFoundError(f"{name} path is missing")

    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"{name} file not found: {path}")
    return path


def load_compact_log_text(result: Dict[str, Any]) -> str:
    path = _require_file(result.get("compact_log_file"), name="compact_log_file")
    return path.read_text(encoding="utf-8")


def load_output_json_document(result: Dict[str, Any]) -> Dict[str, Any]:
    path = _require_file(result.get("output_file"), name="output_file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("output_file JSON must be an object")
    return data


def _normalize_rounds(rounds: Iterable[dict]) -> List[RoundSpec]:
    normalized: List[RoundSpec] = []
    for index, item in enumerate(rounds, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"rounds[{index - 1}] must be an object")

        system_prompt = str(item.get("system_prompt") or "").strip()
        if not system_prompt:
            raise ValueError(f"rounds[{index - 1}].system_prompt is required")

        normalized.append(
            {
                "label": str(item.get("label") or f"round_{index}"),
                "system_prompt": system_prompt,
                "extra_user_context": str(item.get("extra_user_context") or ""),
            }
        )
    return normalized


def compress_waves_for_llm(waves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compressed: List[Dict[str, Any]] = []
    for wave in waves:
        entry: Dict[str, Any] = {
            "wave_number": wave.get("wave_number"),
            "terminated": wave.get("terminated", False),
        }
        verdict = wave.get("verdict") or {}
        entry["simulated_time"] = verdict.get("simulated_time_elapsed", "")
        entry["global_observation"] = verdict.get("global_observation", "")
        if verdict.get("termination_reason"):
            entry["termination_reason"] = verdict["termination_reason"]

        entry["activated_agents"] = [
            {
                "id": agent.get("agent_id", ""),
                "energy": agent.get("incoming_ripple_energy", 0),
                "reason": agent.get("activation_reason", ""),
            }
            for agent in (verdict.get("activated_agents") or [])
        ]

        skipped = verdict.get("skipped_agents") or []
        if skipped:
            entry["skipped_agents"] = [
                {
                    "id": skipped_agent.get("agent_id", ""),
                    "reason": skipped_agent.get("skip_reason", ""),
                }
                for skipped_agent in skipped
            ]

        entry["responses"] = {
            agent_id: {
                "type": response.get("response_type", "unknown"),
                "out_energy": response.get("outgoing_energy", 0),
            }
            for agent_id, response in (wave.get("agent_responses") or {}).items()
        }

        if wave.get("wave_number") == 0:
            pre_snapshot = wave.get("pre_snapshot") or {}
            entry["initial_state"] = {
                "star_count": len(pre_snapshot.get("stars", {})),
                "sea_count": len(pre_snapshot.get("seas", {})),
                "seed_energy": pre_snapshot.get("seed_energy", 0),
            }

        compressed.append(entry)
    return compressed


def load_simulation_log(result: Dict[str, Any]) -> str:
    compact_log = result.get("compact_log_file")
    if compact_log:
        compact_path = Path(str(compact_log))
        if compact_path.exists():
            return compact_path.read_text(encoding="utf-8")

    full_data: Dict[str, Any]
    output_file = result.get("output_file")
    if output_file:
        output_path = Path(str(output_file))
        if output_path.exists():
            loaded = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                full_data = loaded
            else:
                raise ValueError("output_file JSON must be an object")
        else:
            full_data = result
    else:
        full_data = result

    process = full_data.get("process") or {}
    compact = {
        "simulation_input": full_data.get("simulation_input"),
        "init": process.get("init"),
        "seed": process.get("seed"),
        "waves": compress_waves_for_llm(process.get("waves") or []),
        "observation": process.get("observation"),
        "prediction": full_data.get("prediction"),
        "timeline": full_data.get("timeline"),
        "bifurcation_points": full_data.get("bifurcation_points"),
        "agent_insights": full_data.get("agent_insights"),
        "total_waves": full_data.get("total_waves"),
    }
    if full_data.get("deliberation"):
        compact["deliberation"] = full_data["deliberation"]

    return json.dumps(compact, ensure_ascii=False, indent=1, default=str)


async def _call_llm(
    router: Any,
    *,
    role: str,
    system_prompt: str,
    user_message: str,
) -> str:
    if hasattr(router, "check_budget") and not router.check_budget(role):
        raise RuntimeError(f"LLM call budget exceeded for role={role}")
    if hasattr(router, "record_attempt"):
        router.record_attempt(role)
    adapter = router.get_model_backend(role)
    content = await adapter.call(system_prompt, user_message)
    if hasattr(router, "record_call"):
        router.record_call(role)
    return (content or "").strip()


async def generate_report_from_result(
    *,
    result: Dict[str, Any],
    rounds: List[dict],
    role: str = "omniscient",
    max_llm_calls: int = 10,
    config_file: Optional[str] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    normalized_rounds = _normalize_rounds(rounds)
    log_text = load_simulation_log(result)
    router = ModelRouter(
        llm_config=llm_config,
        max_llm_calls=max_llm_calls,
        config_file=config_file,
    )

    parts: List[str] = []
    for round_spec in normalized_rounds:
        user_message = log_text
        extra_user_context = round_spec["extra_user_context"]
        if extra_user_context:
            user_message += "\n\n" + extra_user_context
        try:
            text = await _call_llm(
                router,
                role=role,
                system_prompt=round_spec["system_prompt"],
                user_message=user_message,
            )
        except Exception as exc:
            logger.warning("report round failed: label=%s error=%s", round_spec["label"], exc)
            continue
        if text:
            parts.append(text)

    return "\n\n".join(parts) if parts else None
