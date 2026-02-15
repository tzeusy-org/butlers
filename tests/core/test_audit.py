"""Tests for the daemon-side audit logging helper."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.audit import write_audit_entry

pytestmark = pytest.mark.unit


class TestWriteAuditEntry:
    async def test_inserts_correct_row(self):
        pool = MagicMock()
        pool.execute = AsyncMock()

        await write_audit_entry(
            pool,
            butler="my-butler",
            operation="session",
            request_summary={"session_id": "abc", "trigger_source": "tick"},
            result="success",
            error=None,
        )

        pool.execute.assert_awaited_once()
        args = pool.execute.call_args
        positional = args[0]
        # SQL statement
        assert "INSERT INTO dashboard_audit_log" in positional[0]
        # butler
        assert positional[1] == "my-butler"
        # operation
        assert positional[2] == "session"
        # request_summary is JSON string
        summary = json.loads(positional[3])
        assert summary == {"session_id": "abc", "trigger_source": "tick"}
        # result
        assert positional[4] == "success"
        # error
        assert positional[5] is None
        # user_context is empty JSON object
        assert json.loads(positional[6]) == {}

    async def test_noop_when_pool_is_none(self):
        """Should silently return without error when pool is None."""
        await write_audit_entry(
            None,
            butler="any",
            operation="session",
            request_summary={},
        )

    async def test_swallows_db_errors(self):
        """Should log a warning but not raise when the INSERT fails."""
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("connection lost"))

        with patch("butlers.core.audit.logger") as mock_logger:
            await write_audit_entry(
                pool,
                butler="my-butler",
                operation="session",
                request_summary={"key": "value"},
            )
            mock_logger.warning.assert_called_once()

    async def test_serializes_request_summary_as_json(self):
        pool = MagicMock()
        pool.execute = AsyncMock()

        summary = {"nested": {"list": [1, 2, 3]}, "flag": True}
        await write_audit_entry(pool, butler="b", operation="session", request_summary=summary)

        args = pool.execute.call_args[0]
        assert json.loads(args[3]) == summary

    async def test_error_result(self):
        pool = MagicMock()
        pool.execute = AsyncMock()

        await write_audit_entry(
            pool,
            butler="b",
            operation="session",
            request_summary={},
            result="error",
            error="RuntimeError: boom",
        )

        args = pool.execute.call_args[0]
        assert args[4] == "error"
        assert args[5] == "RuntimeError: boom"
