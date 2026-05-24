"""Incident replay: 2026-04-21 owner-routing safety regression test.

This module replays the original 2026-04-21 incident scenario against the
integrated gen-1 changes to verify that all four acceptance criteria of the
bu-7qfrg epic hold together end-to-end.

Scenario:
  The relationship butler ingested an email thread where the user asked
  "am I correct in understanding my ACME email would be owner@example.com?"
  — a speculative future work email.  The runtime LLM called contact_info_add
  against the owner contact, poisoning outbound email routing for ~3 days.

Covered acceptance criteria
---------------------------
1. bu-jwby9: Non-primary owner-email sends park for approval (not auto-approved).
2. bu-uv4b4: Context-aware notify() routing — personal message resolves to
   personal address, not work address.
3. bu-v6ttx: contact_info_add against the owner contact creates pending_action,
   NOT a DB row.
4. bu-m24ua: Dashboard DELETE on a contact_info row writes to audit log.

This test module uses unittest.mock throughout to avoid integration-test
dependencies (Docker, real DB).  It validates the code contracts.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

OWNER_CONTACT_ID = uuid.UUID("ccf6241a-01cd-40b2-817e-39643d50322b")
OWNER_ENTITY_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

PERSONAL_EMAIL = "tzehow@gmail.com"
WORK_EMAIL = "owner@example.com"


# ---------------------------------------------------------------------------
# AC #3 — contact_info_add against owner → pending_action, not DB row
# ---------------------------------------------------------------------------


class TestAC3OwnerGate:
    """bu-v6ttx: owner-contact contact_info_add is gated."""

    async def test_contact_info_add_owner_parks_acme_email(self) -> None:
        """Replays the 2026-04-21 incident: speculative work email write is parked.

        The relationship butler called contact_info_add with the owner contact_id
        and value='owner@example.com'.  With bu-v6ttx applied, the mutation
        must create a pending_action row rather than inserting into contact_info.
        """
        from butlers.tools.relationship.contact_info import contact_info_add

        # Build a minimal pool that knows:
        # - contacts table: owner contact exists
        # - entities table: contact has 'owner' role (via _is_owner_contact JOIN)
        # - pending_actions table: accept INSERT
        pool = AsyncMock()

        # contact_create check: fetchrow("SELECT id FROM contacts ...")
        # _is_owner_contact JOIN: fetchrow with entities JOIN
        side_effects: list = [
            # contact existence check → found
            MagicMock(**{"__getitem__.return_value": OWNER_CONTACT_ID}),
            # _is_owner_contact → row not None (owner found)
            MagicMock(),
        ]
        pool.fetchrow = AsyncMock(side_effect=side_effects)

        result = await contact_info_add(
            pool,
            OWNER_CONTACT_ID,
            "email",
            WORK_EMAIL,
            is_primary=False,
        )

        # Must return pending_approval, not a contact_info row
        assert result["status"] == "pending_approval", (
            f"Expected pending_approval but got: {result}"
        )
        assert "action_id" in result

        # Must have called INSERT INTO pending_actions (not contact_info)
        assert pool.execute.called, "pool.execute must be called for pending_actions INSERT"
        insert_call_args = pool.execute.call_args_list[0][0]
        sql = insert_call_args[0]
        assert "pending_actions" in sql, f"INSERT must target pending_actions, got: {sql}"
        assert "contact_info" not in sql, "Must NOT insert into contact_info for owner contact"

        # Verify args include the speculative email
        # tool_args is serialized as JSON in the INSERT; check the raw args
        tool_args_json = insert_call_args[3]  # 4th positional param after id, tool_name
        parsed = tool_args_json if isinstance(tool_args_json, dict) else json.loads(tool_args_json)
        assert parsed["value"] == WORK_EMAIL, f"pending_action must record the email: {parsed}"
        assert str(parsed["contact_id"]) == str(OWNER_CONTACT_ID)

    async def test_contact_info_add_non_owner_writes_immediately(self) -> None:
        """Non-owner contact_info_add must write directly (no gate).

        The non-owner path uses pool.acquire() as an async context manager
        and conn.transaction() for atomicity.  This test wires those correctly
        so the assertion exercises the actual code path.
        """
        from butlers.tools.relationship.contact_info import contact_info_add

        non_owner_id = uuid.uuid4()

        inserted_row = {
            "id": uuid.uuid4(),
            "contact_id": non_owner_id,
            "type": "email",
            "value": "nonowner@example.com",
            "label": None,
            "is_primary": False,
            "context": None,
            "created_at": None,
        }

        # Outer pool: handles fetchrow calls before acquire() (existence + owner check).
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                # 1. contact existence check → found
                MagicMock(**{"__getitem__.return_value": non_owner_id}),
                # 2. _is_owner_contact → None (not owner)
                None,
            ]
        )

        # conn is the connection obtained inside the async with pool.acquire() block
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=inserted_row)  # INSERT RETURNING
        conn.transaction = MagicMock(
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False)
            )
        )

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        result = await contact_info_add(pool, non_owner_id, "email", "nonowner@example.com")

        # Must return a real row, not pending_approval
        assert result.get("status") != "pending_approval", (
            "Non-owner contact_info_add must write immediately"
        )
        assert result["value"] == "nonowner@example.com"


# ---------------------------------------------------------------------------
# AC #1 — non-primary owner email send parks for approval
# ---------------------------------------------------------------------------


class TestAC1EmailGuardNonPrimary:
    """bu-jwby9: non-primary owner email address is not auto-approved."""

    async def test_non_primary_owner_email_parks(self) -> None:
        """Owner send to a non-primary email (acme address) must park for approval.

        This is the exact failure mode from the incident: with a acme email and a
        personal email both is_primary=false on the owner contact, a notify() to
        the acme address would have auto-approved under the old code.
        """
        from butlers.identity import ResolvedContact
        from butlers.modules.approvals.email_guard import check_email_recipient

        owner = ResolvedContact(
            contact_id=OWNER_CONTACT_ID,
            entity_id=OWNER_ENTITY_ID,
            name="Tze How Lee",
            roles=["owner"],
        )
        pool = AsyncMock()
        # Targeted address is NOT primary
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
            decision = await check_email_recipient(
                pool,
                email_target=WORK_EMAIL,
                rule_tool_name="notify",
                rule_match_args={"channel": "email", "message": "MOM helper paperwork update"},
                park_tool_name="notify",
                park_tool_args={
                    "channel": "email",
                    "message": "MOM helper paperwork update",
                    "recipient": WORK_EMAIL,
                },
                park_summary=f"notify() rejected: email to '{WORK_EMAIL}'",
                msg_context="personal",
            )

        assert decision.allowed is False, (
            "Non-primary owner email must be blocked (parked for approval)"
        )
        assert decision.reason == "parked"
        assert decision.action_id is not None

    async def test_primary_owner_email_auto_approves(self) -> None:
        """Owner send to is_primary=True address is still auto-approved (no regression)."""
        from butlers.identity import ResolvedContact
        from butlers.modules.approvals.email_guard import check_email_recipient

        owner = ResolvedContact(
            contact_id=OWNER_CONTACT_ID,
            entity_id=OWNER_ENTITY_ID,
            name="Tze How Lee",
            roles=["owner"],
        )
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value={"primary": True})

        with patch(
            "butlers.identity.resolve_contact_by_channel",
            new=AsyncMock(return_value=owner),
        ):
            decision = await check_email_recipient(
                pool,
                email_target=PERSONAL_EMAIL,
                rule_tool_name="notify",
                rule_match_args={},
                park_tool_name="notify",
                park_tool_args={},
                park_summary="test",
            )

        assert decision.allowed is True
        assert decision.reason == "owner"


# ---------------------------------------------------------------------------
# AC #2 — context-aware notify() routing
# ---------------------------------------------------------------------------


class TestAC2ContextAwareRouting:
    """bu-uv4b4: context-aware recipient resolution."""

    async def test_personal_msg_context_prefers_personal_address(self) -> None:
        """When msg_context='personal', the resolution query orders personal context first.

        This test verifies that _resolve_contact_channel_identifier uses the
        context-priority ORDER BY when msg_context is provided, selecting the
        personal email before the work email even if the work email was inserted
        first (i.e., has an earlier created_at).
        """
        from butlers.daemon import ButlerDaemon

        pool = AsyncMock()
        conn = AsyncMock()
        # Simulate DB returning the personal email (context='personal') as top row
        conn.fetchrow = AsyncMock(return_value={"value": PERSONAL_EMAIL})

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        mock_db = MagicMock()
        mock_db.pool = pool
        daemon = MagicMock(spec=ButlerDaemon)
        daemon._CHANNEL_TO_CONTACT_INFO_TYPE = {"email": "email"}
        daemon.db = mock_db
        daemon._resolve_contact_channel_identifier = (
            ButlerDaemon._resolve_contact_channel_identifier.__get__(daemon)
        )

        result = await daemon._resolve_contact_channel_identifier(
            contact_id=OWNER_CONTACT_ID,
            channel="email",
            msg_context="personal",
        )

        assert result == PERSONAL_EMAIL, (
            f"Context-aware resolution should return personal email, got: {result}"
        )

        # Verify the context-aware query was used (contains CASE expression)
        called_query = conn.fetchrow.call_args[0][0]
        assert "CASE" in called_query, "Context-aware query must use CASE ORDER BY"
        assert "context" in called_query, "Query must filter/order by context"

    async def test_context_mismatch_parks_email(self) -> None:
        """When msg_context='personal' but resolved address is tagged 'work', email parks.

        Even if a non-primary owner address somehow resolves, the context mismatch
        in the email guard should block delivery.
        """
        from butlers.identity import ResolvedContact
        from butlers.modules.approvals.email_guard import check_email_recipient

        non_owner = ResolvedContact(
            contact_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            name="Work contact",
            roles=["contact"],
        )
        pool = AsyncMock()

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=non_owner),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ),
        ):
            decision = await check_email_recipient(
                pool,
                email_target=WORK_EMAIL,
                rule_tool_name="notify",
                rule_match_args={},
                park_tool_name="notify",
                park_tool_args={},
                park_summary="personal message to work email",
                msg_context="personal",
            )

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.action_id is not None


# ---------------------------------------------------------------------------
# AC #4 — dashboard DELETE writes audit row
# ---------------------------------------------------------------------------


class TestAC4DashboardAudit:
    """bu-m24ua: dashboard mutations are audit-logged."""

    async def test_emit_dashboard_audit_delete(self) -> None:
        """emit_dashboard_audit for a contact_info DELETE writes to dashboard_audit_log."""
        from butlers.api.audit_emit import emit_dashboard_audit

        pool = AsyncMock()
        db_manager = MagicMock()
        db_manager.pool = MagicMock(return_value=pool)

        info_id = uuid.uuid4()
        contact_id = OWNER_CONTACT_ID

        await emit_dashboard_audit(
            db_manager,
            butler="relationship",
            operation="contact_info_delete",
            method="DELETE",
            path=f"/api/relationship/contacts/{contact_id}/contact-info/{info_id}",
            path_params={"contact_id": str(contact_id), "info_id": str(info_id)},
            response_status=204,
        )

        assert pool.execute.called, "emit_dashboard_audit must call pool.execute"
        insert_sql = pool.execute.call_args[0][0]
        assert "dashboard_audit_log" in insert_sql, (
            f"Must INSERT into dashboard_audit_log, got: {insert_sql}"
        )

        # Verify operation and butler are recorded correctly
        call_args = pool.execute.call_args[0]
        butler_arg = call_args[1]
        operation_arg = call_args[2]
        assert butler_arg == "relationship"
        assert operation_arg == "contact_info_delete"

    async def test_middleware_fires_on_delete(self) -> None:
        """DashboardAuditMiddleware records DELETE /api/... to audit log."""
        # This is verified by the existing test in
        # tests/api/test_dashboard_audit_middleware.py::TestDashboardAuditMiddlewareIntegration
        # ::test_middleware_fires_on_delete (line 158).
        # We include a lightweight unit assertion here for incident replay clarity.
        from butlers.api.dashboard_audit_middleware import _MUTATING_METHODS, _infer_butler

        assert "DELETE" in _MUTATING_METHODS, "DELETE must be in mutating methods"
        assert "GET" not in _MUTATING_METHODS, "GET must NOT be in mutating methods"
        assert _infer_butler("/api/relationship/contacts/xxx/contact-info/yyy") == "relationship"

    def test_redact_body_strips_value_field(self) -> None:
        """contact_info.value (which may contain credentials) is redacted from audit logs."""
        from butlers.api.audit_emit import redact_body

        body = {
            "type": "email",
            "value": "owner@example.com",
            "is_primary": False,
        }
        redacted = redact_body(body)
        assert redacted["value"] == "[REDACTED]", "value field must be redacted in audit logs"
        assert redacted["type"] == "email"
        assert redacted["is_primary"] is False
