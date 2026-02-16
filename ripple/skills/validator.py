# validator.py
# =============================================================================
# Skill 校验错误定义。
#
# CAS 参数由全视者 Agent 在运行时动态决定，无需静态校验。
# 本模块仅保留错误码和异常类，供 SkillManager 使用。
# =============================================================================

from __future__ import annotations


# -----------------------------------------------------------------------------
# 错误码
# -----------------------------------------------------------------------------
SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
SKILL_SCHEMA_INVALID = "SKILL_SCHEMA_INVALID"


class SkillValidationError(Exception):
    """Skill 校验错误 — 携带错误码与诊断信息。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")
