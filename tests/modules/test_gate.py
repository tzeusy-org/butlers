"""Unit tests for the approval gate owner-bypass is_primary guard.

Covers bu-axdie: gate.py owner bypass now requires is_primary=True for the
targeted channel address.  Mirrors the shape of test_email_guard.py::test_owner_non_primary_email_parks
but for the non-email MCP tool gating path (telegram, whatsapp, etc.).

[bu-axdie]
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
    """Unit tests for the shared is_primary_contact helper."""

    async def test_returns_true_when_is_primary(self) -> None:
        contact_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"is_primary": True})
        result = await is_primary_contact(pool, contact_id, "telegram", "12345")
        assert result is True

    async def test_returns_false_when_not_primary(self) -> None:
        contact_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"is_primary": False})
        result = await is_primary_contact(pool, contact_id, "telegram", "99999")
        assert result is False

    async def test_returns_false_when_row_missing(self) -> None:
        contact_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return=None)
        result = await is_primary_contact(pool, contact_id, "telegram", "no-such-id")
        assert result is False

    async def test_returns_false_on_db_error(self) -> None:
        contact_id = uuid.uuid4()
        pool = _make_pool(fetchrow_side_effect=Exception("connection lost"))
        result = await is_primary_contact(pool, contact_id, "whatsapp_jid", "+15555555")
        assert result is False

    async def test_queries_correct_columns(self) -> None:
        contact_id = uuid.uuid4()
        pool = _make_pool(fetchrow_return={"is_primary": True})
        await is_primary_contact(pool, contact_id, "telegram", "chat-99")
        query, *args = pool.fetchrow.call_args.args
        assert "contact_info" in query
        assert "is_primary" in query
        assert args[0] == contact_id
        assert args[1] == "telegram"
        assert args[2] == "chat-99"


# ---------------------------------------------------------------------------
# Gate wrapper: owner bypass requires is_primary
# ---------------------------------------------------------------------------


class TestGateOwnerPrimaryRequirement:
    """gate.py owner bypass must require is_primary=True for channel-based dispatches."""

    async def test_owner_primary_telegram_auto_approves(self) -> None:
        """Owner send to primary telegram chat_id is auto-approved."""
        owner = _owner_contact()
        # is_primary=True for the targeted chat_id
        pool = _make_pool(fetchrow_return={"is_primary": True})

        result = await _call_gate(
            {"chat_id": "12345", "message": "hello"},
            resolved_contact=owner,
            pool=pool,
        )
        assert result == {"status": "sent"}

    async def test_owner_non_primary_telegram_parks(self) -> None:
        """Owner send to a non-primary telegram chat_id is parked for approval.

        This is the regression test for bu-axdie: an owner with both a personal
        (primary) and a work (non-primary) Telegram chat ID must NOT auto-approve
        sends to the work chat ID.
        """
        owner = _owner_contact()
        # Targeted chat_id is NOT the primary one
        pool = _make_pool(fetchrow_return={"is_primary": False})

        with patch(
            "butlers.modules.approvals.gate._resolve_target_contact",
            new=AsyncMock(return_value=owner),
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
                result = await wrapper(chat_id="99999", message="hello from non-primary")

        assert result.get("status") == "pending_approval"
        assert "action_id" in result

    async def test_owner_non_primary_whatsapp_parks(self) -> None:
        """Owner send to a non-primary whatsapp_jid is parked for approval."""
        owner = _owner_contact()
        pool = _make_pool(fetchrow_return={"is_primary": False})

        with patch(
            "butlers.modules.approvals.gate._resolve_target_contact",
            new=AsyncMock(return_value=owner),
        ):
            with patch(
                "butlers.modules.approvals.gate.record_approval_event",
                new=AsyncMock(),
            ):
                wrapper = _make_gate_wrapper(
                    tool_name="whatsapp_send_message",
                    original_fn=_make_original_fn(),
                    pool=pool,
                    expiry_hours=72,
                    risk_tier=MagicMock(value="medium"),
                    rule_precedence=("contact_role", "standing_rule"),
                )
                result = await wrapper(recipient="+15550001111", message="hi from non-primary jid")

        assert result.get("status") == "pending_approval"
        assert "action_id" in result

    async def test_owner_contact_id_dispatch_auto_approves_without_primacy_check(self) -> None:
        """contact_id dispatch is exempt from the primacy check.

        When the tool is called with contact_id (not a specific channel address),
        the system already resolves to the primary channel.  The gate must not
        add an extra primacy barrier here.
        """
        owner_id = uuid.uuid4()
        owner = _owner_contact(owner_id)
        # fetchrow will NOT be called for is_primary in contact_id path
        pool = _make_pool(fetchrow_return={"is_primary": False})

        result = await _call_gate(
            {"contact_id": str(owner_id), "channel": "telegram", "message": "hi"},
            resolved_contact=owner,
            pool=pool,
        )
        # Should auto-approve — contact_id dispatch skips primacy gate
        assert result == {"status": "sent"}
        # Confirm fetchrow was NOT called for primacy (only _resolve_target_contact is patched)
        # pool.fetchrow may be called by _resolve_target_contact's internal direct UUID lookup,
        # but _is_primary_contact must NOT be called for contact_id dispatch.
        # We verify this indirectly: if it were called with is_primary=False the action would park.

    async def test_non_owner_with_primary_telegram_goes_through_rules(self) -> None:
        """Non-owner telegram target goes through rules path regardless of is_primary."""
        non_owner = _non_owner_contact()
        pool = _make_pool(fetchrow_return={"is_primary": True})

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

    async def test_owner_with_two_telegram_chat_ids_parks_non_primary(self) -> None:
        """Scenario: owner has two Telegram chat IDs; send to non-primary one is parked.

        This is the acceptance scenario from bu-axdie: two contact_info rows for
        the same channel type (telegram), one primary and one not.  Sending to the
        non-primary chat_id must park the action even though the contact is owner.
        """
        owner = _owner_contact()
        primary_chat_id = "11111111"
        secondary_chat_id = "22222222"

        async def _fetchrow_with_primacy(query: str, *args: Any) -> dict | None:
            """Return is_primary based on which chat_id is queried."""
            if "contact_info" in query and "is_primary" in query:
                queried_value = args[2] if len(args) > 2 else None
                if queried_value == primary_chat_id:
                    return {"is_primary": True}
                if queried_value == secondary_chat_id:
                    return {"is_primary": False}
            return None

        pool = _make_pool(fetchrow_side_effect=_fetchrow_with_primacy)

        # Send to primary → auto-approve
        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=owner),
            ),
            patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
            patch(
                "butlers.modules.approvals.gate.execute_approved_action",
                new=AsyncMock(
                    return_value=__import__(
                        "butlers.modules.approvals.executor",
                        fromlist=["ExecutionResult"],
                    ).ExecutionResult(success=True, result={"status": "sent"})
                ),
            ),
        ):
            wrapper = _make_gate_wrapper(
                tool_name="telegram_send_message",
                original_fn=_make_original_fn(),
                pool=pool,
                expiry_hours=72,
                risk_tier=MagicMock(value="medium"),
                rule_precedence=("contact_role", "standing_rule"),
            )
            result_primary = await wrapper(chat_id=primary_chat_id, message="hi primary")

        assert result_primary == {"status": "sent"}

        # Send to secondary (non-primary) → parked
        pool2 = _make_pool(fetchrow_side_effect=_fetchrow_with_primacy)
        with (
            patch(
                "butlers.modules.approvals.gate._resolve_target_contact",
                new=AsyncMock(return_value=owner),
            ),
            patch("butlers.modules.approvals.gate.record_approval_event", new=AsyncMock()),
        ):
            wrapper2 = _make_gate_wrapper(
                tool_name="telegram_send_message",
                original_fn=_make_original_fn(),
                pool=pool2,
                expiry_hours=72,
                risk_tier=MagicMock(value="medium"),
                rule_precedence=("contact_role", "standing_rule"),
            )
            result_secondary = await wrapper2(chat_id=secondary_chat_id, message="hi secondary")

        assert result_secondary.get("status") == "pending_approval"
        assert "action_id" in result_secondary
