"""Tests for 'healing' trigger source and session_set_healing_fingerprint.

Covers:
- TRIGGER_SOURCES frozenset includes 'healing'
- session_create accepts 'healing' as a valid trigger_source (no ValueError)
- session_set_healing_fingerprint is callable (no error on mock pool)
- _is_valid_trigger_source correctly validates 'healing'
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TRIGGER_SOURCES constant and _is_valid_trigger_source
# ---------------------------------------------------------------------------


class _FakePool:
    """Fake asyncpg pool that captures fetchval/execute calls."""

    def __init__(self, *, return_id: uuid.UUID | None = None) -> None:
        self._return_id = return_id or uuid.uuid4()
        self.fetchval_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args: Any) -> uuid.UUID:
        self.fetchval_calls.append((sql, args))
        return self._return_id

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        return "UPDATE 1"


async def test_healing_trigger_sources_create_and_fingerprint() -> None:
    """TRIGGER_SOURCES includes 'healing'; session_create accepts/rejects; fingerprint UPDATE works."""
    from butlers.core.sessions import (
        TRIGGER_SOURCES,
        _is_valid_trigger_source,
        session_create,
        session_set_healing_fingerprint,
    )

    # Trigger source set
    assert "healing" in TRIGGER_SOURCES
    for source in ("tick", "external", "trigger", "route"):
        assert source in TRIGGER_SOURCES, f"Missing trigger source: {source}"
    assert _is_valid_trigger_source("healing") is True
    assert _is_valid_trigger_source("unknown") is False
    assert _is_valid_trigger_source("heal") is False
    assert _is_valid_trigger_source("healing:foo") is False

    # session_create accepts 'healing'; rejects invalid
    pool = _FakePool()
    result = await session_create(pool, prompt="Healing agent", trigger_source="healing", request_id=str(uuid.uuid4()))
    assert result == pool._return_id
    assert len(pool.fetchval_calls) == 1
    with pytest.raises(ValueError, match="healing"):
        await session_create(pool, prompt="Test", trigger_source="not_valid", request_id=str(uuid.uuid4()))

    # session_set_healing_fingerprint issues UPDATE; no raise on zero rows
    pool2 = _FakePool()
    session_id = uuid.uuid4()
    fingerprint = "a" * 64
    await session_set_healing_fingerprint(pool2, session_id, fingerprint)
    assert len(pool2.execute_calls) == 1
    sql, args = pool2.execute_calls[0]
    assert "healing_fingerprint" in sql
    assert session_id in args
    assert fingerprint in args
    await session_set_healing_fingerprint(pool2, uuid.uuid4(), "b" * 64)
