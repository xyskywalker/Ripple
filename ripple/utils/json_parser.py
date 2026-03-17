"""Unified JSON parsing from LLM output.

Handles common LLM response patterns: plain JSON, markdown code blocks,
JSON with surrounding text.
"""

import json
import re
from typing import Any, Dict

import yaml


def _try_parse_mapping(text: str) -> Dict[str, Any] | None:
    """尝试将文本解析为字典。 / Try parsing a text blob into a mapping.

    先走严格 JSON，再退化到 YAML 兼容模式，以吸收尾逗号、未转义换行等
    常见 LLM 输出噪音。
    / First attempt strict JSON, then fall back to YAML-compatible parsing to
    absorb common LLM noise such as trailing commas and raw newlines.
    """
    for loader in (json.loads, yaml.safe_load):
        try:
            parsed = loader(text)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _extract_fenced_blocks(text: str) -> list[str]:
    """提取 fenced code block 内容。 / Extract fenced code block payloads."""
    blocks: list[str] = []
    for match in re.finditer(r"```(?:json|yaml)?\s*\n(.*?)\n\s*```", text, re.DOTALL | re.IGNORECASE):
        candidate = match.group(1).strip()
        if candidate:
            blocks.append(candidate)
    return blocks


def _extract_balanced_object(text: str) -> str | None:
    """提取第一个平衡的大括号对象。 / Extract the first balanced brace object.

    该扫描器会正确跳过字符串中的大括号，适合从“说明文字 + JSON”混合输出中
    抽取核心对象。
    / This scanner ignores braces inside quoted strings and is suitable for
    mixed outputs such as prose + JSON.
    """
    start = -1
    depth = 0
    in_string = False
    escaped = False
    quote_char = ""

    for idx, char in enumerate(text):
        if start == -1:
            if char == "{":
                start = idx
                depth = 1
            continue

        if in_string:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == quote_char:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            quote_char = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1].strip()
    return None


def _candidate_texts(text: str) -> list[str]:
    """生成解析候选。 / Build parsing candidates from raw LLM output."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(value: str | None) -> None:
        candidate = str(value or "").strip()
        if candidate and candidate not in seen:
            candidates.append(candidate)
            seen.add(candidate)

    _add(text)
    for block in _extract_fenced_blocks(text):
        _add(block)

    balanced = _extract_balanced_object(text)
    _add(balanced)
    if balanced:
        for block in _extract_fenced_blocks(balanced):
            _add(block)

    return candidates


def parse_json_from_llm(raw: str) -> Dict[str, Any]:
    """Parse JSON from LLM output, handling common wrapping patterns.

    Supports:
    - Plain JSON: '{"key": "value"}'
    - Markdown code blocks: '```json\\n{"key": "value"}\\n```'
    - JSON with surrounding text

    Args:
        raw: Raw LLM output string.

    Returns:
        Parsed JSON as dict.

    Raises:
        ValueError: If no valid JSON found.
    """
    if not raw or not raw.strip():
        raise ValueError("Empty input")

    text = raw.strip()
    for candidate in _candidate_texts(text):
        result = _try_parse_mapping(candidate)
        if result is not None:
            return result

    raise ValueError(f"No valid JSON found in LLM output: {text[:200]}")
