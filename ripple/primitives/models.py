# models.py
# =============================================================================
# 本模块定义 Ripple CAS 引擎的所有核心数据模型，与设计文档 Section 3 严格对齐。
# / Core data models for the Ripple CAS engine, aligned with design doc Section 3.
# 包含 / Contains：Ripple、Event、Meme、PhaseVector、Snapshot、Field、
#       BudgetState、SimulationConfig 等不可变/可序列化结构 / immutable & serializable structures.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Ripple:
    """涟漪 — CAS 核心抽象，统一信息传播、能量衰减、语义变异三个维度。
    / Ripple — core CAS abstraction unifying propagation, energy decay & semantic mutation.

    root_id 约束 / root_id invariant：种子 Ripple root_id = self.id；子 Ripple root_id = parent.root_id；
    不可变；不可为空字符串（创建时必须赋值）。 / Immutable; must not be empty at creation.
    """

    id: str
    content: str  # 语义载荷 / Semantic payload
    content_embedding: List[float]  # 预计算向量指纹 / Pre-computed embedding
    energy: float  # 传播强度 / Propagation intensity (0.0-max_energy)
    origin_agent: str  # 发射者 / Originating agent
    ripple_type: str  # 领域定义 / Domain-defined type (post/comment/repost/...)
    emotion: Dict[str, float]  # 情感向量 / Emotion vector {anger: 0.8, ...}
    trace: List[str]  # 传播路径 / Propagation path [agent_id, ...]
    tick_born: int  # 创建时间（Wave 序号） / Birth tick (wave index)
    mutations: List[Dict[str, Any]]  # 语义漂移日志 / Semantic drift log
    parent_id: Optional[str] = None  # 父 Ripple / Parent ripple
    root_id: str = ""  # 根 Ripple — 必填不变量！ / Root ripple — required invariant!


@dataclass
class Event:
    """唯一权威定义 — 与设计文档 Section 3.4 完全一致。 / Canonical definition — matches design doc Section 3.4."""

    agent_id: str
    action: str  # 动作类型 / Action type (post/repost/quote/comment/like/wait/...)
    ripple_id: str
    tick: int
    response_type: str  # 响应类型 / Response type (amplify/absorb/mutate/ignore/suppress)
    energy: float  # 原始能量 / Raw energy
    effective_energy: float  # 衰减+稀释+放大后的有效能量 / Effective energy after decay & dilution
    parent_ripple_id: Optional[str] = None
    trace_len: int = 0
    drift_direction: Optional[str] = None  # 漂移方向 / Drift direction (radicalization/moderation/tangent/None)
    wave_index: int = 0


@dataclass
class Meme:
    """模因 — Field.meme_pool 的元素类型。 / Meme — element type of Field.meme_pool."""

    tag: str  # 模因标签 / Meme tag (e.g. "#年终奖取消")
    heat: float  # 热度 / Heat (0-1)
    born_tick: int  # 创建时间 / Birth tick
    last_referenced: int  # 最后被引用的 Wave / Last referenced wave


@dataclass
class PhaseVector:
    """多维相态向量。 / Multivariate phase vector."""

    vector: Dict[str, str]  # 相态维度 / Phase dimensions {"heat": "explosion", ...}
    confidence: float = 0.0  # Omniscient 置信度 / Omniscient confidence
    tick: int = 0  # 对应时间步 / Corresponding tick


@dataclass
class Snapshot:
    """宏观快照 — Omniscient Agent 生成的全网状态切片。 / Macro snapshot — network state slice from Omniscient."""

    tick: int
    phase_vector: PhaseVector
    metrics: Dict[str, Any] = field(default_factory=dict)
    emergence_events: List[Dict[str, Any]] = field(default_factory=list)
    bifurcation_candidates: List[Dict[str, Any]] = field(default_factory=list)
    topology_recommendations: List[Dict[str, Any]] = field(default_factory=list)
    estimated: bool = False  # True 表示统计估算（非 LLM 分析） / True = statistical estimate (not LLM analysis)


@dataclass
class Field:
    """场 — CAS 全局状态。 / Field — CAS global state.

    topology 类型为 Any，因 TopologyGraph 在 topology/graph.py 中定义。
    / topology typed as Any because TopologyGraph is defined in topology/graph.py.
    """

    topology: Any  # TopologyGraph（加权有向图） / TopologyGraph (weighted digraph)
    ambient: Dict[str, Any]  # 全局上下文 / Global context
    meme_pool: List[Meme]  # 活跃模因池 / Active meme pool
    dynamic_parameters: Dict[str, Any] = field(default_factory=dict)
    wave_records: List['WaveRecord'] = field(default_factory=list)


@dataclass
class BudgetState:
    """LLM 调用次数预算状态（数据模型占位）。 / LLM call budget state (data-model placeholder).

    运行时完整实现在 ripple.llm.router.BudgetState，两者字段保持一致。
    / Full runtime impl in ripple.llm.router.BudgetState; fields kept in sync.
    max_calls <= 0 表示不限制。 / max_calls <= 0 means unlimited.
    """

    total_calls: int = 0
    max_calls: int = 200  # <= 0 表示不限制 / <= 0 means unlimited
    calls_by_role: Dict[str, int] = field(default_factory=dict)


@dataclass
class SimulationConfig:
    """引擎运行时配置 — 停机条件等引擎编排行为参数。 / Runtime config — halt conditions & orchestration params.

    quiescent_wave_limit 取值范围 / valid range：[1, max_waves]
    """

    max_waves: int = 8
    quiescent_wave_limit: int = 3  # 连续静默轮数停机阈值 / Consecutive quiescent waves before halt [1, max_waves]
    random_seed: Optional[int] = None
    max_llm_calls: int = 200  # 单次模拟 LLM 调用上限 / Max LLM calls per simulation run


# =============================================================================
# 全视者中心制架构数据模型 / Omniscient-driven architecture data models
# =============================================================================


@dataclass
class AgentActivation:
    """全视者裁决中的单个 Agent 激活指令。 / Single agent activation command in an Omniscient verdict."""
    agent_id: str
    incoming_ripple_energy: float
    activation_reason: str


@dataclass
class AgentSkip:
    """全视者裁决中的单个 Agent 跳过记录。 / Single agent skip record in an Omniscient verdict."""
    agent_id: str
    skip_reason: str


@dataclass
class OmniscientVerdict:
    """全视者每轮 wave 的裁决输出。 / Omniscient verdict for each wave."""
    wave_number: int
    simulated_time_elapsed: str
    simulated_time_remaining: str
    continue_propagation: bool
    activated_agents: List[AgentActivation]
    skipped_agents: List[AgentSkip]
    global_observation: str
    termination_reason: Optional[str] = None

    @property
    def activated_agent_ids(self) -> List[str]:
        return [a.agent_id for a in self.activated_agents]


@dataclass
class WaveRecord:
    """一轮波纹的完整记录。 / Complete record of a single wave."""
    wave_number: int
    verdict: OmniscientVerdict
    agent_responses: Dict[str, Dict[str, Any]]
    events: List[Event]
