"""Tests for the daemon-side audit logging helper."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.audit import write_audit_entry

pytestmark = pytest.mark.unit


class TestWriteAuditEntry:
    async def test_audit_entry_fields_and_serialization(self):
        """Correct SQL/fields on success; passes plain dict for JSONB codec; error result recorded."""
        pool = MagicMock()
        pool.execute = AsyncMock()

        # Basic success case
        await write_audit_entry(
            pool,
            butler="my-butler",
            operation="session",
            request_summary={"session_id": "abc", "trigger_source": "tick"},
            result="success",
            error=None,
        )
        pool.execute.assert_awaited_once()
        args = pool.execute.call_args[0]
        assert "INSERT INTO dashboard_audit_log" in args[0]
        assert args[1] == "my-butler"
        assert args[2] == "session"
        # request_summary is passed as a plain dict (not a JSON string) so that
        # the registered asyncpg JSONB codec handles encoding without double-encoding.
        assert args[3] == {"session_id": "abc", "trigger_source": "tick"}
        assert args[4] == "success"
        assert args[5] is None
        # user_context is passed as a plain empty dict
        assert args[6] == {}

        # Complex nested summary
        pool.execute.reset_mock()
        complex_summary = {"nested": {"list": [1, 2, 3]}, "flag": True}
        await write_audit_entry(
            pool, butler="b", operation="session", request_summary=complex_summary
        )
        assert pool.execute.call_args[0][3] == complex_summary

        # Error result
        pool.execute.reset_mock()
        await write_audit_entry(
            pool,
            butler="b",
            operation="session",
            request_summary={},
            result="error",
            error="RuntimeError: boom",
        )
        err_args = pool.execute.call_args[0]
        assert err_args[4] == "error"
        assert err_args[5] == "RuntimeError: boom"

    async def test_noop_and_swallows_errors(self):
        """Silently returns when pool=None; swallows DB errors with a warning."""
        # pool=None → no raise
        await write_audit_entry(None, butler="any", operation="session", request_summary={})

        # DB error → warning, no raise
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("connection lost"))
        with patch("butlers.core.audit.logger") as mock_logger:
            await write_audit_entry(
                pool, butler="my-butler", operation="session", request_summary={"key": "value"}
            )
            mock_logger.warning.assert_called_once()
