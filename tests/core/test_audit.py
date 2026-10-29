"""Tests for the daemon-side audit logging helper.

As of bu-h47nm, ``write_audit_entry`` routes writes through
:func:`butlers.api.routers.audit.append` into the canonical
``public.audit_log`` table (legacy ``dashboard_audit_log`` is no longer
written by this helper).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from butlers.core.audit import AuditTableNotAvailableError, append, write_audit_entry

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
        # metadata column ($7) carries the original request_summary (nested
        # under "request_summary") as a dict -- bound directly, not a
        # pre-serialized JSON string (append() binds dicts straight to the
        # jsonb-codec-registered connection).
        metadata = args[7]
        assert isinstance(metadata, dict)
        assert metadata["request_summary"]["session_id"] == "abc"
        assert metadata["request_summary"]["trigger_source"] == "tick"
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


@pytest.mark.parametrize(
    "storage", ["absent", "no_receipt", "denied", "unmigrated", "disconnected"]
)
async def test_required_append_refuses_unavailable_storage_but_diagnostic_continues(storage):
    """Required writes cannot return success without a persisted receipt."""
    pool = None if storage == "absent" else MagicMock(fetchval=AsyncMock(return_value=None))
    failures = {
        "denied": asyncpg.InsufficientPrivilegeError("controlled denial"),
        "unmigrated": asyncpg.UndefinedColumnError("controlled missing column"),
        "disconnected": asyncpg.ConnectionDoesNotExistError("controlled disconnection"),
    }
    if storage in failures:
        pool.fetchval.side_effect = failures[storage]
    with pytest.raises(AuditTableNotAvailableError):
        await append(pool, "owner", "permission.set")
    await write_audit_entry(pool, "general", "session", {})


@pytest.mark.parametrize(
    "failure",
    [
        asyncio.CancelledError(),
        RuntimeError("controlled bug"),
        asyncpg.InterfaceError("controlled misuse"),
    ],
)
async def test_required_append_preserves_cancellation_and_programming_errors(failure):
    pool = MagicMock(fetchval=AsyncMock(side_effect=failure))
    with pytest.raises(type(failure)) as observed:
        await append(pool, "owner", "permission.set")
    assert observed.value is failure


def _native_connection_without_network():
    """Exercise native Connection lifetime checks with an isolated protocol."""
    connected = True

    async def close_protocol(_timeout):
        nonlocal connected
        connected = False

    protocol = SimpleNamespace(
        is_connected=lambda: connected,
        get_settings=lambda: SimpleNamespace(server_version="17.0"),
        close=close_protocol,
    )
    config = asyncpg.connect_utils._ClientConfiguration(
        command_timeout=None,
        statement_cache_size=0,
        max_cached_statement_lifetime=0,
        max_cacheable_statement_size=0,
    )
    return asyncpg.Connection(protocol, None, asyncio.get_running_loop(), None, config, None)


@pytest.mark.parametrize(
    "storage", ["closed_pool", "closing_pool", "closed_connection", "pool_closes_during_fetch"]
)
async def test_required_append_classifies_native_closed_storage(storage, monkeypatch):
    connection_attempts = []

    async def forbid_connect(*args, **kwargs):
        connection_attempts.append(True)
        raise AssertionError("native storage control must not connect")

    closing = None
    release = None
    if storage == "closed_connection":
        native = _native_connection_without_network()
        await native.close()
        assert native.is_closed()
    else:
        native = await asyncpg.create_pool(min_size=0, max_size=1, connect=forbid_connect)
        if storage == "closing_pool":
            # Keep the real close() in its holder-wait phase without opening a
            # connection. The sentinel is settled in finally, then close joins.
            release = asyncio.get_running_loop().create_future()
            native._holders[0]._in_use = release
            closing = asyncio.create_task(native.close())
            await asyncio.sleep(0)
            assert not closing.done()
        elif storage == "pool_closes_during_fetch":
            fetchval = native.fetchval

            async def close_then_fetch(self, *args, **kwargs):
                await native.close()
                return await fetchval(*args, **kwargs)

            monkeypatch.setattr(asyncpg.Pool, "fetchval", close_then_fetch)
        else:
            await native.close()
        assert native.is_closing() is (storage != "pool_closes_during_fetch")

    try:
        with pytest.raises(AuditTableNotAvailableError):
            await append(native, "owner", "permission.set")
        await write_audit_entry(native, "general", "session", {})
        assert connection_attempts == []
    finally:
        if release is not None and not release.done():
            release.set_result(None)
        if closing is not None:
            await asyncio.wait_for(closing, timeout=1)


@pytest.mark.parametrize("storage", ["uninitialized_pool", "released_proxy", "open_connection"])
async def test_required_append_preserves_native_interface_misuse(storage, monkeypatch):
    native_connection = None
    if storage == "uninitialized_pool":
        native = asyncpg.create_pool(min_size=0, max_size=1)
    else:
        native_connection = _native_connection_without_network()
        if storage == "released_proxy":
            native = asyncpg.pool.PoolConnectionProxy(None, native_connection)
            native._detach()
        else:
            native = native_connection
            failure = asyncpg.InterfaceError("controlled native programming misuse")

            async def misuse(*args, **kwargs):
                raise failure

            monkeypatch.setattr(asyncpg.Connection, "fetchval", misuse)

    try:
        with pytest.raises(asyncpg.InterfaceError) as observed:
            await append(native, "owner", "permission.set")
        if storage == "open_connection":
            assert observed.value is failure
            assert not native.is_closed()
    finally:
        if native_connection is not None:
            await native_connection.close()
