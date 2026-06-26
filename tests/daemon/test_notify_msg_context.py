"""Tests for msg_context parameter in notify() and context-aware resolution.

Covers bu-uv4b4 acceptance criteria (updated for bu-km8xr entity-direct migration):
  - _resolve_entity_channel_identifier uses entity_facts (not contact_info) for all lookups
  - msg_context is no longer used for ordering (entity_facts has no context column), but is
    still passed to check_email_recipient for validation
  - context mismatch causes pending_approval response
  - returns None gracefully when entity_facts table is absent

Migration note (bu-km8xr):
  Resolution queries relationship.entity_facts keyed directly on entity_id — no
  public.contacts indirection. The msg_context parameter is no longer used during
  resolution (since entity_facts has no context column) but continues to be used
  downstream by the email guard for context-mismatch validation.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_ENTITY_ID = uuid.UUID("aabbccdd-0000-0000-0000-000000000001")


def _make_entity_facts_conn(
    entity_id: uuid.UUID | None = _ENTITY_ID,
    facts_value: str | None = "resolved@example.com",
    fetchrow_error: Exception | None = None,
) -> AsyncMock:
    """Return a mock connection simulating entity-direct entity_facts resolution.

    The resolver queries relationship.entity_facts keyed on the entity_id — there
    is no public.contacts indirection step.
    """
    mock_conn = AsyncMock()

    if fetchrow_error is not None:
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_error)
        return mock_conn

    async def _fetchrow(query: str, *args, **kwargs):
        if "entity_facts" in query:
            if facts_value is None:
                return None
            return {"object": facts_value}
        return None

    mock_conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    return mock_conn


def _make_mock_pool_with_entity_facts(
    entity_id: uuid.UUID | None = _ENTITY_ID,
    facts_value: str | None = "resolved@example.com",
    fetchrow_error: Exception | None = None,
):
    """Return (pool, conn) with entity-direct entity_facts resolution support."""
    mock_conn = _make_entity_facts_conn(
        entity_id=entity_id,
        facts_value=facts_value,
        fetchrow_error=fetchrow_error,
    )

    mock_pool = AsyncMock()

    @asynccontextmanager
    async def mock_acquire():
        yield mock_conn

    mock_pool.acquire = mock_acquire
    return mock_pool, mock_conn


def _make_daemon_with_pool(pool):
    """Return a minimal daemon mock wired to *pool*."""
    from butlers.daemon import ButlerDaemon

    daemon = MagicMock(spec=ButlerDaemon)
    daemon._CHANNEL_TO_PREDICATE = ButlerDaemon._CHANNEL_TO_PREDICATE
    daemon._TELEGRAM_HANDLE_PREFIX = ButlerDaemon._TELEGRAM_HANDLE_PREFIX
    daemon._CHANNEL_TO_CONTACT_INFO_TYPE = ButlerDaemon._CHANNEL_TO_CONTACT_INFO_TYPE
    mock_db = MagicMock()
    mock_db.pool = pool
    daemon.db = mock_db
    return daemon


# ---------------------------------------------------------------------------
# _resolve_entity_channel_identifier entity_facts query tests
# ---------------------------------------------------------------------------


class TestResolveEntityChannelIdentifierEntityFacts:
    """Unit tests for entity_facts-based resolution in _resolve_entity_channel_identifier."""

    async def test_resolution_uses_entity_facts_not_contact_info(self) -> None:
        """Resolution queries entity_facts, never contact_info."""
        from butlers.daemon import ButlerDaemon

        mock_pool, mock_conn = _make_mock_pool_with_entity_facts(
            entity_id=_ENTITY_ID,
            facts_value="personal@example.com",
        )
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon, entity_id=uuid.uuid4(), channel="email"
        )

        assert result == "personal@example.com"
        # Must query entity_facts
        queries = [c.args[0] for c in mock_conn.fetchrow.await_args_list]
        assert any("relationship.entity_facts" in q for q in queries)
        # Must NOT query contact_info
        assert not any("contact_info" in q for q in queries)

    async def test_msg_context_does_not_change_query_structure(self) -> None:
        """When msg_context is provided, the query structure is the same as without it.

        entity_facts has no context column; msg_context is no longer used for
        ordering during resolution.  Both with and without msg_context should
        produce the same entity-direct entity_facts query.
        """
        from butlers.daemon import ButlerDaemon

        mock_pool_with, mock_conn_with = _make_mock_pool_with_entity_facts(
            entity_id=_ENTITY_ID,
            facts_value="addr@example.com",
        )
        mock_pool_without, mock_conn_without = _make_mock_pool_with_entity_facts(
            entity_id=_ENTITY_ID,
            facts_value="addr@example.com",
        )

        daemon_with = _make_daemon_with_pool(mock_pool_with)
        daemon_without = _make_daemon_with_pool(mock_pool_without)

        result_with = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon_with, entity_id=uuid.uuid4(), channel="email", msg_context="personal"
        )
        result_without = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon_without, entity_id=uuid.uuid4(), channel="email"
        )

        assert result_with == "addr@example.com"
        assert result_without == "addr@example.com"

        # Both should have queried entity_facts (not contact_info)
        queries_with = [c.args[0] for c in mock_conn_with.fetchrow.await_args_list]
        queries_without = [c.args[0] for c in mock_conn_without.fetchrow.await_args_list]
        assert any("relationship.entity_facts" in q for q in queries_with)
        assert any("relationship.entity_facts" in q for q in queries_without)
        # Neither should have used contact_info
        assert not any("contact_info" in q for q in queries_with)
        assert not any("contact_info" in q for q in queries_without)
        # Neither should use the CASE/context ordering (no context column in entity_facts)
        all_queries_with = " ".join(queries_with)
        # CASE might appear in a different context; specifically check no "ci.context" pattern
        assert "ci.context" not in all_queries_with

    async def test_returns_none_when_no_pool(self) -> None:
        """Returns None immediately if no DB pool is available."""
        from butlers.daemon import ButlerDaemon

        daemon = _make_daemon_with_pool(None)

        result = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon, entity_id=uuid.uuid4(), channel="email", msg_context="personal"
        )
        assert result is None

    async def test_returns_none_on_missing_table_error(self) -> None:
        """Gracefully returns None when entity_facts table is missing (pre-migration)."""
        import asyncpg

        from butlers.daemon import ButlerDaemon

        mock_pool, _ = _make_mock_pool_with_entity_facts(
            fetchrow_error=asyncpg.UndefinedTableError("relation does not exist")
        )
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon, entity_id=uuid.uuid4(), channel="email", msg_context="personal"
        )
        assert result is None

    async def test_returns_none_on_relationship_schema_privilege_error(self) -> None:
        """Schema-isolated butlers do not fail notify() when relationship reads are blocked."""
        import asyncpg

        from butlers.daemon import ButlerDaemon

        mock_pool, _ = _make_mock_pool_with_entity_facts(
            fetchrow_error=asyncpg.InsufficientPrivilegeError(
                "permission denied for schema relationship"
            )
        )
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon, entity_id=uuid.uuid4(), channel="email", msg_context="personal"
        )

        assert result is None

    async def test_returns_none_when_no_fact_exists(self) -> None:
        """Returns None when no active entity_facts row exists for the entity/channel."""
        from butlers.daemon import ButlerDaemon

        mock_pool, _ = _make_mock_pool_with_entity_facts(
            entity_id=_ENTITY_ID,
            facts_value=None,  # no fact found
        )
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon, entity_id=uuid.uuid4(), channel="email"
        )
        assert result is None

    async def test_telegram_strips_prefix_from_handle(self) -> None:
        """Telegram resolution strips 'telegram:' prefix from has-handle object value."""
        from butlers.daemon import ButlerDaemon

        mock_pool, mock_conn = _make_mock_pool_with_entity_facts(
            entity_id=_ENTITY_ID,
            facts_value="telegram:12345678",
        )
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon, entity_id=uuid.uuid4(), channel="telegram"
        )
        # Numeric ID returned, prefix stripped
        assert result == "12345678"

        # Query must include LIKE filter for telegram: prefix disambiguation
        queries = [c.args[0] for c in mock_conn.fetchrow.await_args_list]
        ef_query = next(q for q in queries if "relationship.entity_facts" in q)
        assert "LIKE" in ef_query or "like" in ef_query.lower()

    async def test_resolution_is_single_query_entity_direct(self) -> None:
        """Resolution issues exactly one query — entity_facts, no public.contacts step."""
        from butlers.daemon import ButlerDaemon

        mock_pool, mock_conn = _make_mock_pool_with_entity_facts(
            entity_id=_ENTITY_ID,
            facts_value="result@example.com",
        )
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_entity_channel_identifier(
            daemon, entity_id=uuid.uuid4(), channel="email"
        )
        assert result == "result@example.com"
        assert mock_conn.fetchrow.await_count == 1
        queries = [c.args[0] for c in mock_conn.fetchrow.await_args_list]
        assert any("relationship.entity_facts" in q for q in queries)
        assert not any("public.contacts" in q for q in queries)


# ---------------------------------------------------------------------------
# check_email_recipient msg_context passthrough (isolated guard invocation)
# ---------------------------------------------------------------------------


class TestCheckEmailRecipientMsgContextPassthrough:
    """Verify msg_context is accepted and routed correctly in check_email_recipient."""

    async def test_context_mismatch_parks_delivery(self) -> None:
        """Personal message to a work-tagged address → parked."""
        from butlers.identity import ResolvedContact
        from butlers.modules.approvals.email_guard import check_email_recipient

        contact = ResolvedContact(
            contact_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            name="Friend",
            roles=["contact"],
        )
        pool = AsyncMock()

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=contact),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ),
        ):
            decision = await check_email_recipient(
                pool,
                email_target="friend@work.com",
                rule_tool_name="notify",
                rule_match_args={},
                park_tool_name="notify",
                park_tool_args={},
                park_summary="test",
                msg_context="personal",
            )

        assert decision.allowed is False
        assert decision.reason == "parked"
        assert decision.action_id is not None

    async def test_no_context_no_get_email_context_call(self) -> None:
        """When msg_context is None, _get_email_context is never called."""
        from butlers.identity import ResolvedContact
        from butlers.modules.approvals.email_guard import check_email_recipient

        contact = ResolvedContact(
            contact_id=uuid.uuid4(),
            entity_id=uuid.uuid4(),
            name="Friend",
            roles=["contact"],
        )
        pool = AsyncMock()

        with (
            patch(
                "butlers.identity.resolve_contact_by_channel",
                new=AsyncMock(return_value=contact),
            ),
            patch(
                "butlers.modules.approvals.email_guard._get_email_context",
                new=AsyncMock(return_value="work"),
            ) as mock_get_ctx,
            patch(
                "butlers.modules.approvals.rules.match_rules",
                new=AsyncMock(return_value=None),
            ),
        ):
            await check_email_recipient(
                pool,
                email_target="friend@work.com",
                rule_tool_name="notify",
                rule_match_args={},
                park_tool_name="notify",
                park_tool_args={},
                park_summary="test",
                # msg_context omitted → no context check
            )

        mock_get_ctx.assert_not_awaited()
