"""Real-Postgres coverage for park_prepared_action (bu-2jtfw.11).

A prepared action is parked silently -- unlike every other pending_actions
INSERT path (all of which route through park_pending_action and its
single-choke-point owner push, bu-mda0r), park_prepared_action must never
attempt an owner-facing push. The dedup collision case exercises the same
DB-level uniqueness park_pending_action's deduplication_key already relies on
(approvals_013's partial unique index over 'pending'/'approved'/'rejected'/
'abandoned'), here scoped to origin='prepared' rows.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.approvals.delivery_lifecycle import defer_pending_action
from butlers.modules.approvals.park import park_pending_action, park_prepared_action
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the production core + approvals migration chains."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return a clean JSONB-aware pool against the migrated database."""
    db_pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await db_pool.execute("TRUNCATE approval_events, pending_actions CASCADE")
    yield db_pool
    await db_pool.close()


def _prepared_kwargs(**overrides: object) -> dict:
    now = datetime.now(UTC)
    kwargs = dict(
        action_id=uuid.uuid4(),
        tool_name="notify",
        tool_args={"entity_id": str(uuid.uuid4()), "message": "hi", "intent": "send"},
        agent_summary="Prepared reach-out draft",
        requested_at=now,
        expires_at=now + timedelta(days=3),
        why="contact overdue for check-in",
        origin_butler="relationship",
        deduplication_key=f"relationship:prepared-reach-out:{uuid.uuid4()}",
    )
    kwargs.update(overrides)
    return kwargs


async def test_park_prepared_action_stays_default_off_and_never_pushes(pool) -> None:
    """Default-off admission preserves the silent prepared row without recovery state."""
    kwargs = _prepared_kwargs()
    admission = await park_prepared_action(pool, **kwargs)

    row = await pool.fetchrow(
        "SELECT origin, status, tool_name, expires_at, deduplication_key "
        "FROM pending_actions WHERE id = $1",
        kwargs["action_id"],
    )
    assert row is not None
    assert row["origin"] == "prepared"
    assert row["status"] == "pending"
    assert row["tool_name"] == "notify"
    assert row["deduplication_key"] == kwargs["deduplication_key"]
    assert admission.action_id == kwargs["action_id"]
    assert admission.intent_id is None

    # No approval_push_emissions reservation exists for this action -- confirms
    # the push path was never entered, not merely that the spy wasn't called.
    emission = await pool.fetchrow(
        "SELECT 1 FROM approval_push_emissions WHERE action_id = $1", kwargs["action_id"]
    )
    assert emission is None
    assert not await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM approval_delivery_intents WHERE action_id = $1)",
        kwargs["action_id"],
    )


async def test_enabled_prepared_action_atomically_uses_non_sendable_delivery_protocol(pool) -> None:
    """Enabled admission creates one standalone collapsed presentation and no push."""
    await pool.execute(
        "UPDATE approval_delivery_rollout SET admission_enabled = true WHERE singleton"
    )
    kwargs = _prepared_kwargs()

    admission = await park_prepared_action(pool, **kwargs)

    row = await pool.fetchrow(
        """
        SELECT pa.origin, pa.status, i.action_key, i.origin_butler, i.admission_mode,
               p.presentation_mode, p.state, p.next_attempt_at,
               EXISTS (
                   SELECT 1 FROM approval_delivery_cohort_members m
                    WHERE m.intent_id = i.id
               ) AS has_cohort_membership
          FROM pending_actions pa
          JOIN approval_delivery_intents i ON i.action_id = pa.id
          JOIN approval_delivery_presentations p ON p.intent_id = i.id
         WHERE pa.id = $1
        """,
        kwargs["action_id"],
    )
    assert dict(row) == {
        "origin": "prepared",
        "status": "pending",
        "action_key": admission.action_key,
        "origin_butler": "relationship",
        "admission_mode": "collapsed",
        "presentation_mode": "collapsed",
        "state": "collapsed",
        "next_attempt_at": None,
        "has_cohort_membership": False,
    }
    assert admission.presentation_key == f"{admission.action_key}:p:1"
    assert admission.cohort_key is None
    assert await pool.fetchval("SELECT count(*) FROM approval_push_emissions") == 0

    deferred = await defer_pending_action(
        pool,
        action_id=admission.action_id,
        hours=2,
        actor="owner",
    )
    assert deferred.changed is False
    assert deferred.delivery_missing is True
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM approval_delivery_presentations "
            "WHERE intent_id = $1 AND state = 'ready'",
            admission.intent_id,
        )
        == 0
    )

    ordinary_modes = []
    for ordinal in range(3):
        ordinary = await park_pending_action(
            pool,
            action_id=uuid.uuid4(),
            tool_name=f"ordinary_{ordinal}",
            tool_args={},
            agent_summary="ordinary burst accounting probe",
            requested_at=kwargs["requested_at"] + timedelta(microseconds=ordinal + 1),
            expires_at=kwargs["expires_at"],
            origin_butler="relationship",
        )
        ordinary_modes.append(ordinary.admission_mode)
    assert ordinary_modes == ["single", "single", "single"]

    rollback_kwargs = _prepared_kwargs()
    await pool.execute(
        """
        CREATE OR REPLACE FUNCTION reject_prepared_intent() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'reject prepared intent'; END $$;
        CREATE TRIGGER reject_prepared_intent
        BEFORE INSERT ON approval_delivery_intents
        FOR EACH ROW EXECUTE FUNCTION reject_prepared_intent()
        """
    )
    try:
        with pytest.raises(asyncpg.RaiseError, match="reject prepared intent"):
            await park_prepared_action(pool, **rollback_kwargs)
        assert not await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pending_actions WHERE id = $1)",
            rollback_kwargs["action_id"],
        )
    finally:
        await pool.execute(
            "DROP TRIGGER IF EXISTS reject_prepared_intent ON approval_delivery_intents; "
            "DROP FUNCTION IF EXISTS reject_prepared_intent()"
        )


async def test_dedup_key_collision_leaves_exactly_one_active_prepared_row(pool) -> None:
    """A second prepared action resolves the durable winner without double-parking."""
    dedup_key = f"relationship:prepared-reach-out:{uuid.uuid4()}"

    first = await park_prepared_action(pool, **_prepared_kwargs(deduplication_key=dedup_key))
    second = await park_prepared_action(pool, **_prepared_kwargs(deduplication_key=dedup_key))

    rows = await pool.fetch(
        "SELECT id FROM pending_actions WHERE deduplication_key = $1", dedup_key
    )
    assert len(rows) == 1
    assert second.action_id == first.action_id
    assert second.duplicate is True
