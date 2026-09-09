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
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.approvals.park import park_prepared_action
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
        deduplication_key=f"relationship:prepared-reach-out:{uuid.uuid4()}",
    )
    kwargs.update(overrides)
    return kwargs


async def test_park_prepared_action_inserts_origin_prepared_and_never_pushes(pool) -> None:
    """The row lands with origin='prepared', status='pending', and no push is attempted."""
    kwargs = _prepared_kwargs()
    with patch(
        "butlers.modules.approvals.park.emit_approval_push", new=AsyncMock()
    ) as spy_push:
        await park_prepared_action(pool, **kwargs)
        spy_push.assert_not_called()

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

    # No approval_push_emissions reservation exists for this action -- confirms
    # the push path was never entered, not merely that the spy wasn't called.
    emission = await pool.fetchrow(
        "SELECT 1 FROM approval_push_emissions WHERE action_id = $1", kwargs["action_id"]
    )
    assert emission is None


async def test_dedup_key_collision_leaves_exactly_one_active_prepared_row(pool) -> None:
    """A second prepared action for the same concern cannot double-park."""
    dedup_key = f"relationship:prepared-reach-out:{uuid.uuid4()}"

    await park_prepared_action(pool, **_prepared_kwargs(deduplication_key=dedup_key))

    with pytest.raises(asyncpg.UniqueViolationError):
        await park_prepared_action(pool, **_prepared_kwargs(deduplication_key=dedup_key))

    rows = await pool.fetch(
        "SELECT id FROM pending_actions WHERE deduplication_key = $1", dedup_key
    )
    assert len(rows) == 1
