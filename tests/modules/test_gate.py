"""Unit tests for the approval gate owner-bypass policy.

Covers bu-nd5me: for owner-directed OUTBOUND sends, gate.py auto-approves to ANY
active, verified owner channel — not only the primary one.  This deliberately
relaxes the earlier bu-axdie outbound primacy requirement (owner self-notification
is low-risk).  The shared ``is_primary_contact`` helper is unchanged and still
governs inbound identity resolution and the email guard.

[bu-nd5me]
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.approvals._shared import is_primary_contact
from butlers.modules.approvals.gate import _make_gate_wrapper

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owner_contact(contact_id: uuid.UUID | None = None):
    from butlers.identity import ResolvedContact

    return ResolvedContact(
        contact_id=contact_id or uuid.uuid4(),
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


def _make_pool(*, fetchrow_return: Any = None, fetchrow_side_effect: Any = None) -> AsyncMock:
    """Build a minimal mock asyncpg pool."""
    pool = AsyncMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _make_original_fn() -> AsyncMock:
    """Return an async function that simulates a successful tool call."""
    fn = AsyncMock(return_value={"status": "sent"})
    fn.__name__ = "telegram_send_message"
    fn.__qualname__ = "telegram_send_message"
    return fn


async def _call_gate(
    tool_args: dict,
    *,
    resolved_contact: Any,
    pool: AsyncMock,
    original_fn: AsyncMock | None = None,
) -> dict:
    """Helper: build a gate wrapper and call it with the given tool_args."""
    if original_fn is None:
        original_fn = _make_original_fn()

    from butlers.modules.approvals.executor import ExecutionResult

    wrapper = _make_gate_wrapper(
        tool_name="telegram_send_message",
        original_fn=original_fn,
        pool=pool,
        expiry_hours=72,
        risk_tier=MagicMock(value="medium"),
        rule_precedence=("contact_role", "standing_rule"),
    )

    with (
        patch(
            "butlers.modules.approvals.gate._resolve_target_contact",
            new=AsyncMock(return_value=resolved_contact),
        ),
        patch(
            "butlers.modules.approvals.gate.record_approval_event",
            new=AsyncMock(),
        ),
        patch(
            "butlers.modules.approvals.gate.execute_approved_action",
            new=AsyncMock(return_value=ExecutionResult(success=True, result={"status": "sent"})),
        ),
    ):
        return await wrapper(**tool_args)


# ---------------------------------------------------------------------------
# is_primary_contact unit tests
# ---------------------------------------------------------------------------


class TestIsPrimaryContact:
    """Unit tests for the shared is_primary_contact helper.

    Migration bead 7 (bu-akads): is_primary_contact now takes entity_id and
    queries relationship.entity_facts instead of public.contact_info.
    The triple's ``"primary"`` column replaces the legacy ``is_primary`` column.
    """

    async def test_returns_true_when_is_primary(self) -> None:
        entity_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"primary": True})
        result = await is_primary_contact(pool, entity_id, "telegram", "12345")
        assert result is True

    async def test_returns_false_when_not_primary(self) -> None:
        entity_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"primary": False})
        result = await is_primary_contact(pool, entity_id, "telegram", "99999")
        assert result is False

    async def test_returns_false_when_row_missing(self) -> None:
        entity_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return=None)
        result = await is_primary_contact(pool, entity_id, "telegram", "no-such-id")
        assert result is False

    async def test_returns_false_on_db_error(self) -> None:
        entity_id = uuid.uuid4()
        pool = _make_pool(fetchrow_side_effect=Exception("connection lost"))
        result = await is_primary_contact(pool, entity_id, "whatsapp_jid", "+15555555")
        assert result is False

    async def test_queries_correct_columns(self) -> None:
        """Bead 7 cut-over: query targets relationship.entity_facts with entity_id."""
        entity_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"primary": True})
        await is_primary_contact(pool, entity_id, "telegram", "chat-99")
        query, *args = pool.fetchrow.call_args.args
        assert "entity_facts" in query
        assert '"primary"' in query or "primary" in query
        assert args[0] == entity_id
        assert args[1] == "has-handle"  # telegram → has-handle predicate
        assert args[2] == "chat-99"

    async def test_at_prefixed_username_returns_true_when_stored_without_at(self) -> None:
        """is_primary_contact normalises '@Tzeusy' → 'Tzeusy' for telegram channel.

        Regression for bu-c4f7f: the primacy check must be consistent with
        resolve_contact_by_channel's @-prefix normalisation so that an owner send
        with chat_id='@Tzeusy' is not mis-classified as non-primary when the
        stored fact uses bare 'Tzeusy'.
        """
        entity_id = uuid.uuid4()
        stored = "Tzeusy"

        # fetchrow returns the primary row only for the stored bare value
        def _fetchrow(query: str, eid: Any, predicate: str, value: str) -> dict | None:
            if value.lower() == stored.lower():
                return {"primary": True}
            return None

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)

        result = await is_primary_contact(pool, entity_id, "telegram", "@Tzeusy")

        assert result is True, (
            "is_primary_contact must resolve '@Tzeusy' to stored 'Tzeusy' and return True"
        )

    async def test_at_prefixed_username_returns_false_when_not_stored(self) -> None:
        """is_primary_contact returns False when no variant of the username is primary."""
        entity_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return=None)

        result = await is_primary_contact(pool, entity_id, "telegram", "@nobody")

        assert result is False

    async def test_non_telegram_channel_uses_exact_match_only(self) -> None:
        """Non-telegram channels (e.g. email) are not subject to @-prefix normalization."""
        entity_id = uuid.uuid4()
        # Only one fetchrow call expected — email uses exact match, not candidate loop
        pool = _make_pool(fetchrow_return={"primary": True})

        result = await is_primary_contact(pool, entity_id, "email", "owner@example.com")

        assert result is True
        assert pool.fetchrow.await_count == 1, "email must use a single exact-match query"


# ---------------------------------------------------------------------------
# Gate wrapper: owner bypass requires is_primary
# ---------------------------------------------------------------------------


class TestGateOwnerOutboundAutoApprove:
    """gate.py auto-approves owner-directed OUTBOUND sends to any active owner channel.

    bu-nd5me reverses the earlier bu-axdie outbound primacy requirement: owner
    self-notification is low-risk, so a send to a verified (active) owner channel
    auto-approves regardless of whether it is the primary entry for that channel
    type.  Channel resolution only returns an owner for an active entity_facts
    triple, so reaching the owner branch already implies a verified owner channel.
    """

    async def test_owner_primary_telegram_auto_approves(self) -> None:
        """Owner send to primary telegram chat_id is auto-approved."""
        owner = _owner_contact()
        # is_primary=True for the targeted chat_id
        pool = _make_pool(fetchrow_return={"primary": True})

        result = await _call_gate(
            {"chat_id": "12345", "message": "hello"},
            resolved_contact=owner,
            pool=pool,
        )
        assert result == {"status": "sent"}

    async def test_owner_non_primary_telegram_auto_approves(self) -> None:
        """Owner send to a non-primary (but active/verified) telegram chat_id auto-approves.

        bu-nd5me: an owner with both a personal (primary) and a secondary
        (non-primary) Telegram chat ID may receive auto-approved self-notifications
        on EITHER, because messaging oneself is low-risk.  The gate must no longer
        consult primacy for owner-directed outbound sends.
        """
        owner = _owner_contact()
        # Even with a non-primary row, the owner channel must auto-approve.
        pool = _make_pool(fetchrow_return={"primary": False})

        result = await _call_gate(
            {"chat_id": "99999", "message": "hello from secondary"},
            resolved_contact=owner,
            pool=pool,
        )
        assert result == {"status": "sent"}

    async def test_owner_non_primary_whatsapp_auto_approves(self) -> None:
        """Owner send to a non-primary whatsapp_jid auto-approves (bu-nd5me)."""
        owner = _owner_contact()
        pool = _make_pool(fetchrow_return={"primary": False})

        result = await _call_gate(
            {"recipient": "+15550001111", "message": "hi from secondary jid"},
            resolved_contact=owner,
            pool=pool,
        )
        assert result == {"status": "sent"}

    async def test_owner_entity_id_dispatch_auto_approves_without_primacy_check(self) -> None:
        """entity_id dispatch is exempt from the primacy check.

        When the tool is called with entity_id (not a specific channel address),
        the system already resolves to the primary channel.  The gate must not
        add an extra primacy barrier here.
        """
        owner_id = uuid.uuid4()
        owner = _owner_contact(owner_id)
        # fetchrow will NOT be called for is_primary in entity_id path
        pool = _make_pool(fetchrow_return={"primary": False})

        result = await _call_gate(
            {"entity_id": str(owner_id), "channel": "telegram", "message": "hi"},
            resolved_contact=owner,
            pool=pool,
        )
        # Should auto-approve — entity_id dispatch skips primacy gate
        assert result == {"status": "sent"}
        # Confirm fetchrow was NOT called for primacy (only _resolve_target_contact is patched)
        # pool.fetchrow may be called by _resolve_target_contact's internal direct UUID lookup,
        # but _is_primary_contact must NOT be called for entity_id dispatch.
        # We verify this indirectly: if it were called with is_primary=False the action would park.

    async def test_non_owner_with_primary_telegram_goes_through_rules(self) -> None:
        """Non-owner telegram target goes through rules path regardless of is_primary."""
        non_owner = _non_owner_contact()
        pool = _make_pool(fetchrow_return={"primary": True})

        with patch(
            "butlers.modules.approvals.gate._resolve_target_contact",
            new=AsyncMock(return_value=non_owner),
        ):
            with patch(
                "butlers.modules.approvals.gate.record_approval_event",
                new=AsyncMock(),
            ):
                wrapper = _make_gate_wrapper(
                    tool_name="telegram_send_message",
                    original_fn=_make_original_fn(),
                    pool=pool,
                    expiry_hours=72,
                    risk_tier=MagicMock(value="medium"),
                    rule_precedence=("contact_role", "standing_rule"),
                )
                result = await wrapper(chat_id="12345", message="hi non-owner")

        # No matching rule → parked (fetch returns [])
        assert result.get("status") == "pending_approval"

    async def test_owner_with_two_telegram_chat_ids_both_auto_approve(self) -> None:
        """Scenario: owner has two Telegram chat IDs; sends to EITHER auto-approve.

        bu-nd5me acceptance: two entity_facts rows for the same channel type
        (telegram), one primary and one not.  Both are verified owner channels, so
        an owner self-notification to either must auto-approve — primacy is no
        longer consulted for outbound owner sends.
        """
        owner = _owner_contact()
        primary_chat_id = "11111111"
        secondary_chat_id = "22222222"

        for chat_id in (primary_chat_id, secondary_chat_id):
            result = await _call_gate(
                {"chat_id": chat_id, "message": "hi"},
                resolved_contact=owner,
                pool=_make_pool(fetchrow_return={"primary": chat_id == primary_chat_id}),
            )
            assert result == {"status": "sent"}, f"send to {chat_id} should auto-approve"


# ---------------------------------------------------------------------------
# emit_approvals_event 'created' emission tests [bu-jg0kt]
# ---------------------------------------------------------------------------


class TestGateEmitsCreatedEvent:
    """gate.py must emit 'created' approval WS events at every approval-creation site."""

    async def _call_gate_patched(
        self,
        tool_args: dict,
        *,
        resolved_contact: Any,
        pool: AsyncMock,
        butler_name: str | None = None,
        original_fn: AsyncMock | None = None,
    ) -> tuple[dict, MagicMock]:
        """Run the gate wrapper with emit_approvals_event patched; return (result, mock)."""
        if original_fn is None:
            original_fn = _make_original_fn()

        from butlers.modules.approvals.executor import ExecutionResult

        wrapper = _make_gate_wrapper(
            tool_name="telegram_send_message",
            original_fn=original_fn,
            pool=pool,
            expiry_hours=72,
            risk_tier=MagicMock(value="medium"),
            rule_precedence=("contact_role", "standing_rule"),
            butler_name=butler_name,
        )

        mock_emit = MagicMock()
        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=resolved_contact),
            ),
            patch(
                "butlers.modules.approvals.gate.record_approval_event",
                new=AsyncMock(),
            ),
            patch(
                "butlers.modules.approvals.gate.execute_approved_action",
                new=AsyncMock(
                    return_value=ExecutionResult(success=True, result={"status": "sent"})
                ),
            ),
            patch(
                "butlers.api.routers.approvals.emit_approvals_event",
                new=mock_emit,
            ),
        ):
            result = await wrapper(**tool_args)

        return result, mock_emit

    async def test_owner_auto_approve_emits_created(self) -> None:
        """Owner-targeted primary channel: gate emits kind='created' with status='approved'."""
        owner = _owner_contact()
        pool = _make_pool(fetchrow_return={"primary": True})

        result, mock_emit = await self._call_gate_patched(
            {"chat_id": "12345", "message": "hello"},
            resolved_contact=owner,
            pool=pool,
            butler_name="home",
        )

        assert result == {"status": "sent"}
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args
        assert call_kwargs.args[0] == "created"
        assert call_kwargs.kwargs.get("butler") == "home"
        assert call_kwargs.kwargs.get("tool_name") == "telegram_send_message"
        assert call_kwargs.kwargs.get("status") == "approved"

    async def test_park_pending_emits_created(self) -> None:
        """No-rule path: gate emits kind='created' with status='pending'.

        A non-owner contact with no matching standing rule parks for approval.
        """
        non_owner = _non_owner_contact()
        pool = _make_pool(fetchrow_return=None)

        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=non_owner),
            ),
            patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
        ):
            mock_emit = MagicMock()
            with patch("butlers.api.routers.approvals.emit_approvals_event", new=mock_emit):
                wrapper = _make_gate_wrapper(
                    tool_name="telegram_send_message",
                    original_fn=_make_original_fn(),
                    pool=pool,
                    expiry_hours=72,
                    risk_tier=MagicMock(value="medium"),
                    rule_precedence=("contact_role", "standing_rule"),
                    butler_name="home",
                )
                result = await wrapper(chat_id="99999", message="hi non-owner")

        assert result.get("status") == "pending_approval"
        mock_emit.assert_called_once()
        call_kwargs = mock_emit.call_args
        assert call_kwargs.args[0] == "created"
        assert call_kwargs.kwargs.get("status") == "pending"
        assert call_kwargs.kwargs.get("butler") == "home"

    async def test_emit_created_survives_import_failure(self) -> None:
        """If emit_approvals_event import fails, gate wrapper must not crash."""
        owner = _owner_contact()
        pool = _make_pool(fetchrow_return={"primary": True})

        from butlers.modules.approvals.executor import ExecutionResult

        wrapper = _make_gate_wrapper(
            tool_name="telegram_send_message",
            original_fn=_make_original_fn(),
            pool=pool,
            expiry_hours=72,
            risk_tier=MagicMock(value="medium"),
            rule_precedence=("contact_role", "standing_rule"),
            butler_name="home",
        )

        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=owner),
            ),
            patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
            patch(
                "butlers.modules.approvals.gate.execute_approved_action",
                new=AsyncMock(
                    return_value=ExecutionResult(success=True, result={"status": "sent"})
                ),
            ),
            patch(
                "butlers.api.routers.approvals.emit_approvals_event",
                side_effect=RuntimeError("broker down"),
            ),
        ):
            result = await wrapper(chat_id="12345", message="hello")

        # Must succeed even when emit raises
        assert result == {"status": "sent"}


# ---------------------------------------------------------------------------
# Cross-schema owner fallback (public.resolve_owner_triple SECURITY DEFINER)
# ---------------------------------------------------------------------------


class TestOwnerCrossSchemaFallback:
    """Owner-directed sends auto-approve via the SECURITY DEFINER fallback even when
    the butler role cannot read relationship.entity_facts (resolve returns None).

    [core_145] messenger/home/etc. are schema-isolated from the relationship
    schema, so resolve_contact_by_channel returns None for owner sends; the gate
    recognizes the owner via resolve_owner_channel_via_definer().
    """

    async def _run(self, *, resolve_return, definer_return, fetch_return=None):
        from butlers.modules.approvals.executor import ExecutionResult

        pool = _make_pool()
        if fetch_return is not None:
            pool.fetch = AsyncMock(return_value=fetch_return)
        original_fn = _make_original_fn()
        wrapper = _make_gate_wrapper(
            tool_name="telegram_send_message",
            original_fn=original_fn,
            pool=pool,
            expiry_hours=72,
            risk_tier=MagicMock(value="medium"),
            rule_precedence=("contact_role", "standing_rule"),
        )
        exec_mock = AsyncMock(return_value=ExecutionResult(success=True, result={"status": "sent"}))
        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=resolve_return),
            ),
            patch(
                "butlers.modules.approvals.gate.resolve_owner_channel_via_definer",
                new=AsyncMock(return_value=definer_return),
            ),
            patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
            patch("butlers.modules.approvals.gate.execute_approved_action", new=exec_mock),
        ):
            result = await wrapper(chat_id="206570151", text="hi")
        return result, pool, exec_mock

    async def test_unresolvable_owner_primary_auto_approves(self) -> None:
        owner = _owner_contact()
        result, pool, exec_mock = await self._run(resolve_return=None, definer_return=(owner, True))
        assert result == {"status": "sent"}
        exec_mock.assert_awaited_once()
        inserts = [
            c for c in pool.execute.await_args_list if "INSERT INTO pending_actions" in c.args[0]
        ]
        assert inserts, "expected an owner auto-approve pending_actions insert"
        assert "role:owner" in inserts[0].args

    async def test_non_primary_owner_auto_approves(self) -> None:
        """bu-nd5me: a non-primary but active owner channel resolved via the
        SECURITY DEFINER fallback auto-approves for owner-directed outbound."""
        owner = _owner_contact()
        result, pool, exec_mock = await self._run(
            resolve_return=None, definer_return=(owner, False)
        )
        assert result == {"status": "sent"}
        exec_mock.assert_awaited_once()
        inserts = [
            c for c in pool.execute.await_args_list if "INSERT INTO pending_actions" in c.args[0]
        ]
        assert inserts, "expected an owner auto-approve pending_actions insert"
        assert "role:owner" in inserts[0].args

    async def test_definer_no_match_keeps_parking(self) -> None:
        result, _pool, exec_mock = await self._run(
            resolve_return=None, definer_return=None, fetch_return=[]
        )
        assert result["status"] == "pending_approval"
        exec_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# bu-nd5me acceptance: notify() to a verified-but-secondary owner channel
# ---------------------------------------------------------------------------


class TestNotifySecondaryOwnerChannel:
    """Acceptance for bu-nd5me using the notify() channel+recipient arg shape.

    The reported regression: notify(channel="email", recipient="tzeuse@gmail.com")
    parked even though tzeuse@ is a registered, active (non-primary) owner email.
    These tests assert the owner-self-notification path now auto-approves while a
    send to a non-owner recipient still parks.
    """

    _SECONDARY_OWNER_EMAIL = "tzeuse@gmail.com"
    _NON_OWNER_EMAIL = "stranger@example.com"

    async def test_notify_secondary_owner_email_auto_approves(self) -> None:
        """notify() to a non-primary but active owner email auto-approves."""
        owner = _owner_contact()
        # is_primary would be False for the secondary address — must not matter now.
        pool = _make_pool(fetchrow_return={"primary": False})

        result = await _call_gate(
            {
                "channel": "email",
                "recipient": self._SECONDARY_OWNER_EMAIL,
                "message": "time-sensitive reminder",
            },
            resolved_contact=owner,
            pool=pool,
        )
        assert result == {"status": "sent"}

    async def test_notify_secondary_owner_email_auto_approves_cross_schema(self) -> None:
        """The messenger scenario: the butler role cannot read relationship schema,
        so resolve_contact_by_channel returns None and the owner is recognized via
        the SECURITY DEFINER fallback reporting a non-primary owner channel."""
        from butlers.modules.approvals.executor import ExecutionResult

        owner = _owner_contact()
        pool = _make_pool()
        exec_mock = AsyncMock(return_value=ExecutionResult(success=True, result={"status": "sent"}))
        wrapper = _make_gate_wrapper(
            tool_name="notify",
            original_fn=_make_original_fn(),
            pool=pool,
            expiry_hours=72,
            risk_tier=MagicMock(value="medium"),
            rule_precedence=("contact_role", "standing_rule"),
        )
        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "butlers.modules.approvals.gate.resolve_owner_channel_via_definer",
                new=AsyncMock(return_value=(owner, False)),
            ),
            patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
            patch("butlers.modules.approvals.gate.execute_approved_action", new=exec_mock),
        ):
            result = await wrapper(
                channel="email",
                recipient=self._SECONDARY_OWNER_EMAIL,
                message="time-sensitive reminder",
            )
        assert result == {"status": "sent"}
        exec_mock.assert_awaited_once()
        inserts = [
            c for c in pool.execute.await_args_list if "INSERT INTO pending_actions" in c.args[0]
        ]
        assert inserts and "role:owner" in inserts[0].args

    async def test_notify_non_owner_email_still_parks(self) -> None:
        """notify() to a non-owner recipient with no standing rule still parks.

        Guardrail: relaxing the owner primacy gate must NOT auto-approve sends to
        non-owner recipients.
        """
        non_owner = _non_owner_contact()
        pool = _make_pool(fetchrow_return=None)  # no standing rules (fetch → [])

        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=non_owner),
            ),
            patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
        ):
            wrapper = _make_gate_wrapper(
                tool_name="notify",
                original_fn=_make_original_fn(),
                pool=pool,
                expiry_hours=72,
                risk_tier=MagicMock(value="medium"),
                rule_precedence=("contact_role", "standing_rule"),
            )
            result = await wrapper(
                channel="email",
                recipient=self._NON_OWNER_EMAIL,
                message="hello stranger",
            )
        assert result.get("status") == "pending_approval"
        assert "action_id" in result
