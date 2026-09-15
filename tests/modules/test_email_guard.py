"""Unit tests for the shared email recipient guard."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.approvals.email_guard import check_email_recipient, check_recipient
from butlers.testing.approval_parking_fake import record_pending_action


@pytest.fixture(autouse=True)
def _use_mock_pool_park_recorder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "butlers.modules.approvals.email_guard.park_pending_action",
        record_pending_action,
    )


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
    "butler_name": "messenger",
}


class TestCheckEmailRecipient:
    async def test_owner_primary_email_auto_approves(self) -> None:
        """Owner send to primary email address is auto-approved."""
        pool = AsyncMock()
        owner = _owner_contact()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=owner),
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=AsyncMock(return_value=(owner, True)),
            ),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "owner"
        pool.execute.assert_not_awaited()

    async def test_owner_secondary_email_auto_approves(self) -> None:
        """Any uniquely resolved active owner email address auto-approves."""
        owner = _owner_contact()
        pool = AsyncMock()
        owner_fallback = AsyncMock(return_value=(owner, False))
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=owner_fallback,
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_email_recipient(pool, **_COMMON_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "owner"
        owner_fallback.assert_awaited_once_with(pool, "email", _COMMON_KWARGS["email_target"])
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
        session_id = uuid.uuid4()
        owner_fallback = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_non_owner_contact()),
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=owner_fallback,
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
        owner_fallback.assert_not_awaited()
        # pending_action INSERT (alongside the additive publish_fleet_event()
        # NOTIFY bu-01r64.1 added to the same pool for the "created" event).
        insert_calls = [c for c in pool.execute.call_args_list if "pending_actions" in c.args[0]]
        assert len(insert_calls) == 1
        insert_call = insert_calls[0]
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
        # (alongside the additive publish_fleet_event() NOTIFY bu-01r64.1
        # added to the same pool for the "created" approval bus event).
        insert_calls = [c for c in pool.execute.call_args_list if "pending_actions" in c.args[0]]
        assert len(insert_calls) == 1
        insert_call = insert_calls[0]
        assert insert_call.args[5] is None

    async def test_unknown_contact_without_rule_parks(self) -> None:
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
                "butlers.identity.resolve_owner_channel_via_definer",
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
                "butlers.identity.resolve_owner_channel_via_definer",
                new=AsyncMock(side_effect=RuntimeError("owner lookup unavailable")),
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
        """No standing rule: check_email_recipient publishes kind='created' with
        status='pending' onto the fleet event bus (publish_fleet_event, RFC 0022;
        the daemon-side upward emit_approvals_event import was deleted in
        bu-01r64.2)."""
        pool = AsyncMock()
        mock_publish = AsyncMock()
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
                "butlers.fleet_events.publish_fleet_event",
                new=mock_publish,
            ),
        ):
            decision = await check_email_recipient(
                pool, **{**_COMMON_KWARGS, "butler_name": "home"}
            )

        assert decision.allowed is False
        assert decision.reason == "parked"
        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args.args[1] == "approval"
        event = call_args.args[2]
        assert event["kind"] == "created"
        assert event["butler"] == "home"
        assert event["tool_name"] == "notify"
        assert event["status"] == "pending"

    async def test_context_mismatch_park_emits_created(self) -> None:
        """Context mismatch park: check_email_recipient publishes kind='created'."""
        pool = AsyncMock()
        mock_publish = AsyncMock()
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
                "butlers.fleet_events.publish_fleet_event",
                new=mock_publish,
            ),
        ):
            decision = await check_email_recipient(
                pool,
                msg_context="personal",
                **{**_COMMON_KWARGS, "butler_name": "home"},
            )

        assert decision.allowed is False
        assert decision.reason == "parked"
        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args.args[1] == "approval"
        event = call_args.args[2]
        assert event["kind"] == "created"
        assert event["butler"] == "home"
        assert event["status"] == "pending"

    async def test_emit_created_survives_broker_failure(self) -> None:
        """publish_fleet_event raising must not prevent email guard from parking the action."""
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
                "butlers.fleet_events.publish_fleet_event",
                new=AsyncMock(side_effect=RuntimeError("broker down")),
            ),
        ):
            decision = await check_email_recipient(
                pool, **{**_COMMON_KWARGS, "butler_name": "home"}
            )

        # Guard must still park the action even when publish raises
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
    "butler_name": "messenger",
}


class TestCheckRecipient:
    """Channel-general recipient guard used by notify() for non-email channels."""

    async def test_owner_telegram_auto_approves_without_primacy(self) -> None:
        """Owner-role telegram target auto-approves on any active channel (no primacy)."""
        pool = AsyncMock()
        owner = _owner_contact()
        owner_fallback = AsyncMock(return_value=(owner, False))
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=owner),
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=owner_fallback,
            ),
        ):
            decision = await check_recipient(pool, **_TELEGRAM_KWARGS)

        assert decision.allowed is True
        assert decision.reason == "owner"
        owner_fallback.assert_awaited_once_with(
            pool, _TELEGRAM_KWARGS["channel"], _TELEGRAM_KWARGS["target"]
        )
        # Owner bypass must not park or check standing rules.
        pool.execute.assert_not_awaited()

    async def test_direct_owner_without_unambiguous_corroboration_parks(self) -> None:
        """An owner-looking normalized form cannot hide an external collision."""
        pool = AsyncMock()
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=_owner_contact()),
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

    async def test_parking_failure_is_not_reported_as_parked(self) -> None:
        """A failed transaction keeps delivery blocked without a fake action id."""
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
                "butlers.modules.approvals.email_guard.park_pending_action",
                new=AsyncMock(side_effect=RuntimeError("intent insert failed")),
            ),
        ):
            decision = await check_recipient(pool, **_TELEGRAM_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parking_failed"
        assert decision.action_id is None

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

    @pytest.mark.parametrize("fallback_errors", [False, True])
    async def test_unresolvable_target_parks(self, fallback_errors: bool) -> None:
        """Missing or failed owner corroboration remains approval-gated."""
        pool = AsyncMock()
        owner_fallback = (
            AsyncMock(side_effect=RuntimeError("owner lookup unavailable"))
            if fallback_errors
            else AsyncMock(return_value=None)
        )
        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.identity.resolve_owner_channel_via_definer",
                new=owner_fallback,
            ),
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            decision = await check_recipient(pool, **_TELEGRAM_KWARGS)

        assert decision.allowed is False
        assert decision.reason == "parked"
