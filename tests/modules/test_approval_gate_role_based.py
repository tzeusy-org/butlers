"""Tests for role-based approval gating in gate.py.

Verifies that the gate_wrapper correctly resolves the target contact's role
and gates tool calls accordingly:

- Notifications to the owner bypass approval without a standing rule
- Notifications to non-owner contacts check standing rules, then pend
- Unresolvable targets always require approval (conservative default)
- Standing rules auto-approve non-owner contacts when matched
- All gating paths are covered (owner, non-owner matched, non-owner unmatched,
  unresolvable)

Tests also cover _extract_channel_identity() and _resolve_target_contact()
helpers.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from butlers.config import (
    DEFAULT_APPROVAL_RULE_PRECEDENCE,
    ApprovalConfig,
    ApprovalRiskTier,
    GatedToolConfig,
)
from butlers.identity import ResolvedContact
from butlers.modules.approvals.gate import (
    _extract_channel_identity,
    _resolve_target_contact,
    apply_approval_gates,
)
from butlers.modules.approvals.models import ActionStatus

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test contact fixtures
# ---------------------------------------------------------------------------

OWNER_CONTACT_ID = uuid.uuid4()
NON_OWNER_CONTACT_ID = uuid.uuid4()
OWNER_CHAT_ID = "111111111"
NON_OWNER_CHAT_ID = "222222222"
OWNER_EMAIL = "owner@example.com"
NON_OWNER_EMAIL = "alice@example.com"


def _make_owner_contact() -> ResolvedContact:
    return ResolvedContact(
        contact_id=OWNER_CONTACT_ID,
        name="Owner",
        roles=["owner"],
        entity_id=None,
    )


def _make_non_owner_contact() -> ResolvedContact:
    return ResolvedContact(
        contact_id=NON_OWNER_CONTACT_ID,
        name="Alice",
        roles=["friend"],
        entity_id=None,
    )


# ---------------------------------------------------------------------------
# Mock DB pool that supports contact resolution
# ---------------------------------------------------------------------------


class RoleAwareMockPool:
    """Extended MockPool that supports shared.contacts and contact_info lookups.

    Maps (channel_type, channel_value) -> ResolvedContact for testing role-based
    gating without a real database.
    """

    def __init__(self) -> None:
        self.pending_actions: dict[uuid.UUID, dict[str, Any]] = {}
        self.approval_rules: list[dict[str, Any]] = []
        self.approval_events: list[dict[str, Any]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        # contact_info lookup: (channel_type, channel_value) -> ResolvedContact
        self._contact_info: dict[tuple[str, str], ResolvedContact] = {}
        # contact_id lookup: UUID -> ResolvedContact
        self._contacts_by_id: dict[UUID, ResolvedContact] = {}
        # Flag to simulate DB errors on contact lookup
        self.contact_lookup_raises: bool = False

    def register_contact(
        self,
        channel_type: str,
        channel_value: str,
        contact: ResolvedContact,
    ) -> None:
        """Register a contact for channel-based lookup."""
        self._contact_info[(channel_type, channel_value)] = contact
        self._contacts_by_id[contact.contact_id] = contact

    def add_rule(
        self,
        tool_name: str,
        arg_constraints: dict[str, Any] | None = None,
        *,
        active: bool = True,
        expires_at: datetime | None = None,
        max_uses: int | None = None,
        use_count: int = 0,
    ) -> uuid.UUID:
        """Add a standing approval rule and return its ID."""
        rule_id = uuid.uuid4()
        self.approval_rules.append(
            {
                "id": rule_id,
                "tool_name": tool_name,
                "arg_constraints": json.dumps(arg_constraints or {}),
                "description": f"Rule for {tool_name}",
                "created_from": None,
                "created_at": datetime.now(UTC),
                "expires_at": expires_at,
                "max_uses": max_uses,
                "use_count": use_count,
                "active": active,
            }
        )
        return rule_id

    async def execute(self, query: str, *args: Any) -> None:
        """Simulate asyncpg execute()."""
        self.execute_calls.append((query, args))
        if "INSERT INTO pending_actions" in query:
            action_id = args[0]
            # Parse decided_by and approval_rule_id from query column list.
            # Owner path: (id,...,expires_at, decided_by)  → 9 positional args
            # Rule path:  (id,...,expires_at, approval_rule_id, decided_by) → 10 args
            # Pending path: (id,...,expires_at) → 8 args
            if "decided_by" in query and "approval_rule_id" in query:
                # 10-arg rule-matched insert
                approval_rule_id = args[8] if len(args) > 8 else None
                decided_by = args[9] if len(args) > 9 else None
            elif "decided_by" in query:
                # 9-arg owner insert
                approval_rule_id = None
                decided_by = args[8] if len(args) > 8 else None
            else:
                # 8-arg pending insert
                approval_rule_id = None
                decided_by = None
            self.pending_actions[action_id] = {
                "id": action_id,
                "tool_name": args[1],
                "tool_args": args[2],
                "agent_summary": args[3],
                "session_id": args[4] if len(args) > 4 else None,
                "status": args[5] if len(args) > 5 else "pending",
                "requested_at": args[6] if len(args) > 6 else datetime.now(UTC),
                "expires_at": args[7] if len(args) > 7 else None,
                "approval_rule_id": approval_rule_id,
                "decided_by": decided_by,
                "decided_at": None,
                "execution_result": None,
            }
        elif "INSERT INTO approval_events" in query:
            self.approval_events.append(
                {
                    "event_type": args[0],
                    "action_id": args[1],
                    "rule_id": args[2],
                    "actor": args[3],
                    "reason": args[4],
                    "event_metadata": json.loads(args[5]),
                    "occurred_at": args[6],
                }
            )
        elif "UPDATE pending_actions" in query and "status" in query:
            if "AND status = $5" in query:
                action_id = args[3]
            else:
                action_id = args[-1]
            if action_id in self.pending_actions:
                self.pending_actions[action_id]["status"] = args[0]
                if "execution_result" in query:
                    self.pending_actions[action_id]["execution_result"] = args[1]
        elif "UPDATE approval_rules" in query and "use_count" in query:
            rule_id = args[0]
            for rule in self.approval_rules:
                if rule["id"] == rule_id:
                    rule["use_count"] = rule.get("use_count", 0) + 1

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        """Simulate asyncpg fetch()."""
        if "approval_rules" in query:
            tool_name = args[0] if args else None
            results = []
            for rule in self.approval_rules:
                if tool_name and rule["tool_name"] != tool_name:
                    continue
                if not rule["active"]:
                    continue
                results.append(dict(rule))
            return results
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Simulate asyncpg fetchrow() for various lookups."""
        if self.contact_lookup_raises:
            raise RuntimeError("Simulated DB error for contact lookup")

        # pending_actions lookup (used by executor)
        if "pending_actions" in query and args:
            action_id = args[0]
            row = self.pending_actions.get(action_id)
            return dict(row) if row else None

        # shared.contacts lookup by contact_id UUID (direct lookup)
        if "shared.contacts" in query and "WHERE id" in query and args:
            try:
                lookup_id = UUID(str(args[0]))
            except (ValueError, AttributeError):
                return None
            contact = self._contacts_by_id.get(lookup_id)
            if contact is None:
                return None
            return {
                "contact_id": contact.contact_id,
                "name": contact.name,
                "roles": contact.roles,
                "entity_id": contact.entity_id,
            }

        # shared.contact_info JOIN shared.contacts lookup (channel-based)
        if "shared.contact_info" in query and args and len(args) >= 2:
            channel_type = str(args[0])
            channel_value = str(args[1])
            contact = self._contact_info.get((channel_type, channel_value))
            if contact is None:
                return None
            return {
                "contact_id": contact.contact_id,
                "name": contact.name,
                "roles": contact.roles,
                "entity_id": contact.entity_id,
            }

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_mcp() -> MagicMock:
    """Create a mock FastMCP server."""
    mock_mcp = MagicMock()
    mock_mcp._tool_manager = MagicMock()
    _tools_dict: dict[str, Any] = {}

    class FakeTool:
        def __init__(self, name: str, fn: Any):
            self.name = name
            self.fn = fn

    async def get_tools():
        return _tools_dict

    mock_mcp._tool_manager.get_tools = get_tools

    def tool_decorator(*_args, **_kwargs):
        def decorator(fn):
            _tools_dict[fn.__name__] = FakeTool(fn.__name__, fn)
            return fn

        return decorator

    mock_mcp.tool = tool_decorator
    return mock_mcp


