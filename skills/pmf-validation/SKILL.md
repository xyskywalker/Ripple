---
name: pmf-validation
version: "0.2.0"
description: "Agent-Native PMF 产品市场匹配验证领域，用多 Agent 模拟替代高成本真实流量测试，预判产品与市场的匹配程度。"
use_when: >
  适用于评估产品市场匹配度、上线准备度、目标人群共鸣、核心价值主张，以及多方案优先级比较。
  可用于单方案评估、多方案对比、增长假设检查，以及发布前的人群反馈预判。最小可用输入建议至少包含
  产品概念、目标用户和核心使用场景。
platform_labels:
  douyin: 抖音
  weibo: 微博
  xiaohongshu: 小红书
channel_labels:
  algorithm-ecommerce: 算法电商
  app-distribution: 应用分发
  content-seeding: 内容种草
  enterprise-sales: 企业销售
  generic: 通用渠道
  offline-distribution: 线下分销
  offline-experience: 线下体验
  search-ecommerce: 搜索电商
  social-ecommerce: 社交电商
vertical_labels:
  consumer-electronics: 消费电子
  fashion-retail: 时尚零售
  fmcg: 快消品
  mobile-app: 移动应用
  saas: SaaS 软件
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
