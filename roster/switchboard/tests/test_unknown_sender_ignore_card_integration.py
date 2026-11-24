"""Database proof for the pre-cutover unknown-sender Ignore card (bu-p7glz9).

Runs the real ``park_pending_action`` against the production core, switchboard and
approvals chains in the ``switchboard`` schema: one pending action per entity whatever
the rollout, a delivery intent only when admission is on, and an approved Ignore that
archives the entity and adds one skip rule however often it is replayed.
"""

from __future__ import annotations

import shutil

import pytest

from butlers.roster_imports import roster_module

cards = roster_module("switchboard", "tools/identity/unknown_sender_cards")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


async def _pool(db, *, admission: bool, worker: bool):
    pool = await db(
        "core",
        "switchboard",
        "approvals",
        schemas={"core": "switchboard", "switchboard": "switchboard", "approvals": "switchboard"},
        schema="switchboard",
    )
    await pool.execute(
        "INSERT INTO approval_delivery_rollout (singleton, admission_enabled, worker_enabled) "
        "VALUES (true, $1, $2) ON CONFLICT (singleton) DO UPDATE "
        "SET admission_enabled = $1, worker_enabled = $2",
        admission,
        worker,
    )
    return pool


async def _entity(pool, **metadata):
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, metadata) "
        "VALUES ('Unknown (email chloe@x.com)', 'person', $1) RETURNING id",
        {"unidentified": True, **metadata},
    )


async def _chloe_rules(pool):
    """The live sender_address rules for the test sender (migrations seed other rules)."""
    rows = await pool.fetch(
        "SELECT condition, action, scope FROM switchboard.ingestion_rules "
        "WHERE rule_type = 'sender_address' AND deleted_at IS NULL "
        "AND condition->>'address' = 'chloe@x.com'"
    )
    return [(r["condition"], r["action"], r["scope"]) for r in rows]


async def _park(pool, entity_id, *, display_name="Chloe L"):
    return await cards.park_ignore_card(
        pool,
        entity_id=entity_id,
        channel="email",
        address="chloe@x.com",
        display_name=display_name,
        subject="Re: dinner",
    )


async def test_rollout_off_parks_one_pending_action_without_a_push(db) -> None:
    pool = await _pool(db, admission=False, worker=False)
    entity_id = await _entity(pool)

    first = await _park(pool, entity_id)
    replay = await _park(pool, entity_id, display_name="Someone Else")

    assert first is False and replay is False  # the owner keeps the plain-text notice
    rows = await pool.fetch(
        "SELECT id, status, tool_args, agent_summary, deduplication_key, blast_radius "
        "FROM pending_actions WHERE tool_name = 'identity_ignore_unknown_sender'"
    )
    assert len(rows) == 1
    (row,) = rows
    assert row["id"] == cards.ignore_action_id(entity_id)
    assert row["status"] == "pending"
    assert row["tool_args"] == {
        "entity_id": str(entity_id),
        "channel": "email",
        "address": "chloe@x.com",
        "display_name": "Chloe L",
    }
    assert row["agent_summary"] == "Ignore Chloe L <chloe@x.com>"
    assert row["blast_radius"] == "self"
    assert await pool.fetchval("SELECT count(*) FROM approval_delivery_intents") == 0


async def test_live_rollout_admits_the_card_once_and_replaces_the_notice(db) -> None:
    pool = await _pool(db, admission=True, worker=True)
    entity_id = await _entity(pool)

    assert await _park(pool, entity_id) is True
    assert await _park(pool, entity_id) is False  # a replay never pushes or texts twice

    assert (
        await pool.fetchval(
            "SELECT count(*) FROM pending_actions WHERE tool_name = 'identity_ignore_unknown_sender'"
        )
        == 1
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM approval_delivery_intents WHERE action_id = $1",
            cards.ignore_action_id(entity_id),
        )
        == 1
    )


async def test_admission_without_a_worker_keeps_the_notice(db) -> None:
    pool = await _pool(db, admission=True, worker=False)
    assert await _park(pool, await _entity(pool)) is False


async def test_approved_ignore_archives_and_adds_one_skip_rule(db) -> None:
    pool = await _pool(db, admission=False, worker=False)
    entity_id = await _entity(pool)
    await _park(pool, entity_id)
    args = {
        "entity_id": str(entity_id),
        "channel": "email",
        "address": "chloe@x.com",
        "display_name": "Chloe L",
    }

    first = await cards.ignore_unknown_sender(pool, **args)
    again = await cards.ignore_unknown_sender(pool, **args)

    assert first["rule"] == "created" and again["rule"] == "existing"
    metadata = await pool.fetchval("SELECT metadata FROM public.entities WHERE id = $1", entity_id)
    assert metadata["archived_at"] and metadata["unidentified"] is True
    assert await _chloe_rules(pool) == [({"address": "chloe@x.com"}, "skip", "global")]


async def test_a_merged_entity_is_refused_and_untouched(db) -> None:
    pool = await _pool(db, admission=False, worker=False)
    entity_id = await _entity(pool)
    await _park(pool, entity_id)
    await pool.execute(
        "UPDATE public.entities SET metadata = metadata || $2 WHERE id = $1",
        entity_id,
        {"merged_into": str(entity_id)},
    )

    result = await cards.ignore_unknown_sender(
        pool,
        entity_id=str(entity_id),
        channel="email",
        address="chloe@x.com",
        display_name="Chloe L",
    )

    assert result == {"error": "entity_not_ignorable"}
    metadata = await pool.fetchval("SELECT metadata FROM public.entities WHERE id = $1", entity_id)
    assert "archived_at" not in metadata
    assert await _chloe_rules(pool) == []