def _make_approval_config(
    tool_name: str = "notify",
    risk_tier: ApprovalRiskTier = ApprovalRiskTier.MEDIUM,
) -> ApprovalConfig:
    return ApprovalConfig(
        enabled=True,
        default_expiry_hours=48,
        gated_tools={tool_name: GatedToolConfig(risk_tier=risk_tier)},
    )


# ---------------------------------------------------------------------------
# Tests: _extract_channel_identity
# ---------------------------------------------------------------------------


class TestExtractChannelIdentity:
    """Unit tests for _extract_channel_identity()."""

    def test_notify_tool_channel_and_recipient(self):
        """notify() tool: channel + recipient extracts correctly."""
        result = _extract_channel_identity(
            {"channel": "telegram", "recipient": OWNER_CHAT_ID, "message": "Hello"}
        )
        assert result == ("telegram", OWNER_CHAT_ID)

    def test_notify_tool_email_channel(self):
        """notify() with email channel."""
        result = _extract_channel_identity(
            {"channel": "email", "recipient": OWNER_EMAIL, "message": "Hi"}
        )
        assert result == ("email", OWNER_EMAIL)

    def test_telegram_send_message_chat_id(self):
        """telegram_send_message: chat_id extracts as telegram channel."""
        result = _extract_channel_identity({"chat_id": NON_OWNER_CHAT_ID, "text": "Hi"})
        assert result == ("telegram", NON_OWNER_CHAT_ID)

    def test_email_send_message_to_field(self):
        """email_send_message: to field extracts as email channel."""
        result = _extract_channel_identity(
            {"to": NON_OWNER_EMAIL, "subject": "Test", "body": "Body"}
        )
        assert result == ("email", NON_OWNER_EMAIL)

    def test_contact_id_direct_lookup(self):
        """Explicit contact_id takes priority over other fields."""
        cid = str(uuid.uuid4())
        result = _extract_channel_identity(
            {"contact_id": cid, "channel": "telegram", "recipient": OWNER_CHAT_ID}
        )
        assert result == ("contact_id", cid)

    def test_no_recognizable_field_returns_none(self):
        """Tool args with no channel info returns None."""
        result = _extract_channel_identity({"message": "text only", "priority": "high"})
        assert result is None

    def test_empty_recipient_skipped(self):
        """Empty string recipient falls through to next pattern."""
        result = _extract_channel_identity(
            {"channel": "telegram", "recipient": "  ", "chat_id": NON_OWNER_CHAT_ID}
        )
        assert result == ("telegram", NON_OWNER_CHAT_ID)

    def test_channel_without_recipient_skipped(self):
        """channel alone (no recipient) falls through to next pattern."""
        result = _extract_channel_identity({"channel": "telegram", "text": "hi"})
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _resolve_target_contact
# ---------------------------------------------------------------------------


