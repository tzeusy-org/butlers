"""Owner-ingress engagement hook, exercised directly at its surviving seam.

Only owner-authored ingress marks insight engagement and records the daily
owner-ingress rollup; any other sender touches neither, and a hook failure
never propagates to the ingress path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.identity import ResolvedContact
from butlers.tools.switchboard.insight.engagement import record_owner_ingress_engagement

pytestmark = pytest.mark.unit

_RECEIVED_AT = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)
_POOL = object()


def _patch_hooks(contact: ResolvedContact | None, *, engagement_side_effect=None):
    resolve = AsyncMock(return_value=contact)
    engagement = AsyncMock(return_value=1, side_effect=engagement_side_effect)
    rollup = AsyncMock()
    patches = (
        patch("butlers.identity.resolve_contact_by_channel", resolve),
        patch("butlers.tools.switchboard.insight.broker.check_and_update_engagement", engagement),
        patch("butlers.core.attention_ledger.record_owner_ingress_rollup", rollup),
    )
    return patches, resolve, engagement, rollup


async def test_owner_sender_marks_engagement_and_records_rollup():
    owner = ResolvedContact(name="Owner", roles=["owner"], entity_id=None)
    patches, resolve, engagement, rollup = _patch_hooks(owner)
    with patches[0], patches[1], patches[2]:
        await record_owner_ingress_engagement(
            _POOL, source="telegram", sender_value="1", received_at=_RECEIVED_AT
        )

    resolve.assert_awaited_once_with(_POOL, "telegram", "1")
    engagement.assert_awaited_once_with(_POOL)
    rollup.assert_awaited_once_with(_POOL, occurred_at=_RECEIVED_AT)


@pytest.mark.parametrize(
    ("contact", "source", "sender_value"),
    [
        (ResolvedContact(name="Chloe", roles=[], entity_id=None), "telegram", "2"),
        (None, "telegram", "3"),
        (None, "telegram", None),
        (None, None, "4"),
    ],
    ids=["non-owner", "unresolved", "no-sender", "no-source"],
)
async def test_non_owner_or_unresolved_sender_touches_neither_hook(contact, source, sender_value):
    patches, resolve, engagement, rollup = _patch_hooks(contact)
    with patches[0], patches[1], patches[2]:
        await record_owner_ingress_engagement(
            _POOL, source=source, sender_value=sender_value, received_at=_RECEIVED_AT
        )

    if not (source and sender_value):
        resolve.assert_not_awaited()
    engagement.assert_not_awaited()
    rollup.assert_not_awaited()


async def test_hook_failure_is_swallowed():
    owner = ResolvedContact(name="Owner", roles=["owner"], entity_id=None)
    patches, _, engagement, rollup = _patch_hooks(
        owner, engagement_side_effect=RuntimeError("DB down")
    )
    with patches[0], patches[1], patches[2]:
        await record_owner_ingress_engagement(
            _POOL, source="telegram", sender_value="1", received_at=_RECEIVED_AT
        )

    engagement.assert_awaited_once()
    rollup.assert_not_awaited()
