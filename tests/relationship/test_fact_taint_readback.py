"""RFC 0038 D6 read-back: relationship fact and contact-note reads return the taint stamp.

bu-pkuny2 AC4: entity facts (``rel_039``), narrative facts and contact notes
(memory facts, ``mem_017``) hand back the exposed goal lineage that wrote them.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.testing.records import row
from butlers.tools.relationship.notes import note_list
from butlers.tools.relationship.relationship_lookup import (
    _fetch_identity_facts,
    _fetch_narrative_facts,
)

pytestmark = pytest.mark.unit


def _row(**taint):
    return row(
        id=uuid.uuid4(),
        predicate="employer",
        object="Acme",
        object_kind="literal",
        src="email",
        conf=0.5,
        verified=False,
        primary=None,
        observed_at=None,
        last_seen=None,
        staleness_band="fresh",
        content="met at the fair",
        created_at=None,
        metadata={},
        **taint,
    )


@pytest.mark.parametrize("read", [_fetch_identity_facts, _fetch_narrative_facts, note_list])
async def test_reads_return_the_taint_stamp(read):
    goal_id, invocation_id = uuid.uuid4(), uuid.uuid4()
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[_row(taint_goal_id=goal_id, taint_invocation_id=invocation_id)]
    )
    [item] = await read(pool, uuid.uuid4())
    assert (item["taint_goal_id"], item["taint_invocation_id"]) == (
        str(goal_id),
        str(invocation_id),
    )
    sql = " ".join(pool.fetch.await_args.args[0].split())
    assert "taint_goal_id" in sql and "taint_invocation_id" in sql


@pytest.mark.parametrize("read", [_fetch_identity_facts, _fetch_narrative_facts, note_list])
async def test_an_untainted_row_reads_back_null(read):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_row(taint_goal_id=None, taint_invocation_id=None)])
    [item] = await read(pool, uuid.uuid4())
    assert (item["taint_goal_id"], item["taint_invocation_id"]) == (None, None)
