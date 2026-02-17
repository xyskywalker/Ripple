# events.py
# =============================================================================
# 模拟进度事件 — 供外部应用实时获取模拟状态。 / Simulation progress events for external real-time status.
# =============================================================================

"""模拟进度事件，供外部集成使用。 / Simulation progress events for external integration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SimulationEvent:
    """模拟过程中的结构化进度事件。 / Structured progress event emitted during simulation.

    外部应用通过注册 on_progress 回调来接收此类事件，
    实现实时进度展示、WebSocket 推送、SSE 流等集成场景。
    / External apps register an on_progress callback to receive these events
    for real-time display, WebSocket push, SSE streaming, etc.

    Attributes:
        type: 事件类型 / Event type.
            - "phase_start": 阶段开始 / Phase started
            - "phase_end": 阶段结束 / Phase ended
            - "wave_start": 波次开始 / Wave started
            - "wave_end": 波次结束 / Wave ended
            - "agent_activated": Agent 被激活 / Agent activated
            - "agent_responded": Agent 完成响应 / Agent responded
            - "error": 发生错误 / Error occurred
        phase: 当前阶段 / Current phase ("INIT" | "SEED" | "RIPPLE" | "OBSERVE" | "SYNTHESIZE").
        run_id: 本次模拟的唯一标识 / Unique run identifier.
        timestamp: 单调时钟（秒），用于计算耗时 / Monotonic clock (seconds) for elapsed time.
        progress: 模拟总进度 / Overall progress (0.0 ~ 1.0).
        wave: 当前波次序号 / Current wave index (0-indexed, RIPPLE phase only).
        total_waves: 预估总波次数 / Estimated total waves (available after INIT).
        agent_id: 相关 Agent 标识 / Related agent identifier.
        agent_type: Agent 类型 / Agent type ("star" | "sea" | "omniscient").
        detail: 事件附加数据，结构因 type 而异 / Extra data, structure varies by type.
    """

    type: str
    phase: str
    run_id: str
    timestamp: float = field(default_factory=time.monotonic)
    progress: float = 0.0
    wave: Optional[int] = None
    total_waves: Optional[int] = None
    agent_id: Optional[str] = None
    agent_type: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
