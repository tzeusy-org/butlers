"""Tests for trigger-source validation and session_set_healing_fingerprint.

Covers:
- TRIGGER_SOURCES frozenset includes 'healing'
- session_create accepts 'deadline:<task-name>' as a valid trigger_source
- session_create accepts 'healing' as a valid trigger_source (no ValueError)
- session_set_healing_fingerprint is callable (no error on mock pool)
- _is_valid_trigger_source correctly validates 'healing' and 'deadline:<task-name>'
"""

from __future__ import annotations

import uuid

import pytest

from butlers.testing.fake_pool import fake_pool

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# TRIGGER_SOURCES constant and _is_valid_trigger_source
# ---------------------------------------------------------------------------


async def test_healing_trigger_sources_create_and_fingerprint() -> None:
    """Trigger validation accepts healing/deadline sources; fingerprint UPDATE works."""
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
    assert _is_valid_trigger_source("deadline:release-monitoring") is True
    assert _is_valid_trigger_source("deadline:") is False
    assert _is_valid_trigger_source("unknown") is False
    assert _is_valid_trigger_source("heal") is False
    assert _is_valid_trigger_source("healing:foo") is False

    # session_create accepts 'healing' and 'deadline:<task-name>'; rejects invalid
    session_id = uuid.uuid4()
    pool = fake_pool(fetchval=session_id, execute="SELECT 1")
    result = await session_create(
        pool, prompt="Healing agent", trigger_source="healing", request_id=str(uuid.uuid4())
    )
    assert result == session_id
    assert pool.fetchval.await_count == 1

    deadline_id = await session_create(
        pool,
        prompt="Deadline agent",
        trigger_source="deadline:release-monitoring",
        request_id=str(uuid.uuid4()),
    )
    assert deadline_id == session_id
    assert pool.fetchval.await_count == 2

    with pytest.raises(ValueError, match="healing"):
        await session_create(
            pool, prompt="Test", trigger_source="not_valid", request_id=str(uuid.uuid4())
        )

    # session_set_healing_fingerprint issues UPDATE; no raise on zero rows
    pool2 = fake_pool(execute="UPDATE 1")
    fingerprint = "a" * 64
    await session_set_healing_fingerprint(pool2, session_id, fingerprint)
    pool2.execute.assert_awaited_once()
    sql, *args = pool2.execute.await_args.args
    assert "SET healing_fingerprint = $2" in sql
    assert args == [session_id, fingerprint]
    pool2.execute.return_value = "UPDATE 0"
    await session_set_healing_fingerprint(pool2, uuid.uuid4(), "b" * 64)
