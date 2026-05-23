"""Unit-level regressions for relationship_assert_fact internals."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, _upsert_fact

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_PRED_HAS_EMAIL = "has-email"


async def test_supersession_insert_is_conflict_safe() -> None:
    """Concurrent supersession must not use a plain insert for the new active row."""
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": old_id,
            "src": "source-a",
            "conf": 1.0,
            "verified": False,
            "last_seen": None,
        }
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchval = AsyncMock(return_value=new_id)

    result = await _upsert_fact(
        conn,
        subject=uuid.uuid4(),
        predicate=_PRED_HAS_EMAIL,
        object="alice@example.com",
        object_kind="literal",
        src="source-b",
        conf=1.0,
        last_seen=None,
        weight=None,
        verified=False,
        primary=None,
    )

    insert_sql = conn.fetchval.call_args.args[0]
    assert result.outcome == AssertOutcome.superseded
    assert "ON CONFLICT (subject, predicate, object)" in insert_sql
    assert "WHERE validity = 'active'" in insert_sql
    assert "DO UPDATE" in insert_sql
