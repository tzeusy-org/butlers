"""Tests for the daemon-side audit logging helper.

As of bu-h47nm, ``write_audit_entry`` routes writes through
:func:`butlers.api.routers.audit.append` into the canonical
``public.audit_log`` table (legacy ``dashboard_audit_log`` is no longer
written by this helper).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.audit import write_audit_entry

pytestmark = pytest.mark.unit


class TestWriteAuditEntry:
    async def test_routes_to_public_audit_log_append(self):
        """butler->actor, operation->action, summary->metadata, result/error mapped."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=42)

        await write_audit_entry(
            pool,
            butler="my-butler",
            operation="session",
            request_summary={"session_id": "abc", "trigger_source": "tick"},
            result="success",
            error=None,
        )

        pool.fetchval.assert_awaited_once()
        args = pool.fetchval.call_args[0]
        # Canonical table, not the legacy dashboard table.
        assert "INSERT INTO public.audit_log" in args[0]
        assert "dashboard_audit_log" not in args[0]
        # actor <- butler, action <- operation
        assert args[1] == "my-butler"
        assert args[2] == "session"
        # target <- request_summary.path (absent here -> None)
        assert args[3] is None
        # metadata column ($7) carries the original request_summary as JSON.
        metadata_json = args[7]
        assert isinstance(metadata_json, str)
        assert '"session_id"' in metadata_json
        assert '"trigger_source"' in metadata_json
        # result / error columns ($8 / $9)
        assert args[8] == "success"
        assert args[9] is None

    async def test_path_maps_to_target_and_error_recorded(self):
        """request_summary.path -> target; error result/message land on their columns."""
        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value=7)

        await write_audit_entry(
            pool,
            butler="b",
            operation="session",
            request_summary={"path": "/api/foo", "k": "v"},
            result="error",
            error="RuntimeError: boom",
        )

        args = pool.fetchval.call_args[0]
        assert args[3] == "/api/foo"  # target
        assert args[8] == "error"
        assert args[9] == "RuntimeError: boom"

    async def test_noop_and_swallows_errors(self):
        """Silently returns when pool=None; swallows DB errors with a warning."""
        # pool=None -> no raise, no append call
        await write_audit_entry(None, butler="any", operation="session", request_summary={})

        # DB error -> swallowed, no raise
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("connection lost"))
        await write_audit_entry(
            pool, butler="my-butler", operation="session", request_summary={"key": "value"}
        )
