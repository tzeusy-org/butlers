"""RFC 0038 D6 read-back: relationship fact and contact-note reads return the taint stamp.

bu-pkuny2 AC4: entity facts (``rel_039``), narrative facts and contact notes
(memory facts, ``mem_017``) hand back the exposed goal lineage that wrote them.
bu-dhloi1.28: the same reads account each row's stamp for the broker.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.stamp_account import account_scope
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


async def test_an_exposed_fact_set_stamps_the_goal_lineage(monkeypatch):
    """fact_set is a grantable record capability: an exposed call's fact is tainted."""
    from butlers.core.capability_json import arguments_digest
    from butlers.core.goal_lineage import VerifiedLineage, lineage_scope
    from butlers.tools.relationship import facts

    monkeypatch.setattr(facts, "resolve_contact_entity_id", AsyncMock(return_value=uuid.uuid4()))
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value=row(id=uuid.uuid4(), predicate="employer", content="Acme", created_at=None)
    )
    await facts.fact_set(pool, uuid.uuid4(), "employer", "Acme")
    dark_sql, *dark_params = pool.fetchrow.await_args.args
    assert "taint_" not in dark_sql and len(dark_params) == 5

    exposed = VerifiedLineage(
        goal_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        depth=0,
        origin="owner",
        exposed=True,
        capability="fact_set",
        args_digest=arguments_digest({}),
        call_id=uuid.uuid4(),
    )
    with lineage_scope(exposed):
        await facts.fact_set(pool, uuid.uuid4(), "employer", "Acme")
    sql, *params = pool.fetchrow.await_args.args
    assert "taint_goal_id, taint_invocation_id" in sql and "$6, $7" in sql
    assert params[-2:] == [exposed.goal_id, exposed.invocation_id]


@pytest.mark.parametrize("read", [_fetch_identity_facts, _fetch_narrative_facts, note_list])
async def test_an_untainted_row_reads_back_null(read):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_row(taint_goal_id=None, taint_invocation_id=None)])
    [item] = await read(pool, uuid.uuid4())
    assert (item["taint_goal_id"], item["taint_invocation_id"]) == (None, None)


@pytest.mark.parametrize("read", [_fetch_identity_facts, _fetch_narrative_facts, note_list])
async def test_reads_account_each_rows_stamp_and_authority(read):
    writer = (uuid.uuid4(), uuid.uuid4())
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            _row(content_authority="owner", taint_goal_id=None, taint_invocation_id=None),
            _row(
                content_authority="third_party",
                taint_goal_id=writer[0],
                taint_invocation_id=writer[1],
            ),
        ]
    )
    with account_scope() as account:
        await read(pool, uuid.uuid4())
    assert (account.opened, account.owner_untainted, account.writers) == (True, True, {writer})
    sql = " ".join(pool.fetch.await_args.args[0].split())
    assert "content_authority" in sql

    # An untainted record the owner did not author is not owner-untainted.
    pool.fetch = AsyncMock(
        return_value=[
            _row(content_authority="system", taint_goal_id=None, taint_invocation_id=None)
        ]
    )
    with account_scope() as account:
        await read(pool, uuid.uuid4())
    assert (account.opened, account.owner_untainted) == (True, False)
