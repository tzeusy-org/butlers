"""Runtime-attention page text must give the owner a door they can open.

A dashboard-relative door is not a link in a chat client, so the transport
anchors it at the configured dashboard origin (bu-3osotc).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
