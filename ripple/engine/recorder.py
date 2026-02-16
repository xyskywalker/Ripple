# recorder.py
# =============================================================================
# 模拟过程增量记录器 — 在模拟每个关键节点动态写入 JSON 文件。
#
# 设计目标：
# 1. 细粒度记录：初始化结果、种子注入、每轮 wave 前后的场快照、
#    全视者裁决、Agent 响应、观测、合成结果。
# 2. 动态写入：每个关键节点后立即刷写磁盘，不等模拟结束。
# 3. 崩溃安全：使用临时文件 + 原子重命名，文件在任意时刻都是合法 JSON。
# 4. 向后兼容：合成结果保持顶层键（prediction/timeline/...），
#    详细过程数据放在 process 键下。
# =============================================================================

"""模拟过程增量记录器。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ripple.primitives.models import OmniscientVerdict

logger = logging.getLogger(__name__)


class SimulationRecorder:
    """模拟过程增量记录器。

    在模拟的每个关键节点（初始化、种子注入、每轮 wave 前后、观测、合成）
    动态写入 JSON 文件。确保文件在任意时刻都是合法 JSON，即使模拟中途失败
    也能保留已完成阶段的完整记录。

    输出 JSON 结构：
        {
            "meta": { run_id, engine_version, start_time, end_time, status, ... },
            "simulation_input": { ... },
            "process": {
                "init": { star_configs, sea_configs, dynamic_parameters, ... },
                "seed": { content, energy },
                "waves": [
                    {
                        "wave_number": 0,
                        "pre_snapshot": { ... },     // wave 开始前的场状态
                        "verdict": { ... },          // 全视者裁决
                        "agent_responses": { ... },  // Agent 响应
                        "post_snapshot": { ... },    // wave 结束后的场状态
                    },
                    ...
                ],
                "observation": { ... },
            },
            // 以下为向后兼容的顶层键（合成阶段填充）
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
        """初始化记录器，立即创建输出文件。

        Args:
            output_path: JSON 输出文件路径。
            run_id: 本次模拟的唯一标识。
        """
        self._path = output_path
        self._run_id = run_id
        self._start_time = time.monotonic()
        self._start_datetime = datetime.now()

        # 核心数据结构 — 与输出 JSON 一一对应
        self._data: Dict[str, Any] = {
            "meta": {
                "run_id": run_id,
                "engine_version": "0.1.0",
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
                "observation": None,
            },
            # 向后兼容的顶层键（合成阶段填充）
            "total_waves": 0,
            "run_id": run_id,
            "wave_records_count": 0,
        }
        # 创建文件，标记模拟已启动
        self._flush()

    # -----------------------------------------------------------------
    # 公共记录接口 — 各阶段调用入口
    # -----------------------------------------------------------------

    def record_simulation_input(
        self, simulation_input: Dict[str, Any],
    ) -> None:
        """记录模拟输入参数（供复现追溯）。"""
        self._data["simulation_input"] = simulation_input
        self._flush()

    def record_init(
        self,
        init_result: Dict[str, Any],
        estimated_waves: int,
        max_waves: int,
    ) -> None:
        """记录 INIT 阶段结果 — 全视者初始化输出。

        包含星海 Agent 配置、动态参数、种子涟漪原始数据、预估 wave 数。
        """
        self._data["process"]["init"] = {
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
        """记录 SEED 阶段结果 — 种子涟漪注入。"""
        self._data["process"]["seed"] = {
            "timestamp": datetime.now().isoformat(),
            "content": seed_content,
            "energy": seed_energy,
        }
        self._flush()

    def record_wave_start(
        self, wave_number: int, pre_snapshot: Dict[str, Any],
    ) -> None:
        """记录 wave 启动前的场快照。

        在全视者发出裁决之前调用，捕获场的当前状态作为 pre_snapshot。
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
        self._data["process"]["waves"].append(wave_entry)
        self._data["wave_records_count"] = len(
            self._data["process"]["waves"],
        )
        self._flush()

    def record_wave_end(
        self,
        wave_number: int,
        verdict: OmniscientVerdict,
        agent_responses: Dict[str, Dict[str, Any]],
        post_snapshot: Dict[str, Any],
        terminated: bool = False,
    ) -> None:
        """记录 wave 完成后的完整数据。

        包含全视者裁决、各 Agent 响应、wave 结束后的场快照。
        如果 terminated=True，表示全视者判定传播终止。
        """
        # 查找对应的 wave 条目（由 record_wave_start 创建）
        wave_entry = self._find_wave_entry(wave_number)
        if wave_entry is None:
            # 容错：如果没有对应的 start 记录，创建新条目
            logger.warning(
                f"Wave {wave_number} 缺少 start 记录，创建补充条目"
            )
            wave_entry = {
                "wave_number": wave_number,
                "timestamp_start": datetime.now().isoformat(),
                "pre_snapshot": None,
            }
            self._data["process"]["waves"].append(wave_entry)

        wave_entry["timestamp_end"] = datetime.now().isoformat()
        wave_entry["verdict"] = self._serialize_verdict(verdict)
        wave_entry["agent_responses"] = agent_responses
        wave_entry["post_snapshot"] = post_snapshot
        wave_entry["terminated"] = terminated

        self._data["total_waves"] = len(self._data["process"]["waves"])
        self._data["wave_records_count"] = len(
            self._data["process"]["waves"],
        )
        self._flush()

    def record_observation(self, observation: str) -> None:
        """记录 OBSERVE 阶段结果 — 全视者的全局观测。"""
        self._data["process"]["observation"] = {
            "timestamp": datetime.now().isoformat(),
            "content": observation,
        }
        self._flush()

    def record_synthesis(self, result: Dict[str, Any]) -> None:
        """记录 SYNTHESIZE 阶段 — 将合成结果写入顶层键（向后兼容）。

        合成结果中的 prediction/timeline/bifurcation_points/agent_insights
        等字段直接放入顶层，保持与旧版输出格式一致。
        """
        # 向后兼容：将合成结果的各字段提升到顶层
        for key, value in result.items():
            if key not in ("meta", "process", "simulation_input"):
                self._data[key] = value
        self._flush()

    def finalize(self, total_waves: int) -> None:
        """标记模拟完成，写入最终元信息。"""
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
        """标记模拟失败，记录错误信息。"""
        elapsed = time.monotonic() - self._start_time
        self._data["meta"]["end_time"] = datetime.now().isoformat()
        self._data["meta"]["elapsed_seconds"] = round(elapsed, 2)
        self._data["meta"]["status"] = "failed"
        self._data["meta"]["error"] = error
        self._flush()

    # -----------------------------------------------------------------
    # 属性访问
    # -----------------------------------------------------------------

    @property
    def output_path(self) -> Path:
        """输出文件路径。"""
        return self._path

    @property
    def data(self) -> Dict[str, Any]:
        """当前记录数据的完整快照（只读引用）。"""
        return self._data

    # -----------------------------------------------------------------
    # 内部方法
    # -----------------------------------------------------------------

    def _find_wave_entry(
        self, wave_number: int,
    ) -> Optional[Dict[str, Any]]:
        """根据 wave_number 查找已有的 wave 条目。"""
        for w in self._data["process"]["waves"]:
            if w["wave_number"] == wave_number:
                return w
        return None

    def _flush(self) -> None:
        """将当前状态写入 JSON 文件。

        使用「先写临时文件 → 原子重命名」模式确保文件完整性。
        写入失败仅记录日志，不会中断模拟流程。
        """
        try:
            # 更新运行时长（仅在 running 状态下）
            if self._data["meta"]["status"] == "running":
                elapsed = time.monotonic() - self._start_time
                self._data["meta"]["elapsed_seconds"] = round(elapsed, 2)

            content = json.dumps(
                self._data,
                ensure_ascii=False,
                indent=2,
                default=str,
            )

            # 原子写入：先写 .tmp 再重命名，避免写入过程中崩溃导致文件损坏
            tmp_path = self._path.with_suffix(".json.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self._path)
        except Exception as e:
            logger.warning(f"记录器写入失败（不影响模拟流程）: {e}")

    @staticmethod
    def _serialize_verdict(
        verdict: OmniscientVerdict,
    ) -> Dict[str, Any]:
        """将 OmniscientVerdict 数据模型序列化为可 JSON 化的字典。"""
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
