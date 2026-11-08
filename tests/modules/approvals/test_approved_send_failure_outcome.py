"""Real-Postgres: an approved send's failure reaches the record content-free (bu-dhloi1.184).

Runs in CI's integration shards only (the author lane has no Docker).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.modules.approvals.delivery_failure import SendFailure
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.park import park_prepared_action

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_ARGS = {"channel": "telegram", "recipient": "42", "message": "Secret words", "intent": "send"}


@pytest.fixture
async def approvals_pool(db):
    return await db("core", "approvals")


async def _approved(pool) -> uuid.UUID:
    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    await park_prepared_action(
        pool,
        action_id=action_id,
        tool_name="notify",
        tool_args=_ARGS,
        agent_summary="send",
        requested_at=now,
        expires_at=now + timedelta(days=3),
        origin_butler="relationship",
        deduplication_key=f"relationship:test:{action_id}",
    )
    await pool.execute(
        "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 WHERE id = $4",
        ActionStatus.APPROVED.value,
        "human:owner",
        now,
        action_id,
    )
    return action_id


async def _run(pool, action_id, tool_fn):
    return await execute_approved_action(
        pool=pool,
        action_id=action_id,
        tool_name="notify",
        tool_args=_ARGS,
        tool_fn=tool_fn,
        origin_butler="relationship",
    )


async def test_provider_rejection_is_recorded_executed_and_distinct(approvals_pool) -> None:
    action_id = await _approved(approvals_pool)
    rejected = {
        "status": "delivery_rejected",
        "delivery_confirmed": False,
        "failure_code": "provider_rejected",
    }

    result = await _run(approvals_pool, action_id, AsyncMock(return_value=rejected))

    assert result.success is True
    row = await approvals_pool.fetchrow(
        "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
    )
    assert row["status"] == ActionStatus.EXECUTED.value
    assert row["execution_result"]["result"] == rejected
    event = await approvals_pool.fetchrow(
        "SELECT event_metadata FROM approval_events WHERE action_id = $1 "
        "AND event_type = 'action_execution_succeeded'",
        action_id,
    )
    assert event["event_metadata"]["failure_code"] == "provider_rejected"
    assert event["event_metadata"]["outcome"] == "rejected"


async def test_retryable_send_failure_stays_approved_with_a_code_and_no_content(
    approvals_pool,
) -> None:
    action_id = await _approved(approvals_pool)

    result = await _run(
        approvals_pool,
        action_id,
        AsyncMock(side_effect=SendFailure("recipient_unavailable")),
    )

    assert result.success is False
    assert result.failure_code == "recipient_unavailable"
    assert result.error == "send_failed:recipient_unavailable"
    row = await approvals_pool.fetchrow(
        "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
    )
    assert row["status"] == ActionStatus.APPROVED.value
    assert row["execution_result"] is None
    event = await approvals_pool.fetchrow(
        "SELECT reason, event_metadata FROM approval_events WHERE action_id = $1 "
        "AND event_type = 'action_execution_failed'",
        action_id,
    )
    assert event["reason"] == "send_failed:recipient_unavailable"
    assert event["event_metadata"]["failure_code"] == "recipient_unavailable"


async def test_raw_provider_text_never_reaches_the_record(approvals_pool) -> None:
    action_id = await _approved(approvals_pool)

    result = await _run(
        approvals_pool,
        action_id,
        AsyncMock(side_effect=RuntimeError("chat not found for chat_id='@Butlerly'")),
    )

    assert result.error == "send_failed"
    event = await approvals_pool.fetchrow(
        "SELECT reason FROM approval_events WHERE action_id = $1 "
        "AND event_type = 'action_execution_failed'",
        action_id,
    )
    assert "Butlerly" not in event["reason"]


async def test_uncertain_send_is_executed_outcome_unknown_and_not_retryable(
    approvals_pool,
) -> None:
    action_id = await _approved(approvals_pool)
    uncertain = {
        "status": "failed",
        "error": "timed out talking to chat_id='@Butlerly'",
        "transport": {"outcome": "uncertain", "error_detail": "transport_timeout"},
    }

    result = await _run(approvals_pool, action_id, AsyncMock(return_value=uncertain))

    assert result.success is True
    row = await approvals_pool.fetchrow(
        "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
    )
    assert row["status"] == ActionStatus.EXECUTED.value
    assert row["execution_result"]["result"] == {
        "status": "outcome_unknown",
        "delivery_confirmed": False,
    }
