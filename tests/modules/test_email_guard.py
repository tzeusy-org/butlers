"""Unit tests for the shared email recipient guard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from butlers.modules.approvals.email_guard import check_email_recipient


def _owner_contact():
    from butlers.identity import ResolvedContact

    return ResolvedContact(
        contact_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        name="Owner",
        roles=["owner"],
    )


def _non_owner_contact():
    from butlers.identity import ResolvedContact

    return ResolvedContact(
        contact_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        name="Friend",
        roles=["contact"],
    )


def _standing_rule():
    from butlers.modules.approvals.models import ApprovalRule

    return ApprovalRule(
        id=uuid.uuid4(),
        tool_name="notify",
        arg_constraints={"recipient": {"type": "exact", "value": "friend@test.com"}},
        description="Allow friend",
        created_at=datetime.now(UTC),
    )


_COMMON_KWARGS = {
    "email_target": "friend@test.com",
    "rule_tool_name": "notify",
    "rule_match_args": {"recipient": "friend@test.com"},
    "park_tool_name": "notify",
    "park_tool_args": {"recipient": "friend@test.com", "channel": "email"},
    "park_summary": "test park summary",
}


class TestCheckEmailRecipient:
    async def test_owner_auto_approves(self) -> None:
        pool = AsyncMock()
        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=_owner_contact()),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "owner"
        pool.execute.assert_not_awaited()

    async def test_non_owner_with_rule_approves(self) -> None:
        pool = AsyncMock()
        rule = _standing_rule()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=rule),
            ),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "rule"
        assert decision.rule_id == rule.id
        assert decision.contact_desc == "known non-owner contact"
        # use_count bump
        pool.execute.assert_awaited_once()

    async def test_non_owner_without_rule_parks(self) -> None:
        pool = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_email_recipient(
                pool, session_id="test-session-123", **_COMMON_KWARGS
            )

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.action_id is not None
        assert decision.contact_desc == "known non-owner contact"
        # pending_action INSERT
        pool.execute.assert_awaited_once()
        insert_call = pool.execute.call_args
        assert "pending_actions" in insert_call.args[0]
        assert insert_call.args[5] == "test-session-123"  # session_id

    async def test_unknown_contact_without_rule_parks(self) -> None:
        pool = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.contact_desc == "unknown contact"

    async def test_unknown_contact_with_rule_approves(self) -> None:
        pool = AsyncMock()
        rule = _standing_rule()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=rule),
            ),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "rule"
        assert decision.contact_desc == "unknown contact"

    async def test_rule_match_exception_falls_through_to_park(self) -> None:
        """If match_rules raises, treat as no-rule and park."""
        pool = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(side_effect=Exception("table missing")),
            ),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