class TestResolveTargetContact:
    """Unit tests for _resolve_target_contact()."""

    async def test_resolves_owner_by_telegram_chat_id(self):
        """Owner contact resolved from telegram chat_id."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())

        result = await _resolve_target_contact(pool, {"chat_id": OWNER_CHAT_ID, "text": "hi"})

        assert result is not None
        assert result.contact_id == OWNER_CONTACT_ID
        assert "owner" in result.roles

    async def test_resolves_non_owner_by_email(self):
        """Non-owner contact resolved from email address."""
        pool = RoleAwareMockPool()
        pool.register_contact("email", NON_OWNER_EMAIL, _make_non_owner_contact())

        result = await _resolve_target_contact(
            pool, {"to": NON_OWNER_EMAIL, "subject": "Hi", "body": "Hello"}
        )

        assert result is not None
        assert result.contact_id == NON_OWNER_CONTACT_ID
        assert "owner" not in result.roles

    async def test_unresolvable_returns_none(self):
        """Unknown channel value returns None."""
        pool = RoleAwareMockPool()

        result = await _resolve_target_contact(pool, {"chat_id": "999999", "text": "hi"})

        assert result is None

    async def test_no_channel_info_returns_none(self):
        """Tool args without channel info returns None."""
        pool = RoleAwareMockPool()

        result = await _resolve_target_contact(pool, {"data": "no channel here"})

        assert result is None

    async def test_contact_id_direct_lookup(self):
        """Explicit contact_id performs direct DB lookup."""
        pool = RoleAwareMockPool()
        contact = _make_owner_contact()
        pool.register_contact("telegram", OWNER_CHAT_ID, contact)

        result = await _resolve_target_contact(pool, {"contact_id": str(OWNER_CONTACT_ID)})

        assert result is not None
        assert result.contact_id == OWNER_CONTACT_ID
        assert "owner" in result.roles

    async def test_db_error_returns_none(self):
        """DB error during lookup returns None gracefully."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())
        pool.contact_lookup_raises = True

        result = await _resolve_target_contact(pool, {"chat_id": OWNER_CHAT_ID, "text": "hi"})

        assert result is None


