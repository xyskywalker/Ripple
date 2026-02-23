# recorder.py
# =============================================================================
# 模拟过程增量记录器 — 在模拟每个关键节点动态写入 JSON 文件。
# / Incremental simulation recorder — writes JSON at each key checkpoint.
#
# 设计目标 / Design goals:
# 1. 细粒度记录：初始化、种子、每轮 wave、裁决、响应、观测、合成。
#    / Fine-grained recording: init, seed, per-wave snapshots, verdicts, responses, observation, synthesis.
# 2. 动态写入：关键节点后立即刷盘，不等模拟结束。
#    / Eager flush: write to disk at each checkpoint, don't wait for completion.
# 3. 崩溃安全：临时文件 + 原子重命名，任意时刻文件都是合法 JSON。
#    / Crash-safe: temp file + atomic rename; file is always valid JSON.
# 4. 向后兼容：合成结果保持顶层键，过程数据放在 process 键下。
#    / Backward compat: synthesis at top-level keys; process data under "process".
# =============================================================================

"""模拟过程增量记录器。 / Incremental simulation recorder."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ripple.primitives.models import OmniscientVerdict

logger = logging.getLogger(__name__)


class SimulationRecorder:
    """模拟过程增量记录器。 / Incremental simulation recorder.

    在模拟的每个关键节点动态写入 JSON 文件。确保文件在任意时刻都是合法 JSON，
    即使模拟中途失败也能保留已完成阶段的完整记录。
    / Writes JSON at each key checkpoint. File is always valid JSON,
    preserving completed phases even if simulation fails midway.

    输出 JSON 结构 / Output JSON structure:
        {
            "meta": { run_id, engine_version, start_time, end_time, status, ... },
            "simulation_input": { ... },
            "process": {
                "init": { star_configs, sea_configs, dynamic_parameters, ... },
                "seed": { content, energy },
                "waves": [
                    {
                        "wave_number": 0,
                        "pre_snapshot": { ... },     // 场状态(wave前) / field state (pre-wave)
                        "verdict": { ... },          // 全视者裁决 / Omniscient verdict
                        "agent_responses": { ... },  // Agent 响应 / agent responses
                        "post_snapshot": { ... },    // 场状态(wave后) / field state (post-wave)
                    },
                    ...
                ],
                "observation": { ... },
            },
            // 向后兼容顶层键（合成阶段填充） / Backward-compat top-level keys (filled at synthesis)
            "prediction": { ... },
            "timeline": [ ... ],
            "bifurcation_points": [ ... ],
            "agent_insights": { ... },
            "total_waves": N,
            "run_id": "...",
            "wave_records_count": N,
        }
    """

    def __init__(self, output_path: Path, run_id: str):
        """初始化记录器，立即创建输出文件。 / Initialize recorder and create output file immediately.

        Args:
            output_path: JSON 输出文件路径。 / JSON output file path.
            run_id: 本次模拟的唯一标识。 / Unique identifier for this simulation run.
        """
        self._path = output_path
        self._run_id = run_id
        self._start_time = time.monotonic()
        self._start_datetime = datetime.now()
        self._active_ensemble_run: Optional[Dict[str, Any]] = None
        self._active_ensemble_run_start: Optional[float] = None

        # 核心数据结构 — 与输出 JSON 一一对应 / Core data structure — mirrors output JSON
        self._data: Dict[str, Any] = {
            "meta": {
                "run_id": run_id,
                "engine_version": "0.2.0",
                "start_time": self._start_datetime.isoformat(),
                "end_time": None,
                "elapsed_seconds": 0.0,
                "status": "running",
            },
            "simulation_input": None,
            "process": {
                "init": None,
                "seed": None,
                "waves": [],
                # PMF v4: tribunal deliberation records (JSON Pointer: #/process/deliberation)
                "deliberation": None,
                "observation": None,
                # Ensemble runs boundary recording (P1)
                "ensemble_runs": [],
            },
            # 向后兼容的顶层键（合成阶段填充） / Backward-compat top-level keys (filled at synthesis)
            "total_waves": 0,
            "run_id": run_id,
            "wave_records_count": 0,
        }
        # 创建文件，标记模拟已启动 / Create file, mark simulation as started
        self._flush()

    # -----------------------------------------------------------------
    # Ensemble run boundaries / 集成运行边界
    # -----------------------------------------------------------------

    def begin_ensemble_run(
        self,
        *,
        run_index: int,
        run_id: str,
        random_seed: Optional[int],
    ) -> None:
        """Begin a new ensemble run section inside this output file.

        When active, all subsequent record_* calls will write to this run's
        process/result instead of the top-level process keys.
        """
        run_entry: Dict[str, Any] = {
            "run_index": int(run_index),
            "run_id": str(run_id),
            "random_seed": int(random_seed) if random_seed is not None else None,
            "meta": {
                "start_time": datetime.now().isoformat(),
                "end_time": None,
                "elapsed_seconds": 0.0,
                "status": "running",
                "error": None,
            },
            "process": {
                "init": None,
                "seed": None,
                "waves": [],
                "deliberation": None,
                "observation": None,
            },
            "result": None,
            "total_waves": 0,
            "wave_records_count": 0,
        }
        self._data["process"]["ensemble_runs"].append(run_entry)
        self._active_ensemble_run = run_entry
        self._active_ensemble_run_start = time.monotonic()
        self._flush()

    def end_ensemble_run(self, *, error: Optional[str] = None) -> None:
        """End the current ensemble run section."""
        if self._active_ensemble_run is None:
            return
        elapsed = 0.0
        if self._active_ensemble_run_start is not None:
            elapsed = time.monotonic() - self._active_ensemble_run_start
        self._active_ensemble_run["meta"]["end_time"] = datetime.now().isoformat()
        self._active_ensemble_run["meta"]["elapsed_seconds"] = round(elapsed, 2)
        if error:
            self._active_ensemble_run["meta"]["status"] = "failed"
            self._active_ensemble_run["meta"]["error"] = str(error)
        else:
            self._active_ensemble_run["meta"]["status"] = "completed"
        self._active_ensemble_run = None
        self._active_ensemble_run_start = None
        self._flush()

    def _process_root(self) -> Dict[str, Any]:
        """Return the process dict to write into (active run or top-level)."""
        if self._active_ensemble_run is not None:
            return self._active_ensemble_run["process"]
        return self._data["process"]

    @property
    def active_ensemble_run_index(self) -> Optional[int]:
        """Current active ensemble run index, if any."""
        if self._active_ensemble_run is None:
            return None
        try:
            return int(self._active_ensemble_run.get("run_index"))
        except (TypeError, ValueError):
            return None

    # -----------------------------------------------------------------
    # 公共记录接口 — 各阶段调用入口 / Public recording API — entry points per phase
    # -----------------------------------------------------------------

    def record_simulation_input(
        self, simulation_input: Dict[str, Any],
    ) -> None:
        """记录模拟输入参数（供复现追溯）。 / Record simulation input (for reproducibility)."""
        self._data["simulation_input"] = simulation_input
        self._flush()

    def record_init(
        self,
        init_result: Dict[str, Any],
        estimated_waves: int,
        max_waves: int,
    ) -> None:
        """记录 INIT 阶段结果 — 全视者初始化输出。 / Record INIT phase — Omniscient initialization output.

        包含 Agent 配置、动态参数、种子涟漪、预估 wave 数。
        / Contains agent configs, dynamic params, seed ripple, estimated waves.
        """
        root = self._process_root()
        root["init"] = {
            "timestamp": datetime.now().isoformat(),
            "star_configs": init_result.get("star_configs", []),
            "sea_configs": init_result.get("sea_configs", []),
            "dynamic_parameters": init_result.get("dynamic_parameters", {}),
            "estimated_waves": estimated_waves,
            "max_waves": max_waves,
            "seed_ripple_raw": init_result.get("seed_ripple", {}),
        }
        self._flush()

    def record_seed(
        self, seed_content: str, seed_energy: float,
    ) -> None:
        """记录 SEED 阶段结果 — 种子涟漪注入。 / Record SEED phase — seed ripple injection."""
        root = self._process_root()
        root["seed"] = {
            "timestamp": datetime.now().isoformat(),
            "content": seed_content,
            "energy": seed_energy,
        }
        self._flush()

    def record_wave_start(
        self, wave_number: int, pre_snapshot: Dict[str, Any],
    ) -> None:
        """记录 wave 启动前的场快照。 / Record field snapshot before wave starts.

        在全视者发出裁决之前调用，捕获场的当前状态作为 pre_snapshot。
        / Called before Omniscient verdict; captures current field state as pre_snapshot.
        """
        wave_entry: Dict[str, Any] = {
            "wave_number": wave_number,
            "timestamp_start": datetime.now().isoformat(),
            "timestamp_end": None,
            "pre_snapshot": pre_snapshot,
            "verdict": None,
            "agent_responses": None,
            "post_snapshot": None,
            "terminated": False,
        }
        root = self._process_root()
        root["waves"].append(wave_entry)
        if self._active_ensemble_run is not None:
            self._active_ensemble_run["wave_records_count"] = len(root["waves"])
        else:
            self._data["wave_records_count"] = len(root["waves"])
        self._flush()

    def record_wave_end(
        self,
        wave_number: int,
        verdict: OmniscientVerdict,
        agent_responses: Dict[str, Dict[str, Any]],
        post_snapshot: Dict[str, Any],
        terminated: bool = False,
    ) -> None:
        """记录 wave 完成后的完整数据。 / Record full data after wave completion.

        包含全视者裁决、各 Agent 响应、wave 结束后的场快照。
        terminated=True 表示全视者判定传播终止。
        / Contains Omniscient verdict, agent responses, post-wave snapshot.
        terminated=True means Omniscient decided to stop propagation.
        """
        # 查找对应的 wave 条目（由 record_wave_start 创建） / Find matching wave entry (created by record_wave_start)
        root = self._process_root()
        wave_entry = self._find_wave_entry(wave_number, waves=root["waves"])
        if wave_entry is None:
            # 容错：如果没有对应的 start 记录，创建新条目 / Fault-tolerant: create entry if no matching start record
            logger.warning(
                f"Wave {wave_number} 缺少 start 记录，创建补充条目"
            )
            wave_entry = {
                "wave_number": wave_number,
                "timestamp_start": datetime.now().isoformat(),
                "pre_snapshot": None,
            }
            root["waves"].append(wave_entry)

        wave_entry["timestamp_end"] = datetime.now().isoformat()
        wave_entry["verdict"] = self._serialize_verdict(verdict)
        wave_entry["agent_responses"] = agent_responses
        wave_entry["post_snapshot"] = post_snapshot
        wave_entry["terminated"] = terminated

        if self._active_ensemble_run is not None:
            self._active_ensemble_run["total_waves"] = len(root["waves"])
            self._active_ensemble_run["wave_records_count"] = len(root["waves"])
        else:
            self._data["total_waves"] = len(root["waves"])
            self._data["wave_records_count"] = len(root["waves"])
        self._flush()

    def record_observation(self, observation: str) -> None:
        """记录 OBSERVE 阶段结果 — 全视者的全局观测。 / Record OBSERVE phase — Omniscient's global observation."""
        root = self._process_root()
        root["observation"] = {
            "timestamp": datetime.now().isoformat(),
            "content": observation,
        }
        self._flush()

    def record_process(self, key: str, data: Any) -> None:
        """记录任意 process.* 节点（用于可选 phase 扩展）。 / Record an arbitrary process.* node.

        存储语义：process[key] 直接等于 data（不额外包一层 data/timestamp）。
        / Storage semantics: process[key] equals data directly (no extra wrapper).

        这样 JSON Pointer（如 #/process/deliberation）始终指向实际数据结构，
        避免消费者在不同 process.* 键之间需要额外解引用层级。
        / This keeps JSON Pointer references (e.g. #/process/deliberation) pointing to the actual data.
        """
        if not key or not isinstance(key, str):
            raise ValueError("process key must be a non-empty string")
        root = self._process_root()
        root[key] = data
        self._flush()

    def record_synthesis(self, result: Dict[str, Any]) -> None:
        """记录 SYNTHESIZE 阶段 — 将合成结果写入顶层键（向后兼容）。 / Record SYNTHESIZE — write result to top-level keys (backward compat).

        prediction/timeline/bifurcation_points/agent_insights 等字段直接放入顶层。
        / Fields like prediction/timeline/bifurcation_points/agent_insights go to top level.
        """
        if self._active_ensemble_run is not None:
            # In ensemble mode, store per-run synthesis under the run entry
            self._active_ensemble_run["result"] = result
        else:
            # 向后兼容：将合成结果的各字段提升到顶层 / Backward compat: hoist synthesis fields to top level
            for key, value in result.items():
                if key not in ("meta", "process", "simulation_input"):
                    self._data[key] = value
        self._flush()

    def finalize(self, total_waves: int) -> None:
        """标记模拟完成，写入最终元信息。 / Mark simulation complete and write final metadata."""
        elapsed = time.monotonic() - self._start_time
        self._data["meta"]["end_time"] = datetime.now().isoformat()
        self._data["meta"]["elapsed_seconds"] = round(elapsed, 2)
        self._data["meta"]["status"] = "completed"
        self._data["total_waves"] = total_waves
        self._flush()
        logger.info(
            f"模拟记录已完成: {self._path} "
            f"({total_waves} waves, {elapsed:.1f}s)"
        )

    def mark_failed(self, error: str) -> None:
        """标记模拟失败，记录错误信息。 / Mark simulation failed and record error info."""
        elapsed = time.monotonic() - self._start_time
        self._data["meta"]["end_time"] = datetime.now().isoformat()
        self._data["meta"]["elapsed_seconds"] = round(elapsed, 2)
        self._data["meta"]["status"] = "failed"
        self._data["meta"]["error"] = error
        self._flush()

    # -----------------------------------------------------------------
    # 属性访问 / Property access
    # -----------------------------------------------------------------

    @property
    def output_path(self) -> Path:
        """输出文件路径。 / Output file path."""
        return self._path

    @property
    def data(self) -> Dict[str, Any]:
        """当前记录数据的完整快照（只读引用）。 / Full snapshot of current data (read-only reference)."""
        return self._data

    @property
    def compact_log_path(self) -> Path:
        """压缩 Markdown 日志路径（与 JSON 同目录，.md 后缀）。 / Compact markdown log path (.md alongside .json)."""
        return self._path.with_suffix(".md")

    # -----------------------------------------------------------------
    # 内部方法 / Internal methods
    # -----------------------------------------------------------------

    def _find_wave_entry(
        self,
        wave_number: int,
        *,
        waves: Optional[list] = None,
    ) -> Optional[Dict[str, Any]]:
        """根据 wave_number 查找已有的 wave 条目。 / Find existing wave entry by wave_number."""
        wave_list = waves if waves is not None else self._data["process"]["waves"]
        for w in wave_list:
            if w["wave_number"] == wave_number:
                return w
        return None

    def _flush(self) -> None:
        """将当前状态写入 JSON 文件。 / Flush current state to JSON file.

        使用「先写临时文件 -> 原子重命名」模式确保文件完整性。写入失败仅记录日志。
        / Uses temp file + atomic rename for file integrity. Write failures only logged.
        """
        try:
            # 更新运行时长（仅在 running 状态下） / Update elapsed time (only while running)
            if self._data["meta"]["status"] == "running":
                elapsed = time.monotonic() - self._start_time
                self._data["meta"]["elapsed_seconds"] = round(elapsed, 2)

            content = json.dumps(
                self._data,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

            # 原子写入：先写 .tmp 再重命名，避免崩溃导致文件损坏 / Atomic write: .tmp then rename to prevent corruption
            tmp_path = self._path.with_suffix(".json.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            # 设置文件权限为 0o600（仅所有者读写） / Set file permissions to 0o600 (owner read/write only)
            os.chmod(tmp_path, 0o600)
            tmp_path.replace(self._path)
        except Exception as e:
            logger.warning(f"记录器写入失败（不影响模拟流程）: {e}")
        # 同步写入压缩 Markdown 日志 / Sync write compact markdown log
        self._flush_markdown()

    @staticmethod
    def _serialize_verdict(
        verdict: OmniscientVerdict,
    ) -> Dict[str, Any]:
        """将 OmniscientVerdict 数据模型序列化为可 JSON 化的字典。 / Serialize OmniscientVerdict to JSON-compatible dict."""
        return {
            "wave_number": verdict.wave_number,
            "simulated_time_elapsed": verdict.simulated_time_elapsed,
            "simulated_time_remaining": verdict.simulated_time_remaining,
            "continue_propagation": verdict.continue_propagation,
            "activated_agents": [
                {
                    "agent_id": a.agent_id,
                    "incoming_ripple_energy": a.incoming_ripple_energy,
                    "activation_reason": a.activation_reason,
                }
                for a in verdict.activated_agents
            ],
            "skipped_agents": [
                {
                    "agent_id": s.agent_id,
                    "skip_reason": s.skip_reason,
                }
                for s in verdict.skipped_agents
            ],
            "global_observation": verdict.global_observation,
            "termination_reason": verdict.termination_reason,
        }

    # -----------------------------------------------------------------
    # Compact Markdown log — 极限压缩日志，token 友好
    # / Ultra-compact markdown log, token-friendly for LLM consumption
    # -----------------------------------------------------------------

    def _flush_markdown(self) -> None:
        """将当前状态写入压缩 Markdown 日志文件。 / Flush compact markdown log file."""
        try:
            md = self._build_compact_markdown()
            tmp = self.compact_log_path.with_suffix(".md.tmp")
            tmp.write_text(md, encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(self.compact_log_path)
        except Exception as e:
            logger.warning(f"Markdown 日志写入失败: {e}")

    def _build_compact_markdown(self) -> str:
        """构建极限压缩的 Markdown 日志。

        设计原则 / Design principles:
        - 关键信息零丢失：裁决、激活、响应、观测、合成全部保留
        - 格式极简：无 JSON 结构开销，key=value 紧凑表示
        - 冗余数据丢弃：pre/post_snapshot、时间戳、详细 Agent 画像
        - 适合直接喂给 LLM 生成解读报告
        """
        L: list = []
        meta = self._data.get("meta") or {}

        # === Header ===
        L.append(f"# Ripple {meta.get('run_id', '')} {meta.get('status', '')}")
        L.append(
            f"v{meta.get('engine_version', '')} "
            f"{meta.get('start_time', '')} "
            f"{meta.get('elapsed_seconds', 0)}s"
        )
        L.append("")

        # === Input ===
        si = self._data.get("simulation_input")
        if si:
            L.append("## INPUT")
            event = si.get("event") or {}
            title = event.get("title") or event.get("summary") or ""
            if title:
                L.append(title)
            desc = event.get("description") or ""
            if desc and desc != title:
                L.append(desc[:300])
            tags = []
            for k in ("skill", "platform", "channel", "vertical", "simulation_horizon"):
                v = si.get(k)
                if v:
                    tags.append(f"{k}={v}")
            if tags:
                L.append(" ".join(tags))
            src = si.get("source") or {}
            if isinstance(src, dict) and src.get("summary"):
                L.append(f"src: {src['summary'][:200]}")
            hist = si.get("historical")
            if isinstance(hist, list) and hist:
                L.append(f"hist: {len(hist)} records")
            L.append("")

        # === Process data ===
        process = self._data.get("process") or {}
        ensemble_runs = process.get("ensemble_runs") or []
        has_ensemble = bool(ensemble_runs) and any(
            r.get("process") for r in ensemble_runs
        )

        if has_ensemble:
            for run_entry in ensemble_runs:
                idx = run_entry.get("run_index", "?")
                rid = run_entry.get("run_id", "")
                seed_val = run_entry.get("random_seed", "")
                st = (run_entry.get("meta") or {}).get("status", "")
                L.append(f"## RUN {idx} {rid} seed={seed_val} {st}")
                self._md_process(run_entry.get("process") or {}, L)
                res = run_entry.get("result")
                if res:
                    self._md_synthesis(res, L)
                L.append("")

            # Ensemble stats
            es = self._data.get("ensemble_stats")
            if es:
                L.append("## ENSEMBLE")
                L.append(
                    f"runs={es.get('runs_completed', 0)}/{es.get('runs_requested', 0)} "
                    f"failed={es.get('runs_failed', 0)}"
                )
                grades = es.get("grade_sequence") or []
                if grades:
                    agree = es.get("grade_agreement_rate", 0)
                    L.append(
                        f"grades: {','.join(str(g) for g in grades)} "
                        f"mode={es.get('grade_mode')} agree={agree:.0%}"
                    )
                kappa = es.get("dimension_agreement_kappa")
                if kappa is not None:
                    L.append(f"kappa={kappa:.3f} ({es.get('dimension_agreement_level', '')})")
                agg = es.get("dimension_aggregates") or {}
                if agg:
                    parts = []
                    for dim, vals in agg.items():
                        if isinstance(vals, dict):
                            parts.append(f"{dim}={vals.get('median', '?')}")
                        else:
                            parts.append(f"{dim}={vals}")
                    L.append("scores: " + " ".join(parts))
                L.append("")
        else:
            self._md_process(process, L)

        # Top-level synthesis & total_waves
        tw = self._data.get("total_waves")
        if tw:
            L.append(f"total_waves={tw}")
        self._md_synthesis(self._data, L)

        # Safety: ensure all items are strings (LLM output may inject dicts)
        return "\n".join(str(x) for x in L)

    def _md_process(self, process: Dict[str, Any], L: list) -> None:
        """构建 INIT/SEED/WAVES/OBSERVE/DELIBERATION 段落。"""
        init = process.get("init")
        if init:
            L.append("### INIT")
            stars = init.get("star_configs") or []
            if stars:
                items = []
                for s in stars:
                    sid = s.get("id") or s.get("agent_id") or "?"
                    desc = s.get("description") or s.get("name") or ""
                    items.append(f"{sid}({desc[:50]})" if desc else sid)
                L.append(f"Stars({len(stars)}): {', '.join(items)}")
            seas = init.get("sea_configs") or []
            if seas:
                items = []
                for s in seas:
                    sid = s.get("id") or s.get("agent_id") or "?"
                    desc = s.get("description") or s.get("name") or ""
                    items.append(f"{sid}({desc[:50]})" if desc else sid)
                L.append(f"Seas({len(seas)}): {', '.join(items)}")
            params = init.get("dynamic_parameters") or {}
            if params:
                L.append("Params: " + " ".join(f"{k}={v}" for k, v in params.items()))
            L.append(
                f"Est: {init.get('estimated_waves', '?')}/{init.get('max_waves', '?')} waves"
            )
            seed_raw = init.get("seed_ripple_raw") or {}
            if seed_raw:
                content = (seed_raw.get("content") or "")[:120]
                energy = seed_raw.get("initial_energy", "?")
                L.append(f"SeedDraft: E={energy} {content}")
            L.append("")

        seed = process.get("seed")
        if seed:
            L.append("### SEED")
            L.append(f"E={seed.get('energy', '?')} {(seed.get('content') or '')[:150]}")
            L.append("")

        waves = process.get("waves") or []
        if waves:
            L.append(f"### WAVES ({len(waves)})")
            for w in waves:
                wn = w.get("wave_number", "?")
                terminated = w.get("terminated", False)
                verdict = w.get("verdict") or {}

                time_el = verdict.get("simulated_time_elapsed", "")
                hdr = f"W{wn}"
                if time_el:
                    hdr += f" T={time_el}"
                if terminated:
                    reason = verdict.get("termination_reason") or ""
                    hdr += " STOP"
                    if reason:
                        hdr += f": {reason}"
                L.append(hdr)

                obs = verdict.get("global_observation", "")
                if obs:
                    L.append(f"  obs: {obs}")

                for a in verdict.get("activated_agents") or []:
                    aid = a.get("agent_id", "?")
                    energy = a.get("incoming_ripple_energy", 0)
                    reason = a.get("activation_reason", "")
                    L.append(f"  +{aid} E={energy} {reason}")

                for s in verdict.get("skipped_agents") or []:
                    sid = s.get("agent_id", "?")
                    reason = s.get("skip_reason", "")
                    L.append(f"  -{sid} {reason}")

                for aid, r in (w.get("agent_responses") or {}).items():
                    rtype = r.get("response_type", "?")
                    out_e = r.get("outgoing_energy", 0)
                    comment = (r.get("comment") or "")[:80]
                    line = f"  >{aid} {rtype} E={out_e}"
                    if comment:
                        line += f" {comment}"
                    L.append(line)

            L.append("")

        observation = process.get("observation")
        if observation:
            L.append("### OBSERVE")
            if isinstance(observation, dict):
                L.append(observation.get("content", ""))
            else:
                L.append(str(observation))
            L.append("")

        delib = process.get("deliberation")
        if delib:
            L.append("### DELIBERATION")
            self._md_deliberation(delib, L)
            L.append("")

    def _md_deliberation(self, delib: Any, L: list) -> None:
        """构建合议庭审议段落。"""
        if not isinstance(delib, dict):
            L.append(str(delib)[:300])
            return

        summary = delib.get("deliberation_summary") or {}
        if summary:
            L.append(
                f"rounds={summary.get('rounds_executed', '?')} "
                f"converged={summary.get('converged', '?')}"
            )
            for pos in summary.get("final_positions") or []:
                role = pos.get("member_role", "?")
                scores = pos.get("scores") or {}
                sc = " ".join(f"{k}={v}" for k, v in scores.items())
                L.append(f"  {role}: {sc}")
            for cp in summary.get("consensus_points") or []:
                L.append(f"  +consensus: {str(cp)[:120]}")
            for dp in summary.get("dissent_points") or []:
                L.append(f"  -dissent: {str(dp)[:120]}")

        records = delib.get("deliberation_records") or []
        for rec in records:
            rnd = rec.get("round_number", "?")
            conv = rec.get("converged", False)
            L.append(f"  R{rnd} converged={conv}")
            for op in rec.get("opinions") or []:
                role = op.get("member_role", "?")
                scores = op.get("scores") or {}
                sc = " ".join(f"{k}={v}" for k, v in scores.items())
                rationale = (op.get("rationale") or "")[:120]
                L.append(f"    {role}: {sc} | {rationale}")

    @staticmethod
    def _format_agent_insights(insights: Dict[str, Any], L: list) -> None:
        """Format agent_insights supporting both flat and nested (stars/seas) schemas."""
        # Detect nested schema: {"stars": {id: {...}}, "seas": {id: {...}}}
        nested_keys = {"stars", "seas"}
        if nested_keys & set(insights.keys()):
            for group_key in ("stars", "seas"):
                group = insights.get(group_key)
                if not group or not isinstance(group, dict):
                    continue
                for aid, info in group.items():
                    if isinstance(info, dict):
                        # Map varied field names to display
                        desc = (
                            info.get("insight", "")
                            or info.get("role", "")
                            or info.get("core_motivation", "")
                        )
                        risk = info.get("risk", "")
                        move = (
                            info.get("recommended_move", "")
                            or info.get("best_leverage", "")
                            or info.get("best_message", "")
                        )
                        L.append(f"{aid}: {desc}")
                        if risk:
                            L.append(f"  risk: {risk}")
                        if move:
                            L.append(f"  move: {move}")
                    else:
                        L.append(f"{aid}: {str(info)[:200]}")
        else:
            # Flat schema: {agent_id: {insight, risk, recommended_move}}
            for aid, info in insights.items():
                if isinstance(info, dict):
                    insight = info.get("insight", "")
                    risk = info.get("risk", "")
                    move = info.get("recommended_move", "")
                    L.append(f"{aid}: {insight}")
                    if risk:
                        L.append(f"  risk: {risk}")
                    if move:
                        L.append(f"  move: {move}")
                else:
                    L.append(f"{aid}: {str(info)[:200]}")

    def _md_synthesis(self, data: Dict[str, Any], L: list) -> None:
        """构建合成结果段落（prediction/timeline/bifurcation/insights）。"""
        prediction = data.get("prediction")
        if prediction:
            L.append("### PREDICTION")
            if isinstance(prediction, dict):
                impact = prediction.get("impact", "")
                if impact:
                    L.append(str(impact)[:500])
                reach = prediction.get("reach_estimate") or {}
                if reach:
                    level = reach.get("relative_level", "")
                    drivers = reach.get("drivers") or []
                    constraints = reach.get("constraints") or []
                    parts = [f"reach={level}"]
                    if drivers:
                        parts.append(
                            f"drivers={'|'.join(str(d)[:60] for d in drivers[:4])}"
                        )
                    if constraints:
                        parts.append(
                            f"constraints={'|'.join(str(c)[:60] for c in constraints[:4])}"
                        )
                    L.append(" ".join(parts))
                verdict_text = prediction.get("verdict", "")
                if verdict_text:
                    L.append(str(verdict_text)[:500])
            else:
                L.append(str(prediction)[:500])
            L.append("")

        timeline = data.get("timeline")
        if timeline and isinstance(timeline, list):
            L.append("### TIMELINE")
            for t in timeline:
                if isinstance(t, dict):
                    wave = t.get("wave") or t.get("time_from_publish", "?")
                    event_text = t.get("event", "")
                    drivers = t.get("drivers")
                    effect = t.get("effect", "")
                    detail = effect or (", ".join(drivers) if drivers else "")
                    L.append(f"W{wave}: {event_text} -> {detail}")
                else:
                    L.append(str(t)[:200])
            L.append("")

        bif = data.get("bifurcation_points")
        if bif and isinstance(bif, list):
            L.append("### BIFURCATION")
            for b in bif:
                if isinstance(b, dict):
                    wave = b.get("wave") or b.get("wave_range", "?")
                    trigger = b.get("trigger", "") or b.get("turning_point", "")
                    from_s = b.get("from", "")
                    to_s = b.get("to", "") or b.get("counterfactual", "")
                    L.append(f"W{wave}: {trigger} | {from_s} -> {to_s}")
                else:
                    L.append(str(b)[:200])
            L.append("")

        insights = data.get("agent_insights")
        if insights and isinstance(insights, dict):
            L.append("### INSIGHTS")
            self._format_agent_insights(insights, L)
            L.append("")

        # PMF-specific fields
        grade = (
            data.get("grade")
            or data.get("pmf_grade")
            or data.get("overall_grade")
        )
        if grade:
            L.append(f"PMF_GRADE: {grade}")

        scorecard = data.get("scorecard")
        if scorecard and isinstance(scorecard, dict):
            L.append("### SCORECARD")
            dims = scorecard.get("dimensions") or scorecard
            for k, v in dims.items():
                if isinstance(v, dict):
                    score = v.get("score", "?")
                    rationale = (v.get("rationale") or "")[:120]
                    L.append(f"  {k}={score} {rationale}")
                else:
                    L.append(f"  {k}={v}")
            L.append("")
