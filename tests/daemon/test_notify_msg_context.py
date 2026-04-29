"""Tests for msg_context parameter in notify() and context-aware resolution.

Covers bu-uv4b4 acceptance criteria:
  - _resolve_contact_channel_identifier accepts msg_context and uses context-priority ORDER BY
  - notify() threads msg_context through to check_email_recipient
  - context mismatch causes pending_approval response
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _make_mock_pool_with_conn(fetchrow_result=None, fetchrow_error=None):
    """Return (pool, conn) with the asynccontextmanager pattern for pool.acquire()."""
    mock_conn = AsyncMock()
    if fetchrow_error is not None:
        mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_error)
    else:
        mock_conn.fetchrow = AsyncMock(return_value=fetchrow_result)

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
    daemon._CHANNEL_TO_CONTACT_INFO_TYPE = {}
    mock_db = MagicMock()
    mock_db.pool = pool
    daemon.db = mock_db
    return daemon


# ---------------------------------------------------------------------------
# _resolve_contact_channel_identifier context-aware query tests
# ---------------------------------------------------------------------------


class TestResolveContactChannelIdentifierContext:
    """Unit tests for context-aware SQL query selection in resolution."""

    async def test_context_aware_query_used_when_msg_context_provided(self) -> None:
        """When msg_context is given, the context-priority CASE ORDER BY query is used."""
        from butlers.daemon import ButlerDaemon

        mock_pool, mock_conn = _make_mock_pool_with_conn({"value": "personal@example.com"})
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_contact_channel_identifier(
            daemon, contact_id=uuid.uuid4(), channel="email", msg_context="personal"
        )

        assert result == "personal@example.com"
        query = mock_conn.fetchrow.await_args.args[0]
        # Must use the CASE expression for context-priority ordering
        assert "CASE" in query
        assert "context" in query
        # msg_context must be passed as a positional argument
        args = mock_conn.fetchrow.await_args.args
        assert "personal" in args

    async def test_legacy_query_used_when_no_msg_context(self) -> None:
        """When msg_context is None, the simple is_primary DESC query is used."""
        from butlers.daemon import ButlerDaemon

        mock_pool, mock_conn = _make_mock_pool_with_conn({"value": "any@example.com"})
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_contact_channel_identifier(
            daemon, contact_id=uuid.uuid4(), channel="email"
        )

        assert result == "any@example.com"
        query = mock_conn.fetchrow.await_args.args[0]
        # Must NOT use the CASE expression
        assert "CASE" not in query
        assert "is_primary DESC" in query

    async def test_returns_none_when_no_pool(self) -> None:
        """Returns None immediately if no DB pool is available."""
        from butlers.daemon import ButlerDaemon

        daemon = _make_daemon_with_pool(None)

        result = await ButlerDaemon._resolve_contact_channel_identifier(
            daemon, contact_id=uuid.uuid4(), channel="email", msg_context="personal"
        )
        assert result is None

    async def test_returns_none_on_missing_column_error(self) -> None:
        """Gracefully returns None when context column is missing (pre-migration)."""
        import asyncpg

        from butlers.daemon import ButlerDaemon

        mock_pool, _ = _make_mock_pool_with_conn(
            fetchrow_error=asyncpg.UndefinedColumnError("column context does not exist")
        )
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_contact_channel_identifier(
            daemon, contact_id=uuid.uuid4(), channel="email", msg_context="personal"
        )
        assert result is None

    async def test_context_query_falls_back_when_no_match(self) -> None:
        """Returns None when no contact_info row exists (even with context)."""
        from butlers.daemon import ButlerDaemon

        mock_pool, _ = _make_mock_pool_with_conn(None)
        daemon = _make_daemon_with_pool(mock_pool)

        result = await ButlerDaemon._resolve_contact_channel_identifier(
            daemon, contact_id=uuid.uuid4(), channel="email", msg_context="personal"
        )
        assert result is None


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
