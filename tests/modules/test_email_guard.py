"""Unit tests for the shared email recipient guard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from butlers.modules.approvals.email_guard import check_email_recipient, check_recipient


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
    async def test_owner_primary_email_auto_approves(self) -> None:
        """Owner send to primary email address is auto-approved."""
        pool = AsyncMock()
        # is_primary=True for the targeted address
        pool.fetchrow = AsyncMock(return_value={"primary": True})
        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=_owner_contact()),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "owner"
        pool.execute.assert_not_awaited()

    async def test_owner_non_primary_email_parks(self) -> None:
        """Owner send to a non-primary email address is parked for approval.

        This is the regression test for bu-jwby9: an owner with both a personal
        (primary) and a work (non-primary) email must NOT auto-approve sends to
        the work address.  The non-primary address must go through the normal
        standing-rules / parking flow.
        """
        owner = _owner_contact()
        pool = AsyncMock()
        # Targeted address is NOT the primary one
        pool.fetchrow = AsyncMock(return_value={"primary": False})
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=owner),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.action_id is not None
        # contact_desc reflects owner is still recognised as a known contact
        assert decision.contact_desc == "known non-owner contact"
        # pending_action INSERT must have been called
        pool.execute.assert_awaited_once()
        insert_call = pool.execute.call_args
        assert "pending_actions" in insert_call.args[0]

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
        session_id = uuid.uuid4()
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
                pool, session_id=str(session_id), **_COMMON_KWARGS
            )

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.action_id is not None
        assert decision.contact_desc == "known non-owner contact"
        # pending_action INSERT
        pool.execute.assert_awaited_once()
        insert_call = pool.execute.call_args
        assert "pending_actions" in insert_call.args[0]
        assert insert_call.args[5] == session_id

    async def test_invalid_session_id_is_not_written_to_pending_action(self) -> None:
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
            decision = await check_email_recipient(pool, session_id="not-a-uuid", **_COMMON_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
        insert_call = pool.execute.call_args
        assert "pending_actions" in insert_call.args[0]
        assert insert_call.args[5] is None

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


# ---------------------------------------------------------------------------
# emit_approvals_event 'created' emission tests [bu-jg0kt]
# ---------------------------------------------------------------------------


class TestEmailGuardEmitsCreatedEvent:
    """email_guard.py must emit 'created' approval WS events when parking actions."""

    async def test_no_rule_park_emits_created(self) -> None:
        """No standing rule: check_email_recipient emits kind='created' with status='pending'."""
        from unittest.mock import MagicMock

        pool = AsyncMock()
        mock_emit = MagicMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.api.routers.approvals.emit_approvals_event",
                new=mock_emit,
            ),
        ):
            decision = await check_email_recipient(pool, butler_name="home", **_COMMON_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args
        assert call_kwargs.args[0] == "created"
        assert call_kwargs.kwargs.get("butler") == "home"
        assert call_kwargs.kwargs.get("tool_name") == "notify"
        assert call_kwargs.kwargs.get("status") == "pending"

    async def test_context_mismatch_park_emits_created(self) -> None:
        """Context mismatch park: check_email_recipient emits kind='created'."""
        from unittest.mock import MagicMock

        pool = AsyncMock()
        mock_emit = MagicMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ),
            patch(
                "butlers.api.routers.approvals.emit_approvals_event",
                new=mock_emit,
            ),
        ):
            decision = await check_email_recipient(
                pool,
                butler_name="home",
                msg_context="personal",
                **_COMMON_KWARGS,
            )

        assert decision.allowed is False
        assert decision.reason == "parked"
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args
        assert call_kwargs.args[0] == "created"
        assert call_kwargs.kwargs.get("butler") == "home"
        assert call_kwargs.kwargs.get("status") == "pending"

    async def test_emit_created_survives_broker_failure(self) -> None:
        """emit_approvals_event raising must not prevent email guard from parking the action."""
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
            patch(
                "butlers.api.routers.approvals.emit_approvals_event",
                side_effect=RuntimeError("broker down"),
            ),
        ):
            decision = await check_email_recipient(pool, butler_name="home", **_COMMON_KWARGS)

        # Guard must still park the action even when emit raises
        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.action_id is not None


_TELEGRAM_KWARGS = {
    "channel": "telegram",
    "target": "900800700",
    "rule_tool_name": "notify",
    "rule_match_args": {"recipient": "900800700", "channel": "telegram"},
    "park_tool_name": "notify",
    "park_tool_args": {"recipient": "900800700", "channel": "telegram"},
    "park_summary": "test park summary",
}


class TestCheckRecipient:
    """Channel-general recipient guard used by notify() for non-email channels."""

    async def test_owner_telegram_auto_approves_without_primacy(self) -> None:
        """Owner-role telegram target auto-approves on any active channel (no primacy)."""
        pool = AsyncMock()
        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=_owner_contact()),
        ):
            decision = await check_recipient(pool, **_TELEGRAM_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "owner"
        # Owner bypass must not park or check standing rules.
        pool.execute.assert_not_awaited()

    async def test_owner_via_definer_fallback_auto_approves(self) -> None:
        """When direct resolution fails, the SECURITY DEFINER owner fallback still approves."""
        pool = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=AsyncMock(return_value=(_owner_contact(), False)),
            ),
        ):
            decision = await check_recipient(pool, **_TELEGRAM_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "owner"

    async def test_non_owner_without_rule_parks(self) -> None:
        """Known non-owner telegram target without a standing rule is parked (fail-closed)."""
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
            decision = await check_recipient(pool, **_TELEGRAM_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.action_id is not None
        pool.execute.assert_awaited()

    async def test_standing_rule_permits_non_owner(self) -> None:
        """A matching standing rule auto-approves a non-owner telegram send."""
        pool = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=_standing_rule()),
            ),
        ):
            decision = await check_recipient(pool, **_TELEGRAM_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "rule"

    async def test_unresolvable_target_parks(self) -> None:
        """An unresolvable target (no contact, no owner fallback, no rule) is parked."""
        pool = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_recipient(pool, **_TELEGRAM_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