# ---------------------------------------------------------------------------
# Tests: role-based gating paths (gate_wrapper)
# ---------------------------------------------------------------------------


class TestRoleBasedGating:
    """Integration tests for the role-based gating logic in gate_wrapper."""

    async def test_owner_targeted_auto_approves_without_rule(self):
        """Tool call targeting the owner auto-approves with no standing rule needed."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())

        mock_mcp = _make_mock_mcp()
        call_log: list[dict] = []

        @mock_mcp.tool()
        async def notify(channel: str, recipient: str, message: str) -> dict:
            call_log.append({"channel": channel, "recipient": recipient})
            return {"status": "sent"}

        config = _make_approval_config("notify")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["notify"].fn
        result = await wrapper(channel="telegram", recipient=OWNER_CHAT_ID, message="Hello owner")

        # Should execute immediately (not pending)
        assert result.get("status") == "sent"
        assert call_log, "Original tool function should have been called"

    async def test_owner_targeted_action_persisted_as_approved(self):
        """Owner-targeted action is persisted with APPROVED status."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def notify(channel: str, recipient: str, message: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("notify")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["notify"].fn
        await wrapper(channel="telegram", recipient=OWNER_CHAT_ID, message="Hello")

        # Find the inserted pending_action
        assert len(pool.pending_actions) == 1
        action = next(iter(pool.pending_actions.values()))
        # Owner auto-approve inserts as APPROVED initially, then executor updates to EXECUTED
        assert action["status"] in (ActionStatus.APPROVED.value, ActionStatus.EXECUTED.value)
        assert action["decided_by"] == "role:owner"

    async def test_owner_targeted_decided_by_role_owner(self):
        """Owner-targeted auto-approve sets decided_by='role:owner'."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def notify(channel: str, recipient: str, message: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("notify")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["notify"].fn
        await wrapper(channel="telegram", recipient=OWNER_CHAT_ID, message="Hello")

        # Check INSERT args for decided_by
        insert_call = next(
            (c for c in pool.execute_calls if "INSERT INTO pending_actions" in c[0]), None
        )
        assert insert_call is not None
        # decided_by is the last arg in the owner INSERT ($9)
        args = insert_call[1]
        assert "role:owner" in args

    async def test_non_owner_without_rule_gets_pending(self):
        """Tool call targeting a non-owner without a standing rule is pended."""
        pool = RoleAwareMockPool()
        pool.register_contact("email", NON_OWNER_EMAIL, _make_non_owner_contact())

        mock_mcp = _make_mock_mcp()
        call_log: list[dict] = []

        @mock_mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            call_log.append({"to": to})
            return {"status": "sent"}

        config = _make_approval_config("email_send_message")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["email_send_message"].fn
        result = await wrapper(to=NON_OWNER_EMAIL, subject="Hi", body="Hello non-owner")

        assert result["status"] == "pending_approval"
        assert "action_id" in result
        assert not call_log, "Original tool should NOT have been called"

    async def test_non_owner_with_matching_rule_auto_approves(self):
        """Non-owner with a matching standing rule is auto-approved."""
        pool = RoleAwareMockPool()
        pool.register_contact("email", NON_OWNER_EMAIL, _make_non_owner_contact())
        pool.add_rule("email_send_message")  # Matches all email_send_message calls

        mock_mcp = _make_mock_mcp()
        call_log: list[dict] = []

        @mock_mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            call_log.append({"to": to})
            return {"status": "sent"}

        config = _make_approval_config("email_send_message")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["email_send_message"].fn
        result = await wrapper(to=NON_OWNER_EMAIL, subject="Hi", body="Hello")

        # Should execute via standing rule
        assert result.get("status") == "sent"
        assert call_log

    async def test_unresolvable_target_requires_approval(self):
        """Tool call with unresolvable target is always pended."""
        pool = RoleAwareMockPool()
        # No contacts registered — target is unresolvable

        mock_mcp = _make_mock_mcp()
        call_log: list[dict] = []

        @mock_mcp.tool()
        async def telegram_send_message(chat_id: str, text: str) -> dict:
            call_log.append({"chat_id": chat_id})
            return {"status": "sent"}

        config = _make_approval_config("telegram_send_message")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["telegram_send_message"].fn
        result = await wrapper(chat_id="999999999", text="unknown target")

        assert result["status"] == "pending_approval"
        assert not call_log

    async def test_unresolvable_target_pends_even_with_standing_rule(self):
        """Unresolvable target requires approval even when standing rules exist."""
        pool = RoleAwareMockPool()
        # Add a standing rule but no contact info registered
        pool.add_rule("telegram_send_message")

        mock_mcp = _make_mock_mcp()
        call_log: list[dict] = []

        @mock_mcp.tool()
        async def telegram_send_message(chat_id: str, text: str) -> dict:
            call_log.append({"chat_id": chat_id})
            return {"status": "sent"}

        config = _make_approval_config("telegram_send_message")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["telegram_send_message"].fn
        result = await wrapper(chat_id="999999999", text="unknown target")

        # Unresolvable target → pend regardless of standing rules
        assert result["status"] == "pending_approval"
        assert not call_log

    async def test_no_channel_info_in_args_requires_approval(self):
        """Tool with no channel-identifying args requires approval."""
        pool = RoleAwareMockPool()

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def send_newsletter(content: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("send_newsletter")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["send_newsletter"].fn
        result = await wrapper(content="latest news")

        assert result["status"] == "pending_approval"

    async def test_owner_targeted_emits_owner_auto_approve_events(self):
        """Owner auto-approve emits ACTION_QUEUED + ACTION_AUTO_APPROVED events."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def notify(channel: str, recipient: str, message: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("notify")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["notify"].fn
        await wrapper(channel="telegram", recipient=OWNER_CHAT_ID, message="Hi")

        event_types = [e["event_type"] for e in pool.approval_events]
        assert "action_queued" in event_types or any(
            "queued" in str(et).lower() for et in event_types
        ), f"Expected action_queued event, got {event_types}"
        # At least one auto_approved event
        assert any("auto_approved" in str(et).lower() for et in event_types), (
            f"Expected auto_approved event, got {event_types}"
        )

    async def test_owner_auto_approve_event_has_owner_path(self):
        """Owner auto-approve events carry 'owner_auto_approve' path metadata."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def notify(channel: str, recipient: str, message: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("notify")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["notify"].fn
        await wrapper(channel="telegram", recipient=OWNER_CHAT_ID, message="Hi")

        queued_events = [
            e for e in pool.approval_events if "queued" in str(e.get("event_type", "")).lower()
        ]
        assert any(
            e.get("event_metadata", {}).get("path") == "owner_auto_approve" for e in queued_events
        ), f"Expected owner_auto_approve path in queued event metadata, got {queued_events}"

    async def test_non_owner_pending_event_has_pending_path(self):
        """Non-owner pend events carry 'pending' path metadata."""
        pool = RoleAwareMockPool()
        pool.register_contact("email", NON_OWNER_EMAIL, _make_non_owner_contact())

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("email_send_message")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["email_send_message"].fn
        await wrapper(to=NON_OWNER_EMAIL, subject="Hi", body="Hello")

        queued_events = [
            e for e in pool.approval_events if "queued" in str(e.get("event_type", "")).lower()
        ]
        assert any(e.get("event_metadata", {}).get("path") == "pending" for e in queued_events), (
            f"Expected pending path in queued event metadata, got {queued_events}"
        )

    async def test_owner_by_contact_id_auto_approves(self):
        """Owner resolved via explicit contact_id parameter auto-approves."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())

        mock_mcp = _make_mock_mcp()
        call_log: list[dict] = []

        @mock_mcp.tool()
        async def notify(contact_id: str, message: str) -> dict:
            call_log.append({"contact_id": contact_id})
            return {"status": "sent"}

        config = _make_approval_config("notify")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["notify"].fn
        result = await wrapper(contact_id=str(OWNER_CONTACT_ID), message="Direct contact_id owner")

        assert result.get("status") == "sent"
        assert call_log

    async def test_non_owner_pending_action_has_pending_status(self):
        """Pending action for non-owner has 'pending' status in DB."""
        pool = RoleAwareMockPool()
        pool.register_contact("email", NON_OWNER_EMAIL, _make_non_owner_contact())

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("email_send_message")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["email_send_message"].fn
        result = await wrapper(to=NON_OWNER_EMAIL, subject="Hi", body="Hello")

        action_id = uuid.UUID(result["action_id"])
        assert action_id in pool.pending_actions
        action = pool.pending_actions[action_id]
        assert action["status"] == ActionStatus.PENDING.value

    async def test_unresolvable_pending_action_has_pending_status(self):
        """Pending action for unresolvable target has 'pending' status in DB."""
        pool = RoleAwareMockPool()

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def telegram_send_message(chat_id: str, text: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("telegram_send_message")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["telegram_send_message"].fn
        result = await wrapper(chat_id="999999", text="hi unknown")

        action_id = uuid.UUID(result["action_id"])
        assert action_id in pool.pending_actions
        action = pool.pending_actions[action_id]
        assert action["status"] == ActionStatus.PENDING.value

    async def test_non_owner_rule_auto_approve_increments_use_count(self):
        """Standing rule use_count is incremented for non-owner auto-approvals."""
        pool = RoleAwareMockPool()
        pool.register_contact("email", NON_OWNER_EMAIL, _make_non_owner_contact())
        rule_id = pool.add_rule("email_send_message")

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def email_send_message(to: str, subject: str, body: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("email_send_message")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["email_send_message"].fn
        await wrapper(to=NON_OWNER_EMAIL, subject="Hi", body="Hello")

        rule = next((r for r in pool.approval_rules if r["id"] == rule_id), None)
        assert rule is not None
        assert rule["use_count"] == 1

    async def test_owner_auto_approve_no_rule_used(self):
        """Owner auto-approve path does not increment any standing rule."""
        pool = RoleAwareMockPool()
        pool.register_contact("telegram", OWNER_CHAT_ID, _make_owner_contact())
        rule_id = pool.add_rule("notify")  # Standing rule exists but should not be used

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def notify(channel: str, recipient: str, message: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("notify")
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["notify"].fn
        await wrapper(channel="telegram", recipient=OWNER_CHAT_ID, message="Hi owner")

        rule = next((r for r in pool.approval_rules if r["id"] == rule_id), None)
        assert rule is not None
        assert rule["use_count"] == 0, "Standing rule should NOT be used for owner auto-approve"

    async def test_pending_response_structure(self):
        """Pending approval response has correct structure and fields."""
        pool = RoleAwareMockPool()
        # Unresolvable target for clean pending path

        mock_mcp = _make_mock_mcp()

        @mock_mcp.tool()
        async def telegram_send_message(chat_id: str, text: str) -> dict:
            return {"status": "sent"}

        config = _make_approval_config("telegram_send_message", risk_tier=ApprovalRiskTier.HIGH)
        await apply_approval_gates(mock_mcp, config, pool)

        wrapper = (await mock_mcp._tool_manager.get_tools())["telegram_send_message"].fn
        result = await wrapper(chat_id="000000", text="test")

        assert result["status"] == "pending_approval"
        assert "action_id" in result
        uuid.UUID(result["action_id"])  # Valid UUID
        assert "message" in result
        assert result["risk_tier"] == "high"
        assert "rule_precedence" in result
        assert tuple(result["rule_precedence"]) == DEFAULT_APPROVAL_RULE_PRECEDENCE
