"""Ripple 集中式提示词管理模块。

本文件统一管理 Ripple 系统中所有 Agent 使用的 LLM 提示词模板。
每个提示词均标注了调用位置和用途，方便后续优化管理。

提示词分类：
1. 全视者 (Omniscient) 提示词 —— INIT / RIPPLE / OBSERVE / SYNTHESIZE 各阶段
2. 星 Agent (Star) 提示词 —— KOL 个体行为模拟
3. 海 Agent (Sea) 提示词 —— 群体行为模拟
4. 通用提示词 —— 重试、错误处理等
"""

# =============================================================================
# 通用提示词
# =============================================================================

# 调用位置: omniscient.py — _init_sub_call(), ripple_verdict(), observe(),
#           synthesize_result() 中 JSON 解析失败时的重试前缀
# 用途: 告知 LLM 上一次输出格式有误，要求重新输出合法 JSON
RETRY_JSON_PREFIX = (
    "上一次输出解析失败，错误: {error}\n"
    "请重新输出，确保是合法 JSON 格式。\n\n"
)

# 调用位置: omniscient.py — ripple_verdict(), observe() 中的简短重试前缀
# 用途: 同上，较简短的版本
RETRY_JSON_PREFIX_SHORT = (
    "上一次输出解析失败: {error}\n请重新输出合法 JSON。\n\n"
)


# =============================================================================
# 全视者 (Omniscient) 提示词 — Phase INIT
# =============================================================================

# 调用位置: omniscient.py — _build_init_dynamics_prompt()
# 用途: INIT 阶段 Sub-call 1，分析领域画像中的时间特征，
#       提取每轮 wave 对应的现实时间窗口和衰减参数
OMNISCIENT_INIT_DYNAMICS = (
    "## 领域画像\n\n{skill_profile}\n\n"
    "## 模拟请求\n\n{input_json}\n\n"
    "## 你的任务\n\n"
    "请分析领域画像中的时间特征，提取每轮 wave 对应的现实时间窗口。\n"
    "{horizon_line}"
    "**重点关注**：画像中的内容衰减周期、推荐算法刷新周期、"
    "传播关键窗口、互动高峰时段等时间线索。\n"
    "如果画像中有明确的时间约定，直接使用；"
    "如果没有，请基于对该平台特性的理解裁定一个合理值。\n\n"
    "输出严格 JSON，格式如下：\n"
    "```json\n"
    "{{\n"
    '  "wave_time_window": "4h",\n'
    '  "wave_time_window_reasoning": "从画像中提取的推理依据...",\n'
    '  "energy_decay_per_wave": 0.15,\n'
    '  "platform_characteristics": "平台关键特征摘要"\n'
    "}}\n"
    "```\n"
)

# 调用位置: omniscient.py — _build_init_dynamics_prompt() 内条件拼接
# 用途: 当模拟请求指定了 simulation_horizon 时，附加到 INIT:dynamics 提示中
OMNISCIENT_INIT_DYNAMICS_HORIZON_LINE = (
    "\n- 模拟总时长为 {horizon}，请据此判断 wave_time_window\n"
)

# 调用位置: omniscient.py — _build_init_agents_prompt()
# 用途: INIT 阶段 Sub-call 2，根据领域画像和动态参数创建 Star/Sea Agent 配置
OMNISCIENT_INIT_AGENTS = (
    "## 领域画像\n\n{skill_profile}\n\n"
    "## 模拟请求\n\n{input_json}\n\n"
    "## 已确定的动态参数\n\n{dp_json}\n\n"
    "## 你的任务\n\n"
    "请根据以上信息，创建参与模拟的 Star（KOL）Agent 和 "
    "Sea（用户群体）Agent。\n\n"
    "输出严格 JSON，格式如下：\n"
    "```json\n"
    "{{\n"
    '  "star_configs": [\n'
    '    {{"id": "star_xxx", "description": "...", '
    '"influence_level": "high/medium/low"}}\n'
    "  ],\n"
    '  "sea_configs": [\n'
    '    {{"id": "sea_xxx", "description": "...", '
    '"interest_tags": ["tag1", "tag2"]}}\n'
    "  ]\n"
    "}}\n"
    "```\n"
)

