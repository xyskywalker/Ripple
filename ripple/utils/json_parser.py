"""Unified JSON parsing from LLM output.

Handles common LLM response patterns: plain JSON, markdown code blocks,
JSON with surrounding text.
"""

import json
import re
from typing import Any, Dict


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

    # Try 1: direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try 2: extract from markdown code block
    code_block_match = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if code_block_match:
        try:
            result = json.loads(code_block_match.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Try 3: find first { ... } block
    brace_match = re.search(r'\{.*\}', text, re.DOTALL)
    if brace_match:
        try:
            result = json.loads(brace_match.group(0))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No valid JSON found in LLM output: {text[:200]}")
