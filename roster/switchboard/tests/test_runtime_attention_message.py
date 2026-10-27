"""Runtime-attention page text must give the owner a door they can open.

A dashboard-relative door is not a link in a chat client, so the transport
anchors it at the configured dashboard origin (bu-3osotc).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.testing.fake_pool import AsyncCtx
from butlers.tools.switchboard.runtime_attention.outbox import OutboxEpisode
from butlers.tools.switchboard.runtime_attention.worker import build_messenger_transport
from butlers.tools.switchboard.transport import TransportOutcome

pytestmark = [pytest.mark.unit]


def _breaker_episode() -> OutboxEpisode:
    now = datetime.now(UTC)
    return OutboxEpisode(
        id=uuid.uuid4(),
        source="model_breaker",
        source_snapshot={},
        payload={"classification": "model_breaker_open", "door": "/settings/models?highlight=e1"},
        lifecycle_state="sending",
        claim_token=uuid.uuid4(),
        claim_epoch=1,
        delivery_lease_epoch=1,
        claimed_at=now,
        claim_expires_at=now,
    )


async def _recipient() -> str:
    return "owner-recipient"


@pytest.mark.parametrize(
    ("dashboard_base_url", "expected_door"),
    (
        (
            "https://dashboard.example/butlers-dev/",
            "https://dashboard.example/butlers-dev/settings/models?highlight=e1",
        ),
        (None, "/settings/models?highlight=e1"),
    ),
)
async def test_transport_anchors_the_door_at_the_dashboard_origin(
    dashboard_base_url: str | None, expected_door: str
) -> None:
    with patch(
        "butlers.tools.switchboard.runtime_attention.worker.deliver",
        new_callable=AsyncMock,
        return_value={"status": "sent"},
    ) as deliver_mock:
        transport = build_messenger_transport(
            MagicMock(), resolve_recipient=_recipient, dashboard_base_url=dashboard_base_url
        )
        result = await transport(_breaker_episode())

    assert result.outcome is TransportOutcome.CONFIRMED
    assert deliver_mock.await_args.kwargs["notify_request"]["delivery"]["message"] == (
        f"Runtime attention: model_breaker_open. Open {expected_door} to review."
    )


@pytest.mark.parametrize("pending", [False, True])
async def test_repository_claim_uses_shared_fence_inside_committed_role_transaction(pending):
    """Exercise the real helper/repository seam; migrated PG owns authority proof."""
    from contextlib import asynccontextmanager

    from butlers.tools.switchboard.runtime_attention.outbox import (
        CLAIM_LEASE_SECONDS,
        RuntimeAttentionOutbox,
        ServiceLease,
    )

    now = datetime.now(UTC)
    key, token = uuid.uuid4(), uuid.uuid4()
    lease = ServiceLease(token=uuid.uuid4(), epoch=7, holder="worker", expires_at=now)
    row = {
        "id": key,
        "source": "model_breaker",
        "source_snapshot": {},
        "payload": {},
        "lifecycle_state": "sending",
        "claim_token": token,
        "claim_epoch": 2,
        "delivery_lease_epoch": lease.epoch,
        "claimed_at": now,
        "claim_expires_at": now,
    }
    events = []
    active = False

    @asynccontextmanager
    async def transaction():
        nonlocal active
        active = True
        events.append("begin")
        try:
            yield
        finally:
            active = False
        events.append("commit")

    async def fetch(sql, *args):
        assert active and events[-1] in {"role", "select"}
        if sql.startswith("SELECT"):
            events.append("select")
            return [{"id": key}] if pending else []
        events.append("update")
        return [row]

    async def execute(sql):
        assert active
        assert sql == 'SET LOCAL ROLE "butler_switchboard_rw"'
        events.append("role")

    connection = MagicMock(is_in_transaction=lambda: active, transaction=transaction)
    connection.execute = AsyncMock(side_effect=execute)
    connection.fetch = AsyncMock(side_effect=fetch)
    connection.fetchrow = AsyncMock(return_value=row if pending else None)

    pool = MagicMock(acquire=lambda: AsyncCtx(connection))
    repository = RuntimeAttentionOutbox(pool, instance_id="worker")
    with patch("butlers.tools.switchboard.runtime_attention.outbox.uuid.uuid4", return_value=token):
        episode = await repository.claim_next_pending(lease)
    assert events == ["begin", "role", "select", *(["update"] if pending else []), "commit"]
    connection.fetchrow.assert_not_awaited()
    if pending:
        assert episode == OutboxEpisode(**row)
        sql, *bound = connection.fetch.await_args.args
        assert {token, lease.epoch, "worker", CLAIM_LEASE_SECONDS} <= set(
            value for value in bound if not isinstance(value, list)
        )
        assert "claim_epoch=q.claim_epoch+1" in sql
        assert "q.lifecycle_state='pending'" in sql
        assert "q.next_attempt_at<=now()" in sql
        assert "leased_until" not in sql and "leased_by" not in sql
    else:
        assert episode is None
        assert connection.fetch.await_count == 1