# 调用位置: omniscient.py — _build_init_topology_prompt()
# 用途: INIT 阶段 Sub-call 3，构建 Agent 间拓扑结构和种子涟漪
OMNISCIENT_INIT_TOPOLOGY = (
    "## 领域画像\n\n{skill_profile}\n\n"
    "## 模拟请求\n\n{input_json}\n\n"
    "## 已确定的动态参数\n\n{dp_json}\n\n"
    "## 已确定的 Agent 配置\n\n{agents_json}\n\n"
    "## 你的任务\n\n"
    "请基于以上已确定的 Agent 列表，构建拓扑结构和种子涟漪。\n\n"
    "输出严格 JSON，格式如下：\n"
    "```json\n"
    "{{\n"
    '  "topology": {{\n'
    '    "edges": [\n'
    '      {{"from": "agent_id_1", "to": "agent_id_2", '
    '"weight": 0.7}}\n'
    "    ]\n"
    "  }},\n"
    '  "seed_ripple": {{\n'
    '    "content": "种子涟漪内容描述",\n'
    '    "initial_energy": 0.6\n'
    "  }}\n"
    "}}\n"
    "```\n"
)


# =============================================================================
# 全视者 (Omniscient) 提示词 — Phase RIPPLE
# =============================================================================

# 调用位置: omniscient.py — _build_ripple_prompt() 内构建时间进度段
# 用途: 在 RIPPLE 裁决提示中展示当前模拟时间进度，
#       帮助全视者判断是否应终止传播
OMNISCIENT_RIPPLE_TIME_PROGRESS = (
    "## 模拟时间进度\n\n"
    "- 每轮 Wave 对应现实时间: {wave_time_window}\n"
    "- 当前模拟时间: {elapsed_h}h"
    "（Wave {wave_number} × {wave_time_window}）\n"
    "- 模拟总时长: {simulation_horizon}\n"
    "- 剩余模拟时间: {remaining_h}h\n"
    "- **当剩余时间 ≤ 0 时，你必须设置 "
    "continue_propagation = false**\n\n"
)

# 调用位置: omniscient.py — _build_ripple_prompt()
# 用途: CAS 复杂自适应系统核心传播原则，指导全视者做出延续性裁决
OMNISCIENT_RIPPLE_CAS_PRINCIPLES = (
    "## CAS 涟漪传播原则\n\n"
    "你的裁决必须遵循复杂自适应系统的核心规律：\n\n"
    "1. **累积叠加**：已激活的 Agent 在后续 wave 中应继续被激活"
    "（除非其能量自然衰减到极低水平）。"
    "新 Agent 是叠加到已有活跃群体之上，而非替换。\n"
    "2. **能量叠加**：一个 Agent 被多次激活时，其能量应反映"
    "累积效应——早期涟漪的余波 + 新涟漪的注入。\n"
    "3. **自然衰减**：如果一个 Agent 连续多轮未被新涟漪触达，"
    "其能量应逐步降低，直到低于激活阈值后自然退出。\n"
    "4. **非线性放大**：当多个 Agent 同时活跃时，它们之间的互动"
    "可以产生超线性的能量放大（涌现）。\n"
    "5. **反馈环**：Agent 的响应（特别是 amplify/create）会产生"
    "新的涟漪能量，这些能量应反馈到后续 wave 的裁决中。\n"
    "6. **注意力竞争**：Agent 的注意力资源是有限的。随着时间推移，"
    "新的刺激源不断出现，旧涟漪的激活效率自然下降。"
    "参考 energy_decay_per_wave 参数来量化衰减幅度。\n"
    "7. **传播饱和**：目标 Agent 群体是有限的。当涟漪已覆盖大部分"
    "可触达 Agent 后，新增触达的边际效率显著下降。"
    "已被多次激活的 Agent，其响应强度倾向于递减而非递增。\n"
    "8. **基础概率校准**：在 CAS 中，绝大多数扰动不会引发级联反应。"
    "你的默认预期应是平稳或小幅扩散，只有在明确观察到正反馈放大信号"
    "（多个 Agent 同时高能量 amplify）时才裁决高速增长。\n\n"
    "参考上方 Agent 状态中的激活次数和能量值，做出延续性裁决。\n\n"
)

# 调用位置: omniscient.py — _build_ripple_prompt() 中 wave_number == 0 时注入
# 用途: 首轮传播时提示全视者优先激活群体 Agent
OMNISCIENT_RIPPLE_WAVE0_HINT = (
    "## 首轮传播注意\n\n"
    "这是种子涟漪刚注入系统的阶段。在 CAS 中，"
    "扰动信号首先触达的是距离种子最近的群体（Sea Agent），"
    "随后才可能传导到更远的个体节点（Star Agent）。"
    "首轮应优先考虑激活群体 Agent。\n\n"
)

