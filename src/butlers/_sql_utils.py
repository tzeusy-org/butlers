"""Shared SQL query utilities for butlers."""

from __future__ import annotations


def escape_like_pattern(value: str) -> str:
    """Escape PostgreSQL LIKE metacharacters (%, _, and backslash).

    PostgreSQL's default LIKE escape character is backslash, so no
    explicit ESCAPE clause is needed.  Call this on any caller-controlled
    substring before interpolating it into a LIKE pattern parameter.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
