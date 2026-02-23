"""Tests for unified JSON parsing from LLM output."""

import pytest
from ripple.utils.json_parser import parse_json_from_llm


class TestParseJsonFromLlm:
    def test_plain_json(self):
        result = parse_json_from_llm('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_codeblock(self):
        result = parse_json_from_llm('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self):
        result = parse_json_from_llm('Here is the result:\n```json\n{"key": "value"}\n```\nDone.')
        assert result == {"key": "value"}

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError):
            parse_json_from_llm("not json at all")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_json_from_llm("")