# 调用位置: omniscient.py — _build_ripple_prompt()
# 用途: RIPPLE 阶段的完整裁决提示词框架，
#       包含系统状态、传播历史、Agent 列表和 JSON 输出格式要求
OMNISCIENT_RIPPLE_VERDICT = (
    "## 当前 Wave: {wave_number}\n\n"
    "{time_progress}"
    "{cas_principles}"
    "## 系统状态\n\n{snapshot_json}\n\n"
    "## 传播历史\n\n{propagation_history}\n\n"
    "## 可用 Agent 列表（你必须从以下 agent_id 中选择）\n\n"
    "{agent_list}\n\n"
    "## 你的任务\n\n"
    "请决定本轮涟漪传播。你是全视者编排器，必须将涟漪委派给具体的 "
    "Agent 来模拟。每轮 wave 中，你应该激活所有仍有能量的 Agent "
    "并考虑新增被涟漪触达的 Agent"
    "（除非你决定终止传播）。\n\n"
    "**重要：activated_agents 必须使用上面列出的准确 agent_id 值。**\n\n"
    "输出严格 JSON，格式如下：\n"
    "```json\n"
    "{{\n"
    '  "wave_number": {wave_number},\n'
    '  "simulated_time_elapsed": "例: 2h",\n'
    '  "simulated_time_remaining": "例: 46h",\n'
    '  "continue_propagation": true,\n'
    '  "activated_agents": [\n'
    "    {{\n"
    '      "agent_id": "上轮已激活的_agent_id",\n'
    '      "incoming_ripple_energy": 0.55,\n'
    '      "activation_reason": "延续上轮活跃+新涟漪叠加"\n'
    "    }},\n"
    "    {{\n"
    '      "agent_id": "上轮已激活但衰减的_agent_id",\n'
    '      "incoming_ripple_energy": 0.35,\n'
    '      "activation_reason": "持续活跃，能量自然衰减"\n'
    "    }},\n"
    "    {{\n"
    '      "agent_id": "新触达的_agent_id",\n'
    '      "incoming_ripple_energy": 0.38,\n'
    '      "activation_reason": "首次被涟漪波及"\n'
    "    }}\n"
    "  ],\n"
    '  "skipped_agents": [\n'
    "    {{\n"
    '      "agent_id": "跳过的 agent_id",\n'
    '      "skip_reason": "能量衰减至极低/尚未被涟漪触达"\n'
    "    }}\n"
    "  ],\n"
    '  "global_observation": "本轮全局观察"\n'
    "}}\n"
    "```\n"
)


# =============================================================================
# 全视者 (Omniscient) 提示词 — Phase OBSERVE
# =============================================================================

# 调用位置: omniscient.py — _build_observe_prompt()
# 用途: OBSERVE 阶段，分析整个传播过程，判断当前相态（phase vector）、
#       检测涌现事件和相变
OMNISCIENT_OBSERVE = (
    "## 系统状态\n\n{snapshot_json}\n\n"
    "## 完整传播历史\n\n{full_history}\n\n"
    "## 你的任务\n\n"
    "请分析整个传播过程，判断当前相态和涌现事件。\n\n"
    "输出严格 JSON，格式如下：\n"
    "```json\n"
    "{{\n"
    '  "phase_vector": {{\n'
    '    "heat": "growth",\n'
    '    "sentiment": "unified",\n'
    '    "coherence": "ordered"\n'
    "  }},\n"
    '  "phase_transition_detected": false,\n'
    '  "transition_description": "",\n'
    '  "emergence_events": [\n'
    '    {{"description": "涌现事件描述", "evidence": "证据"}}\n'
    "  ],\n"
    '  "topology_recommendations": []\n'
    "}}\n"
    "```\n\n"
    "**字段约束：**\n"
    "- `heat` 必须为以下之一: seed | growth | explosion | stable | decline\n"
    "- `sentiment` 必须为以下之一: unified | polarized | neutral\n"
    "- `coherence` 必须为以下之一: ordered | chaotic | fragmented\n"
)


# =============================================================================
# 全视者 (Omniscient) 提示词 — Phase SYNTHESIZE
# =============================================================================

