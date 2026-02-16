# events.py
# =============================================================================
# 模拟进度事件 — 供外部应用实时获取模拟状态。
# =============================================================================

"""Simulation progress events for external integration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SimulationEvent:
    """模拟过程中的结构化进度事件。

    外部应用通过注册 on_progress 回调来接收此类事件，
    实现实时进度展示、WebSocket 推送、SSE 流等集成场景。

    Attributes:
        type: 事件类型。
            - "phase_start": 阶段开始
            - "phase_end": 阶段结束
            - "wave_start": 波次开始
            - "wave_end": 波次结束
            - "agent_activated": Agent 被激活
            - "agent_responded": Agent 完成响应
            - "error": 发生错误
        phase: 当前阶段 ("INIT" | "SEED" | "RIPPLE" | "OBSERVE" | "SYNTHESIZE")。
        run_id: 本次模拟的唯一标识。
        timestamp: 事件产生时的单调时钟（秒），用于计算耗时。
        progress: 模拟总进度 (0.0 ~ 1.0)，适合直接驱动进度条。
        wave: 当前波次序号（0-indexed），仅 RIPPLE 阶段有效。
        total_waves: 预估总波次数，仅 INIT 完成后有效。
        agent_id: 相关 Agent 标识。
        agent_type: Agent 类型 ("star" | "sea" | "omniscient")。
        detail: 事件附加数据，结构因 type 而异。
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
