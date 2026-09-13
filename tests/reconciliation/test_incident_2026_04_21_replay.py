"""Incident replay: 2026-04-21 owner-routing safety regression test.

This module replays the original 2026-04-21 incident scenario against the
integrated gen-1 changes to verify that all four acceptance criteria of the
bu-7qfrg epic hold together end-to-end.

Scenario:
  The relationship butler ingested an email thread where the user asked
  "am I correct in understanding my QRT email would be TzeHow.Lee@qube-rt.com?"
  — a speculative future work email.  The runtime LLM called channel_add
  against the owner contact, poisoning outbound email routing for ~3 days.

Covered acceptance criteria
---------------------------
1. bu-jwby9: Non-primary owner-email sends park for approval (not auto-approved).
2. bu-uv4b4: Context-aware notify() routing — personal message resolves to
   personal address, not work address.
3. bu-v6ttx: channel_add against the owner contact creates pending_action,
   NOT a DB row.
4. bu-m24ua: Dashboard DELETE on a contact_info row writes to audit log.

This test module uses unittest.mock throughout to avoid integration-test
dependencies (Docker, real DB).  It validates the code contracts.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.testing.approval_parking_fake import record_pending_action

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_pool_park_recorder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "butlers.modules.approvals.email_guard.park_pending_action",
        record_pending_action,
    )


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

OWNER_CONTACT_ID = uuid.UUID("ccf6241a-01cd-40b2-817e-39643d50322b")
OWNER_ENTITY_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")

PERSONAL_EMAIL = "tzehow@gmail.com"
WORK_EMAIL = "TzeHow.Lee@qube-rt.com"


# ---------------------------------------------------------------------------
# AC #3 — channel_add against owner → pending_action, not DB row
# ---------------------------------------------------------------------------


def _channel_module():
    """Return the module that owns channel_add's writer binding."""
    import importlib

    return importlib.import_module("butlers.tools.relationship.channel")


def _assert_result(outcome: str, *, fact_id=None, action_id=None):
    r = MagicMock()
    r.outcome.value = outcome
    r.fact_id = fact_id
    r.action_id = action_id
    return r


class TestAC3OwnerGate:
    """bu-v6ttx + bu-k9ylx: owner-contact channel writes are gated.

    Post write-path cut-over (bu-k9ylx): channel_add no longer INSERTs into
    public.contact_info. It resolves the contact's entity_id and asserts the
    channel triple via relationship_assert_fact(), which owns the RFC 0017 owner
    carve-out (returns pending_approval for owner-role entities). The original
    2026-04-21 incident protection now lives in the central writer.
    """

    async def test_channel_add_owner_parks_qube_email(self) -> None:
        """Replays the 2026-04-21 incident: speculative work email write is parked.

        The central writer returns pending_approval for the owner entity, and
        channel_add surfaces that without any contact_info INSERT.
        """
        from butlers.tools.relationship.channel import channel_add

        pool = MagicMock()
        # resolve_contact_entity_id now queries contacts_source_links.local_entity_id
        # (contacts-schema retirement, bu-ozpyl) rather than public.contacts.entity_id.
        pool.fetchrow = AsyncMock(return_value={"local_entity_id": OWNER_ENTITY_ID})
        pool.execute = AsyncMock()

        action_id = uuid.uuid4()
        with patch.object(
            _channel_module(), "relationship_assert_fact", new_callable=AsyncMock
        ) as writer:
            writer.return_value = _assert_result("pending_approval", action_id=action_id)
            result = await channel_add(
                pool, OWNER_CONTACT_ID, "email", WORK_EMAIL, is_primary=False
            )

        # Central writer owns the owner carve-out; called with the resolved
        # entity as subject, has-email predicate, and the speculative email.
        writer.assert_awaited_once()
        call = writer.call_args
        assert call.args[1] == OWNER_ENTITY_ID  # subject = entity_id
        assert call.args[2] == "has-email"
        assert call.args[3] == WORK_EMAIL

        # Surfaced as pending_approval; no contact_info INSERT (read-only table).
        assert result["status"] == "pending_approval", f"got: {result}"
        assert result["action_id"] == str(action_id)
        pool.execute.assert_not_called()

    async def test_channel_add_non_owner_asserts_triple(self) -> None:
        """Non-owner channel_add writes via the central writer (no gate).

        Post-cut-over the non-owner path asserts a channel triple through
        relationship_assert_fact() — there is NO direct contact_info INSERT.
        """
        from butlers.tools.relationship.channel import channel_add

        non_owner_id = uuid.uuid4()
        non_owner_entity = uuid.uuid4()

        pool = MagicMock()
        # resolve_contact_entity_id now queries contacts_source_links.local_entity_id
        # (contacts-schema retirement, bu-ozpyl) rather than public.contacts.entity_id.
        pool.fetchrow = AsyncMock(return_value={"local_entity_id": non_owner_entity})
        pool.execute = AsyncMock()

        fact_id = uuid.uuid4()
        with patch.object(
            _channel_module(), "relationship_assert_fact", new_callable=AsyncMock
        ) as writer:
            writer.return_value = _assert_result("inserted", fact_id=fact_id)
            result = await channel_add(pool, non_owner_id, "email", "nonowner@example.com")

        writer.assert_awaited_once()
        assert writer.call_args.args[1] == non_owner_entity
        assert writer.call_args.args[2] == "has-email"
        assert writer.call_args.args[3] == "nonowner@example.com"
        # No direct contact_info DML — the triple write is the only write.
        pool.execute.assert_not_called()
        assert result["status"] == "asserted"
        assert result["fact_id"] == str(fact_id)


