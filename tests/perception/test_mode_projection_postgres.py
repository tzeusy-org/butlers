"""The owner.mode projector against the real perception store and sw_077 entries (PR6).

Real migrated core + Switchboard ledger under the repository's privileged test
bootstrap. Everything runs in one transaction that is rolled back, including
the activation grants this exercises (schema usage, SELECT and an RLS read
policy on ``switchboard.owner_mode_entries`` for ``perception_context_writer``,
and the ``context.mode`` source profile), so nothing persists and no runtime
identity gains a privilege outside the test.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.perception import flags
from butlers.perception.mode_projection import (
    ModeProjectionProfile,
    ModeProjectionUnavailable,
    ModeProjectionUnconfigured,
    ModeProjector,
)
from butlers.perception.store import PerceptionStore
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.perception.factories import EVIDENCE, OWNER

pytestmark = [pytest.mark.integration, pytest.mark.db]

MAX_AGE = timedelta(hours=6)
PROFILE = ModeProjectionProfile(subject=OWNER, evidence_ref=EVIDENCE, max_age=MAX_AGE)

_SEED = (
    "INSERT INTO switchboard.owner_mode_entries "
    "(mode, version, since, until, cause, exited_at, owner_action_id) "
    "VALUES ($1, 1, now() - interval '3 hours', $2, $3, $4, gen_random_uuid())"
)


def _bootstrap_url(container):
    name = migration_db_name()
    create_migrated_test_db(
        container,
        name,
        ["core", "switchboard"],
        schemas={"core": "switchboard", "switchboard": "switchboard"},
    )
    return migration_bootstrap_db_url(container, name)


async def _as(conn, role, call):
    """Run ``call`` as ``role`` inside a savepoint.

    A refusal rolls the savepoint back, which also undoes the role; a success
    resets it before the savepoint is released.
    """
    async with conn.transaction():
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        result = await call()
        await conn.execute("RESET ROLE")
        return result


async def _exercise(url):
    conn = await asyncpg.connect("postgresql://" + url.split("://", 1)[1])
    await register_jsonb_codec(conn)
    tx = conn.transaction()
    await tx.start()
    try:
        now = await conn.fetchval("SELECT now()")
        await conn.execute(
            "INSERT INTO public.entities(id, canonical_name, entity_type, roles) "
            "VALUES ($1, 'opaque test owner', 'person', ARRAY['owner'])",
            OWNER,
        )
        # Seed history the trigger only admits over time (ended and exited
        # entries), with triggers off for this transaction only.
        await conn.execute("SET LOCAL session_replication_role = replica")
        for mode, until, cause, exited_at in (
            ("travel", None, "owner_declared", None),
            ("focus", now + timedelta(hours=2), "owner_confirmed", None),
            ("asleep", now, "owner_declared", None),  # ends exactly now: ended
            ("sick", None, "owner_declared", now - timedelta(minutes=5)),  # exited
            ("hosting", now - timedelta(hours=1), "owner_declared", None),  # past its end
        ):
            await conn.execute(_SEED, mode, until, cause, exited_at)
        await conn.execute("SET LOCAL session_replication_role = origin")

        projector = ModeProjector(conn, PROFILE)
        # Without the activation grants the context writer cannot read the entries.
        with pytest.raises(ModeProjectionUnavailable):
            await _as(conn, "perception_context_writer", projector.project)
        # Activation grants, rolled back with the transaction.
        await conn.execute("GRANT USAGE ON SCHEMA switchboard TO perception_context_writer")
        await conn.execute(
            "GRANT SELECT ON switchboard.owner_mode_entries TO perception_context_writer"
        )
        await conn.execute(
            "CREATE POLICY owner_mode_entries_projection ON switchboard.owner_mode_entries "
            "FOR SELECT TO perception_context_writer USING (true)"
        )
        # The context.mode profile is still unconfigured: the store refuses.
        with pytest.raises(ModeProjectionUnconfigured):
            await _as(conn, "perception_context_writer", projector.project)
        await conn.execute(
            "UPDATE perception_private.sources SET subjects = ARRAY[$1::uuid], "
            "max_age_seconds = $2, evidence_sources = ARRAY['context.mode'] "
            "WHERE source = 'context.mode'",
            OWNER,
            int(MAX_AGE.total_seconds()),
        )
        await conn.execute(
            "INSERT INTO perception_private.source_refs(id, source, ref_type) "
            "VALUES ($1, 'context.mode', 'evidence')",
            EVIDENCE,
        )
        # Only its own writer projects, even with every grant in place.
        with pytest.raises(ModeProjectionUnavailable):
            await _as(conn, "perception_dots_writer", projector.project)

        receipt = await _as(conn, "perception_context_writer", projector.project)
        assert receipt.get("status") != "conflict"
        read = await _as(
            conn,
            "perception_owner_reader",
            lambda: PerceptionStore(conn).read("owner.mode", OWNER, as_of=now),
        )
        assert read["state"] == "known" and read["generation"] == 1
        # Exited, ended-exactly-now and expired entries are not projected.
        assert read["value"] == ["focus", "travel"]
        # The earliest finite end (focus, 2 h) is sooner than now + 6 h.
        assert datetime.fromisoformat(read["fresh_until"]) == now + timedelta(hours=2)
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM public.situation_slots WHERE kind = 'owner.mode'"
            )
            == 0
        )

        # The same projection at the same instant replays its original receipt.
        again = await _as(conn, "perception_context_writer", projector.project)
        assert again == receipt
    finally:
        await tx.rollback()
        await conn.close()


def test_the_projector_writes_owner_mode_from_the_owner_entries_alone(
    postgres_container, monkeypatch
):
    monkeypatch.setattr(flags, "MODE_PROJECTION_LIVE", True)
    asyncio.run(_exercise(_bootstrap_url(postgres_container)))
