---
name: social-media
description: >
  社交媒体内容传播模拟领域，用于预判内容在不同社交平台上的扩散路径、互动走势与舆情变化。
version: "0.2.0"
use_when: >
  适用于预测内容传播路径、互动动能、情绪变化、发布时间风险，以及在小红书、抖音、微博、
  哔哩哔哩、知乎、微信等社交平台上的潜在扩散结果。最小可用输入建议至少包含内容标题与正文。
platform_labels:
  bilibili: 哔哩哔哩
  douyin: 抖音
  generic: 通用社交平台
  wechat: 微信
  weibo: 微博
  xiaohongshu: 小红书
  zhihu: 知乎
prompts:
  omniscient: prompts/omniscient.md
  tribunal: prompts/tribunal.md
  star: prompts/star.md
  sea: prompts/sea.md
domain_profile: domain-profile.md
---

# Social Media Content Propagation Skill

Predict how content spreads across social media platforms (Xiaohongshu, Douyin, Weibo, Bilibili, Zhihu, WeChat).