# 调用位置: omniscient.py — _build_synth_prompt() 当无历史数据时
# 用途: SYNTHESIZE 阶段的相对值预测模板（无历史数据锚定时使用）
OMNISCIENT_SYNTHESIZE_RELATIVE = (
    "## 最终系统状态\n\n{snapshot_json}\n\n"
    "## 观测分析\n\n{obs_json}\n\n"
    "## 原始模拟请求\n\n{input_json}\n\n"
    "## 你的任务\n\n"
    "请合成最终预测结果。\n\n"
    "**重要：本次模拟没有提供历史参考数据，因此不要输出任何绝对数字。"
    "所有预测必须以相对百分比形式表达，描述相对于同类内容平均水平的变化。**\n\n"
    "输出严格 JSON，格式如下：\n"
    "```json\n"
    "{{\n"
    '  "prediction": {{\n'
    '    "impact": "简要影响描述",\n'
    '    "relative_estimate": {{\n'
    '      "simulation_horizon": "48h",\n'
    '      "vs_baseline": "描述参考基线（同类内容平均水平）",\n'
    '      "views_relative": "+15%~+30%",\n'
    '      "engagements_relative": "+10%~+25%",\n'
    '      "favorites_relative": "+5%~+20%",\n'
    '      "comments_relative": "+10%~+30%",\n'
    '      "shares_relative": "+5%~+15%",\n'
    '      "follows_relative": "+2%~+10%",\n'
    '      "confidence": "low/medium/high",\n'
    '      "confidence_reasoning": "推理依据"\n'
    "    }},\n"
    '    "verdict": "一句话结论（必须包含以下英文关键词之一：'
    'explosion / growth / decline / stable / seed）"\n'
    "  }},\n"
    '  "timeline": [\n'
    "    {{\n"
    '      "time_from_publish": "0-2h",\n'
    '      "event": "事件描述",\n'
    '      "drivers": ["driver1", "driver2"]\n'
    "    }}\n"
    "  ],\n"
    '  "bifurcation_points": [\n'
    "    {{\n"
    '      "wave_range": "Wave0-1",\n'
    '      "turning_point": "转折描述",\n'
    '      "counterfactual": "反事实推理"\n'
    "    }}\n"
    "  ],\n"
    '  "agent_insights": {{\n'
    '    "stars": {{"star_id": {{"role": "角色", "best_leverage": "建议"}}}},\n'
    '    "seas": {{"sea_id": {{"core_motivation": "动机", "best_message": "建议"}}}}\n'
    "  }}\n"
    "}}\n"
    "```\n"
)

# 调用位置: omniscient.py — _build_synth_prompt() 当有历史数据时
# 用途: SYNTHESIZE 阶段的锚定式绝对值预测模板（有历史数据参考时使用）
OMNISCIENT_SYNTHESIZE_ANCHORED = (
    "## 最终系统状态\n\n{snapshot_json}\n\n"
    "## 观测分析\n\n{obs_json}\n\n"
    "## 原始模拟请求\n\n{input_json}\n\n"
    "## 你的任务\n\n"
    "请合成最终预测结果。\n\n"
    "**重要：本次模拟提供了历史参考数据。"
    "你必须先展示历史基线，再给出预测变化。"
    "绝对值必须从历史基线 + 变化百分比推导得出。"
    "预测值不应偏离历史基线超过 5 倍（上限）或 0.2 倍（下限），"
    "除非有极强的涌现信号支持。**\n\n"
    "输出严格 JSON，格式如下：\n"
    "```json\n"
    "{{\n"
    '  "prediction": {{\n'
    '    "impact": "简要影响描述",\n'
    '    "anchored_estimate": {{\n'
    '      "simulation_horizon": "48h",\n'
    '      "historical_baseline": {{\n'
    '        "source": "历史数据来源描述",\n'
    '        "metrics": "从 simulation_input.historical 中提取的关键指标"\n'
    "      }},\n"
    '      "predicted_change": "+20%",\n'
    '      "views": {{"p50": 60000, "p80": 75000, "p95": 100000}},\n'
    '      "engagements_total": {{"p50": 9600}},\n'
    '      "favorites": {{"p50": 3200}},\n'
    '      "comments": {{"p50": 640}},\n'
    '      "shares": {{"p50": 480}},\n'
    '      "follows_gained": {{"p50": 150}},\n'
    '      "confidence": "low/medium/high",\n'
    '      "confidence_reasoning": "推理依据"\n'
    "    }},\n"
    '    "verdict": "一句话结论（必须包含以下英文关键词之一：'
    'explosion / growth / decline / stable / seed）"\n'
    "  }},\n"
    '  "timeline": [\n'
    "    {{\n"
    '      "time_from_publish": "0-2h",\n'
    '      "event": "事件描述",\n'
    '      "drivers": ["driver1", "driver2"]\n'
    "    }}\n"
    "  ],\n"
    '  "bifurcation_points": [\n'
    "    {{\n"
    '      "wave_range": "Wave0-1",\n'
    '      "turning_point": "转折描述",\n'
    '      "counterfactual": "反事实推理"\n'
    "    }}\n"
    "  ],\n"
    '  "agent_insights": {{\n'
    '    "stars": {{"star_id": {{"role": "角色", "best_leverage": "建议"}}}},\n'
    '    "seas": {{"sea_id": {{"core_motivation": "动机", "best_message": "建议"}}}}\n'
    "  }}\n"
    "}}\n"
    "```\n"
)


