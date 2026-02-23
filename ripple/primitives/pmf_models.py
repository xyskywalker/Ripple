# ripple/primitives/pmf_models.py
"""PMF 验证领域数据模型。 / PMF validation domain data models.

与设计文档 Section 10 对齐。 / Aligned with design doc Section 10.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TribunalMember:
    """合议庭成员配置。 / Tribunal member configuration."""
    role: str
    perspective: str
    expertise: str


@dataclass(frozen=True)
class TribunalOpinion:
    """单个评审员的意见。 / Single tribunal member's opinion."""
    member_role: str
    scores: Dict[str, int]
    narrative: str
    round_number: int


@dataclass(frozen=True)
class DeliberationRecord:
    """合议庭单轮辩论记录。 / Single round deliberation record."""
    round_number: int
    opinions: List[TribunalOpinion]
    challenges: List[Dict[str, Any]]
    consensus_points: List[str]
    dissent_points: List[str]
    converged: bool = False  # v3: 双重闸门收敛标记 / Dual-gate convergence flag


@dataclass(frozen=True)
class EvidencePack:
    """压缩后的证据包（v3 新增）。 / Compressed evidence pack (v3).

    固定字段、固定长度上限，防止溢出 LLM 上下文窗口。
    / Fixed fields, fixed length limits to prevent context overflow.
    """
    source: str                     # 证据来源（如 "RIPPLE Phase, Wave 0-3"） / Evidence source
    summary: str                     # 证据摘要（≤500 字） / Evidence summary (≤500 words)
    key_signals: List[Dict[str, Any]]  # 关键信号（≤10 条，含 wave_id） / Key signals (≤10)
    statistics: Dict[str, Any]       # 传播统计概要 / Propagation statistics
    full_records_ref: str            # v4.1: JSON Pointer 引用，如 "#/process/waves"


@dataclass(frozen=True)
class PMFScorecard:
    """结构化评分卡。 / Structured scorecard."""
    dimensions: Dict[str, Dict[str, Any]]
    overall_grade: str
    confidence: str


@dataclass(frozen=True)
class PMFVerdict:
    """PMF 验证最终裁决。 / PMF validation final verdict.

    v4 修正（与设计文档 Section 10 完全对齐）：
    - wave_records → evidence_pack: EvidencePack（类型统一为 dataclass，非 Dict）
    - deliberation_records → deliberation_summary + deliberation_records_ref
      （只返回摘要，完整记录落盘到 process.deliberation，通过 JSON Pointer 引用）
    - 新增 disclaimer（强制免责声明）
    - 新增 assumptions_to_verify（需真实数据验证的假设列表）
    """
    grade: str
    confidence: str
    executive_summary: str
    scorecard: PMFScorecard
    evidence_pack: EvidencePack  # v4: 统一为 dataclass 类型（非 Dict）
    deliberation_summary: Dict[str, Any]  # v4: 辩论摘要（最终立场+共识/分歧，非全部轮次）
    deliberation_records_ref: str  # v4.1: JSON Pointer，如 "#/process/deliberation"
    recommendations: List[str]
    assumptions_to_verify: List[str]  # v3: 哪些假设需要真实数据验证
    ensemble_stats: Dict[str, Any]
    disclaimer: str = "本报告为基于 LLM 多 Agent 模拟的预判，所有数据仅反映模型输出稳定性，不构成统计显著性结论或投资/经营建议。真实市场验证不可替代。"
    variant_comparison: Optional[Dict[str, Any]] = None
