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
# TRIGGER_SOURCES constant
# ---------------------------------------------------------------------------


def test_trigger_sources_includes_healing() -> None:
    """TRIGGER_SOURCES frozenset must include 'healing'."""
    from butlers.core.sessions import TRIGGER_SOURCES

    assert "healing" in TRIGGER_SOURCES


def test_trigger_sources_still_includes_all_original_values() -> None:
    """All original trigger sources must still be present."""
    from butlers.core.sessions import TRIGGER_SOURCES

    for source in ("tick", "external", "trigger", "route"):
        assert source in TRIGGER_SOURCES, f"Missing trigger source: {source}"


# ---------------------------------------------------------------------------
# _is_valid_trigger_source
# ---------------------------------------------------------------------------


def test_is_valid_trigger_source_healing() -> None:
    """_is_valid_trigger_source('healing') must return True."""
    from butlers.core.sessions import _is_valid_trigger_source

    assert _is_valid_trigger_source("healing") is True


def test_is_valid_trigger_source_unknown_still_false() -> None:
    """_is_valid_trigger_source('unknown') must return False."""
    from butlers.core.sessions import _is_valid_trigger_source

    assert _is_valid_trigger_source("unknown") is False
    assert _is_valid_trigger_source("heal") is False
    assert _is_valid_trigger_source("healing:foo") is False


# ---------------------------------------------------------------------------
# session_create — healing trigger source is accepted
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


async def test_session_create_accepts_healing_trigger_source() -> None:
    """session_create must NOT raise ValueError for trigger_source='healing'."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    request_id = str(uuid.uuid4())

    # Should not raise
    result = await session_create(
        pool,
        prompt="Healing agent investigating error abc123",
        trigger_source="healing",
        request_id=request_id,
    )

    assert result == pool._return_id
    assert len(pool.fetchval_calls) == 1


async def test_session_create_rejects_invalid_trigger_source() -> None:
    """session_create must raise ValueError for an unrecognised trigger_source."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    with pytest.raises(ValueError, match="Invalid trigger_source"):
        await session_create(
            pool,
            prompt="Test",
            trigger_source="not_valid",
            request_id=str(uuid.uuid4()),
        )

    assert pool.fetchval_calls == []


async def test_session_create_error_message_mentions_healing() -> None:
    """The ValueError message for invalid trigger_source must mention 'healing'."""
    from butlers.core.sessions import session_create

    pool = _FakePool()
    with pytest.raises(ValueError, match="healing"):
        await session_create(
            pool,
            prompt="Test",
            trigger_source="not_valid",
            request_id=str(uuid.uuid4()),
        )


# ---------------------------------------------------------------------------
# session_set_healing_fingerprint
# ---------------------------------------------------------------------------


async def test_session_set_healing_fingerprint_calls_execute() -> None:
    """session_set_healing_fingerprint must issue an UPDATE via pool.execute."""
    from butlers.core.sessions import session_set_healing_fingerprint

    pool = _FakePool()
    session_id = uuid.uuid4()
    fingerprint = "a" * 64  # 64-char hex fingerprint

    await session_set_healing_fingerprint(pool, session_id, fingerprint)

    assert len(pool.execute_calls) == 1
    sql, args = pool.execute_calls[0]
    assert "healing_fingerprint" in sql
    assert session_id in args
    assert fingerprint in args


async def test_session_set_healing_fingerprint_no_error_on_missing_session() -> None:
    """session_set_healing_fingerprint must not raise even if no row matched."""
    from butlers.core.sessions import session_set_healing_fingerprint

    pool = _FakePool()
    # pool.execute returns "UPDATE 0" — zero rows affected — no exception
    session_id = uuid.uuid4()
    fingerprint = "b" * 64

    # Should not raise
    await session_set_healing_fingerprint(pool, session_id, fingerprint)