# =============================================================================
# 星 Agent (Star) 提示词
# =============================================================================

# 调用位置: star.py — _build_system_prompt()
# 用途: Star Agent 的系统提示词，定义 KOL 角色身份、响应类型、
#       记忆回忆策略和 JSON 输出格式
STAR_SYSTEM_PROMPT = (
    "你是 {description}。\n\n"
    "你收到了一条涟漪（信息传播信号）。"
    "请以你的身份决定如何响应。\n\n"
    "可选的响应类型：\n"
    "- amplify: 转发/扩散这条信息\n"
    "- create: 基于此创作新的内容\n"
    "- comment: 发表评论\n"
    "- ignore: 忽略\n\n"
    "在回忆过去的经历时，同时考虑：\n"
    "1. 事情发生了多久（最近的事更容易想起）\n"
    "2. 事情有多重要（重大事件更深刻）\n"
    "3. 与当前情境的相关性\n"
    "{memory_context}\n\n"
    "输出严格 JSON：response_type, response_content, "
    "outgoing_energy (0-1), reasoning"
)

# 调用位置: star.py — _build_user_prompt()
# 用途: Star Agent 收到涟漪时的用户提示词，传递涟漪来源、能量和内容
STAR_USER_PROMPT = (
    "收到涟漪:\n"
    "- 来源: {source}\n"
    "- 能量: {energy}\n"
    "- 内容: {content}\n\n"
    "请决定你的响应。"
)

# 调用位置: star.py — _build_system_prompt() 内记忆格式化
# 用途: Star Agent 单条记忆的格式模板
STAR_MEMORY_LINE = (
    "- 收到来自 {ripple_source} 的涟漪: "
    "'{ripple_content_preview}...' → "
    "我的回应: {response_type}"
)

# 调用位置: star.py — _build_system_prompt() 内记忆段标题
# 用途: Star Agent 记忆段的标题
STAR_MEMORY_HEADER = "\n\n## 你的近期记忆\n"


# =============================================================================
# 海 Agent (Sea) 提示词
# =============================================================================

# 调用位置: sea.py — _build_system_prompt()
# 用途: Sea Agent 的系统提示词，定义群体角色、响应类型、
#       群体内部差异性（避免 LLM 从众倾向）和 JSON 输出格式
SEA_SYSTEM_PROMPT = (
    "你代表的群体是：{description}\n\n"
    "你收到了一条涟漪（信息传播信号）。"
    "请以这个群体的集体视角决定如何响应。\n\n"
    "可选的响应类型：\n"
    "- amplify: 群体积极传播扩散\n"
    "- absorb: 群体关注但不主动传播\n"
    "- mutate: 群体将话题方向变异/漂移\n"
    "- suppress: 群体沉默或压制（沉默螺旋）\n"
    "- ignore: 群体完全无视\n\n"
    "**重要：你代表的群体不是铁板一块。**"
    "即使大多数人倾向于跟随主流意见，"
    "群体中也有一部分人会持不同看法，甚至故意唱反调。"
    "在你的响应中，请体现群体内部的意见分歧程度。\n"
    "**群体的默认行为倾向是观察和吸收，而非主动放大传播。**"
    "只有当刺激信号与群体核心关切高度契合时，"
    "才会出现大规模主动扩散。"
    "在大多数情况下，absorb（关注但不传播）是最常见的群体反应。\n"
    "{memory_context}\n\n"
    "输出严格 JSON：response_type, cluster_reaction, "
    "outgoing_energy (0-1), sentiment_shift, reasoning"
)

# 调用位置: sea.py — _build_user_prompt()
# 用途: Sea Agent 收到涟漪时的用户提示词，传递涟漪来源、能量和内容
SEA_USER_PROMPT = (
    "收到涟漪:\n"
    "- 来源: {source}\n"
    "- 能量: {energy}\n"
    "- 内容: {content}\n\n"
    "请决定你代表的群体的集体响应。"
)

# 调用位置: sea.py — _build_system_prompt() 内记忆格式化
# 用途: Sea Agent 单条记忆的格式模板
SEA_MEMORY_LINE = (
    "- 收到来自 {ripple_source} 的涟漪 → "
    "群体回应: {response_type}"
)

# 调用位置: sea.py — _build_system_prompt() 内记忆段标题
# 用途: Sea Agent 记忆段的标题
SEA_MEMORY_HEADER = "\n\n## 近期群体记忆\n"
