"""Real-Postgres coverage for deterministic approval-request park pushes.

The reservation query, burst counter, deferred envelope, and pending-action
clock are all database state. These tests therefore use the production core +
approvals migration chains rather than a mocked pool.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.config import ApprovalRiskTier
from butlers.db import register_jsonb_codec
from butlers.modules.approvals.gate import _make_gate_wrapper
from butlers.modules.approvals.notifications import (
    ApprovalPushRuntime,
    emit_approval_push,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision the production tables the park path writes and reads."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "approvals"],
    )


@pytest.fixture
async def approval_push_pool(migrated_db_url: str):
    """Return a clean JSONB-aware pool with approval pushes enabled."""
    pool = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await pool.execute(
        "TRUNCATE approval_push_emissions, approval_events, pending_actions, "
        "deferred_notifications CASCADE"
    )
    await pool.execute(
        "UPDATE public.approvals_policy "
        "SET quiet_start_hour = NULL, quiet_end_hour = NULL, timezone = 'UTC' "
        "WHERE id = 1"
    )
    yield pool
    await pool.close()


def _runtime(dispatch: AsyncMock) -> ApprovalPushRuntime:
    """Provide deterministic owner/secret dependencies without an LLM or broker."""
    credential_store = SimpleNamespace(resolve=AsyncMock(return_value="callback-secret"))
    return ApprovalPushRuntime(
        dispatch=dispatch,
        resolve_owner_recipient=AsyncMock(return_value="100200300"),
        credential_store=credential_store,
        dashboard_base_url="https://dashboard.example.test",
    )


async def _insert_pending_action(
    pool: asyncpg.Pool,
    *,
    requested_at: datetime,
    expires_at: datetime | None = None,
    tool_name: str = "relationship_assert_fact",
) -> dict[str, object]:
    action_id = uuid.uuid4()
    action = {
        "id": action_id,
        "tool_name": tool_name,
        "requested_at": requested_at,
        "expires_at": expires_at or requested_at + timedelta(hours=72),
        "why": "The owner requested this relationship update.",
        "blast_radius": "contact",
        "reversibility": "compensable",
    }
    await pool.execute(
        """
        INSERT INTO pending_actions
            (id, tool_name, tool_args, status, requested_at, expires_at,
             why, evidence, blast_radius, reversibility)
        VALUES ($1, $2, $3, 'pending', $4, $5, $6, $7, $8, $9)
        """,
        action_id,
        tool_name,
        {"subject": "owner", "predicate": "knows", "object": "Ada"},
        requested_at,
        action["expires_at"],
        action["why"],
        [],
        action["blast_radius"],
        action["reversibility"],
    )
    return action


async def _never_execute(**_kwargs: object) -> dict[str, object]:
    raise AssertionError("A parked gate action must not execute its original tool")


async def test_gate_park_emits_one_real_database_backed_approval_envelope(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """Park → one signed owner envelope, with a durable action-id reservation."""
    dispatch = AsyncMock()
    runtime = _runtime(dispatch)
    wrapper = _make_gate_wrapper(
        tool_name="relationship_assert_fact",
        original_fn=_never_execute,
        pool=approval_push_pool,
        expiry_hours=72,
        risk_tier=ApprovalRiskTier.MEDIUM,
        rule_precedence=(),
        butler_name="relationship",
        approval_push_runtime=runtime,
    )

    result = await wrapper(
        subject="owner",
        predicate="knows",
        object="Ada",
        _why="The owner asked to preserve this relationship fact.",
        _evidence=[],
        _blast_radius="contact",
        _reversibility="compensable",
    )

    assert result["status"] == "pending_approval"
    action_id = uuid.UUID(result["action_id"])
    dispatch.assert_awaited_once()
    envelope = dispatch.await_args.args[0]
    assert envelope["delivery"]["intent"] == "approval_request"
    assert envelope["delivery"]["recipient"] == "100200300"
    assert "Tool: relationship_assert_fact" in envelope["delivery"]["message"]
    assert (
        "Why: The owner asked to preserve this relationship fact."
        in envelope["delivery"]["message"]
    )
    assert "Blast radius: contact" in envelope["delivery"]["message"]
    assert "Reversibility: compensable" in envelope["delivery"]["message"]
    assert "Expires:" in envelope["delivery"]["message"]
    assert [action["verb"] for action in envelope["actions"]] == [
        "approve",
        "reject",
        "open_dashboard",
    ]

    emitted = await approval_push_pool.fetchrow(
        "SELECT emission_kind FROM approval_push_emissions WHERE action_id = $1",
        action_id,
    )
    assert emitted is not None
    assert emitted["emission_kind"] == "single"

    row = await approval_push_pool.fetchrow(
        """
        SELECT id, tool_name, requested_at, expires_at, why, blast_radius, reversibility
        FROM pending_actions WHERE id = $1
        """,
        action_id,
    )
    assert row is not None
    duplicate = await emit_approval_push(
        pool=approval_push_pool,
        action=dict(row),
        origin_butler="relationship",
        runtime=runtime,
        now=row["requested_at"],
    )
    assert duplicate == "duplicate"
    dispatch.assert_awaited_once()


async def test_quiet_hours_defers_push_without_changing_pending_action_expiry(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """Deferral persists the full push envelope while preserving expires_at exactly."""
    now = datetime(2026, 7, 18, 15, 30, tzinfo=UTC)  # 23:30 Asia/Singapore
    action = await _insert_pending_action(approval_push_pool, requested_at=now)
    await approval_push_pool.execute(
        """
        UPDATE public.approvals_policy
        SET quiet_start_hour = 22, quiet_end_hour = 7, timezone = 'Asia/Singapore'
        WHERE id = 1
        """
    )
    dispatch = AsyncMock()

    outcome = await emit_approval_push(
        pool=approval_push_pool,
        action=action,
        origin_butler="relationship",
        runtime=_runtime(dispatch),
        now=now,
    )

    assert outcome == "deferred"
    dispatch.assert_not_awaited()
    deferred = await approval_push_pool.fetchrow(
        "SELECT envelope, priority, status, deferred_at, deliver_at FROM deferred_notifications"
    )
    assert deferred is not None
    assert deferred["priority"] == "high"
    assert deferred["status"] == "pending"
    assert deferred["deferred_at"] == now
    assert deferred["deliver_at"] == datetime(2026, 7, 19, 0, 0, tzinfo=UTC)
    assert deferred["envelope"]["delivery"]["intent"] == "approval_request"

    stored_expiry = await approval_push_pool.fetchval(
        "SELECT expires_at FROM pending_actions WHERE id = $1",
        action["id"],
    )
    assert stored_expiry == action["expires_at"]


async def test_real_database_burst_reservation_emits_one_digest_then_collapses(
    approval_push_pool: asyncpg.Pool,
) -> None:
    """The fourth park emits the sole digest; later parks in-window do not push."""
    now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    dispatch = AsyncMock()
    runtime = _runtime(dispatch)
    outcomes: list[str] = []

    for index in range(5):
        action_now = now + timedelta(seconds=index)
        action = await _insert_pending_action(approval_push_pool, requested_at=action_now)
        outcomes.append(
            await emit_approval_push(
                pool=approval_push_pool,
                action=action,
                origin_butler="relationship",
                runtime=runtime,
                now=action_now,
            )
        )

    assert outcomes == ["delivered", "delivered", "delivered", "delivered", "collapsed"]
    assert dispatch.await_count == 4
    fourth_envelope = dispatch.await_args_list[3].args[0]
    assert fourth_envelope["delivery"]["message"].startswith("4 actions awaiting review.")
    assert fourth_envelope["actions"] == [
        {
            "verb": "open_dashboard",
            "dashboard_url": "https://dashboard.example.test/approvals",
        }
    ]

    kinds = await approval_push_pool.fetch(
        "SELECT emission_kind FROM approval_push_emissions ORDER BY created_at"
    )
    assert [row["emission_kind"] for row in kinds] == [
        "single",
        "single",
        "single",
        "burst_digest",
        "collapsed",
    ]
