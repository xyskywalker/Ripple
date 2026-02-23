---
name: pmf-validation
version: "0.2.0"
description: "Agent-Native PMF (Product-Market Fit) Validation Engine — 用 LLM 多 Agent 模拟替代真实流量测试，预判产品市场匹配度。"
prompts:
  omniscient: prompts/omniscient.md
  tribunal: prompts/tribunal.md
  star: prompts/star.md
  sea: prompts/sea.md
domain_profile: domain-profile.md
---

# PMF Validation Skill

Agent-Native 产品市场验证引擎。支持单方案评估和多方案对比（类 AB 测试），
通过微观传播模拟（RIPPLE）+ 宏观合议辩论（DELIBERATE）双层验证，
输出三层嵌套报告：执行摘要 → 结构化评分卡 → 证据追溯。