# ---------------------------------------------------------------------------
# Owner decision amendment — unique owner email association bypasses approval
# ---------------------------------------------------------------------------


class TestOwnerEmailAssociation:
    """The current owner decision supersedes outbound email primacy gating."""

    async def test_secondary_owner_email_auto_approves(self) -> None:
        """A unique active owner association auto-approves regardless of primacy."""
        from butlers.identity import ResolvedContact
        from butlers.modules.approvals.email_guard import check_email_recipient

        owner = ResolvedContact(
            contact_id=OWNER_CONTACT_ID,
            entity_id=OWNER_ENTITY_ID,
            name="Tze How Lee",
            roles=["owner"],
        )
        pool = AsyncMock()
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
                butler_name="messenger",
            )

        assert decision.allowed is True
        assert decision.reason == "owner"

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
                butler_name="messenger",
            )

        assert decision.allowed is True
        assert decision.reason == "owner"


# ---------------------------------------------------------------------------
# AC #2 — context-aware notify() routing
# ---------------------------------------------------------------------------


class TestAC2ContextAwareRouting:
    """bu-uv4b4: context-aware recipient resolution."""

    async def test_personal_msg_context_resolves_via_entity_facts(self) -> None:
        """Resolution with msg_context now queries entity_facts (not contact_info).

        Migration bead bu-km8xr: _resolve_entity_channel_identifier reads from
        relationship.entity_facts keyed directly on entity_id.  msg_context is no
        longer used for DB-level ordering (entity_facts has no context column);
        context-mismatch enforcement happens downstream in the email guard.

        This test verifies that resolution queries entity_facts and returns the
        active has-email value, regardless of msg_context.
        """
        from butlers.daemon import ButlerDaemon

        entity_id = OWNER_ENTITY_ID
        pool = AsyncMock()
        conn = AsyncMock()

        async def _ef_fetchrow(query: str, *args, **kwargs):
            if "entity_facts" in query:
                return {"object": PERSONAL_EMAIL}
            return None

        conn.fetchrow = AsyncMock(side_effect=_ef_fetchrow)

        @asynccontextmanager
        async def mock_acquire():
            yield conn

        pool.acquire = mock_acquire

        mock_db = MagicMock()
        mock_db.pool = pool
        daemon = MagicMock(spec=ButlerDaemon)
        daemon._CHANNEL_TO_PREDICATE = ButlerDaemon._CHANNEL_TO_PREDICATE
        daemon._TELEGRAM_HANDLE_PREFIX = ButlerDaemon._TELEGRAM_HANDLE_PREFIX
        daemon._CHANNEL_TO_CONTACT_INFO_TYPE = ButlerDaemon._CHANNEL_TO_CONTACT_INFO_TYPE
        daemon.db = mock_db
        daemon._resolve_entity_channel_identifier = (
            ButlerDaemon._resolve_entity_channel_identifier.__get__(daemon)
        )

        result = await daemon._resolve_entity_channel_identifier(
            entity_id=entity_id,
            channel="email",
            msg_context="personal",
        )

        assert result == PERSONAL_EMAIL, (
            f"Resolution should return active has-email from entity_facts, got: {result}"
        )

        # Verify entity_facts was queried (not contact_info)
        queries = [c.args[0] for c in conn.fetchrow.await_args_list]
        assert any("relationship.entity_facts" in q for q in queries), (
            "Must query relationship.entity_facts"
        )
        assert not any("contact_info" in q for q in queries), (
            "Must NOT query public.contact_info (migrated to entity_facts)"
        )

    async def test_context_mismatch_parks_email(self) -> None:
        """When msg_context='personal' but resolved address is tagged 'work', email parks.

        A non-owner address with conflicting context remains approval-gated.
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
                butler_name="messenger",
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
        """emit_dashboard_audit for a contact_info DELETE appends to public.audit_log.

        As of bu-h47nm the writer routes through audit.append() into the
        canonical ``public.audit_log`` table.  Reads come solely from
        ``public.audit_log`` (bu-j26e8 removed the legacy UNION arm after
        core_124 backfilled the historical ``dashboard_audit_log`` rows).
        """
        from butlers.api.audit_emit import emit_dashboard_audit

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value=1)
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

        assert pool.fetchval.called, "emit_dashboard_audit must append via pool.fetchval"
        insert_sql = pool.fetchval.call_args[0][0]
        assert "public.audit_log" in insert_sql, (
            f"Must INSERT into public.audit_log, got: {insert_sql}"
        )

        # Verify actor (<- butler), action (<- operation) and target (<- path).
        call_args = pool.fetchval.call_args[0]
        assert call_args[1] == "relationship"  # actor
        assert call_args[2] == "contact_info_delete"  # action
        assert call_args[3] == (
            f"/api/relationship/contacts/{contact_id}/contact-info/{info_id}"
        )  # target

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
            "value": "TzeHow.Lee@qube-rt.com",
            "is_primary": False,
        }
        redacted = redact_body(body)
        assert redacted["value"] == "[REDACTED]", "value field must be redacted in audit logs"
        assert redacted["type"] == "email"
        assert redacted["is_primary"] is False
