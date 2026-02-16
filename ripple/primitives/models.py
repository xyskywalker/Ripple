# models.py
# =============================================================================
# 本模块定义 Ripple CAS 引擎的所有核心数据模型，与设计文档 Section 3 严格对齐。
# 包含：Ripple、Event、Meme、PhaseVector、Snapshot、Field、
#       BudgetState、SimulationConfig 等不可变/可序列化结构。
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Ripple:
    """涟漪 — CAS 核心抽象，统一信息传播、能量衰减、语义变异三个维度。

    root_id 约束：种子 Ripple root_id = self.id；子 Ripple root_id = parent.root_id；
    不可变；不可为空字符串（创建时必须赋值）。
    """

    id: str
    content: str  # 语义载荷
    content_embedding: List[float]  # 预计算向量指纹
    energy: float  # 传播强度 (0.0-max_energy, 线性空间)
    origin_agent: str  # 发射者
    ripple_type: str  # 领域定义（post/comment/repost/...）
    emotion: Dict[str, float]  # 情感向量 {anger: 0.8, fear: 0.2, ...}
    trace: List[str]  # 传播路径 [agent_id, ...]
    tick_born: int  # 创建时间（Wave 序号）
    mutations: List[Dict[str, Any]]  # 语义漂移日志
    parent_id: Optional[str] = None  # 父 Ripple
    root_id: str = ""  # 根 Ripple — 必填不变量！


@dataclass
class Event:
    """唯一权威定义 — 与设计文档 Section 3.4 完全一致。"""

    agent_id: str
    action: str  # post/repost/quote/comment/like/wait/...
    ripple_id: str
    tick: int
    response_type: str  # amplify/absorb/mutate/ignore/suppress
    energy: float  # 原始能量
    effective_energy: float  # 衰减+稀释+放大后的有效能量
    parent_ripple_id: Optional[str] = None
    trace_len: int = 0
    drift_direction: Optional[str] = None  # radicalization/moderation/tangent/None
    wave_index: int = 0


@dataclass
class Meme:
    """模因 — Field.meme_pool 的元素类型。"""

    tag: str  # 模因标签（如 "#年终奖取消"）
    heat: float  # 热度 (0-1)
    born_tick: int  # 创建时间
    last_referenced: int  # 最后被引用的 Wave


@dataclass
class PhaseVector:
    """多维相态向量。"""

    vector: Dict[str, str]  # {"heat": "explosion", "sentiment": "polarized", "coherence": "chaos"}
    confidence: float = 0.0  # Omniscient 置信度
    tick: int = 0  # 对应时间步


@dataclass
class Snapshot:
    """宏观快照 — Omniscient Agent 生成的全网状态切片。"""

    tick: int
    phase_vector: PhaseVector
    metrics: Dict[str, Any] = field(default_factory=dict)
    emergence_events: List[Dict[str, Any]] = field(default_factory=list)
    bifurcation_candidates: List[Dict[str, Any]] = field(default_factory=list)
    topology_recommendations: List[Dict[str, Any]] = field(default_factory=list)
    estimated: bool = False  # True 表示统计估算（非 LLM 分析）


@dataclass
class Field:
    """场 — CAS 全局状态。

    topology 类型为 Any，因 TopologyGraph 在 topology/graph.py 中定义。
    """

    topology: Any  # TopologyGraph（加权有向图）
    ambient: Dict[str, Any]  # 全局上下文
    meme_pool: List[Meme]  # 活跃模因池
    dynamic_parameters: Dict[str, Any] = field(default_factory=dict)
    wave_records: List['WaveRecord'] = field(default_factory=list)


@dataclass
class BudgetState:
    """LLM 调用次数预算状态（数据模型占位）。

    运行时完整实现在 ripple.llm.router.BudgetState，两者字段保持一致。
    max_calls <= 0 表示不限制。
    """

    total_calls: int = 0
    max_calls: int = 200  # <= 0 表示不限制
    calls_by_role: Dict[str, int] = field(default_factory=dict)


@dataclass
class SimulationConfig:
    """引擎运行时配置 — 停机条件等引擎编排行为参数。

    quiescent_wave_limit 取值范围：[1, max_waves]
    """

    max_waves: int = 8
    quiescent_wave_limit: int = 3  # 连续静默轮数停机阈值 [1, max_waves]
    random_seed: Optional[int] = None
    max_llm_calls: int = 200  # 单次模拟的 LLM 调用总次数上限


# =============================================================================
# 全视者中心制架构数据模型
# =============================================================================


@dataclass
class AgentActivation:
    """全视者裁决中的单个 Agent 激活指令。"""
    agent_id: str
    incoming_ripple_energy: float
    activation_reason: str


@dataclass
class AgentSkip:
    """全视者裁决中的单个 Agent 跳过记录。"""
    agent_id: str
    skip_reason: str


@dataclass
class OmniscientVerdict:
    """全视者每轮 wave 的裁决输出。"""
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
    """一轮波纹的完整记录。"""
    wave_number: int
    verdict: OmniscientVerdict
    agent_responses: Dict[str, Dict[str, Any]]
    events: List[Event]
