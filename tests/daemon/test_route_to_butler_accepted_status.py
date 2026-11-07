"""Tests for route_to_butler 'accepted' status passthrough — condensed.

Verifies that when a target butler's route.execute returns {status: 'accepted'},
the switchboard's route_to_butler tool passes through {status: 'accepted', butler: ...}
instead of the generic {status: 'ok', butler: ...}.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
    set_runtime_session_routing_context,
)
from butlers.testing.daemon import write_butler_config
from butlers.testing.fake_pool import fake_pool
from butlers.tools.switchboard.routing.contracts import parse_route_envelope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def _route_to_butler(
    tool_host,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: AsyncMock,
    *,
    db: bool = False,
):
    """The Switchboard's registered route_to_butler, with the internal route() faked.

    DB-free unless *db*: only the permission tests read SQL (public.permissions).
    """
    # The package re-exports route(), shadowing the module name for string targets.
    monkeypatch.setattr(
        importlib.import_module("butlers.tools.switchboard.routing.route"), "route", route
    )
    config_dir = write_butler_config(tmp_path, "switchboard", schema="switchboard", port=41100)
    if db:
        host = await tool_host(config_dir, "core", "switchboard")
    else:
        host = await tool_host(
            config_dir,
            pool=fake_pool(
                fetchrow=AsyncMock(return_value=None),
                fetchval=AsyncMock(return_value=None),
                execute=AsyncMock(return_value=None),
                fetch=AsyncMock(return_value=[]),
            ),
        )
    return host, await host.tool("route_to_butler")


# ---------------------------------------------------------------------------
# Status passthrough
# ---------------------------------------------------------------------------


async def test_route_to_butler_status_mapping(
    tool_host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """route_to_butler maps inner results to correct outer status codes."""
    # accepted → passed through
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})
    _, fn = await _route_to_butler(tool_host, tmp_path, monkeypatch, mock_route)
    result = await fn(butler="health", prompt="test")
    assert result["status"] == "accepted"
    assert result["butler"] == "health"

    # error → error with message
    mock_route2 = AsyncMock(return_value={"error": "Butler 'health' not found in registry"})
    _, fn2 = await _route_to_butler(tool_host, tmp_path / "d2", monkeypatch, mock_route2)
    result2 = await fn2(butler="health", prompt="test")
    assert result2["status"] == "error"
    assert "not found" in result2.get("error", "")


# ---------------------------------------------------------------------------
# Request context normalization
# ---------------------------------------------------------------------------


async def test_route_to_butler_envelope_behavior(
    tool_host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing context generates uuid7; runtime session fallback honored; complexity defaults/validation."""
    captured: dict[str, Any] = {}

    async def _capture(*_args, **kwargs):
        captured.update(kwargs["args"])
        return {"result": {"status": "accepted"}}

    _, fn = await _route_to_butler(
        tool_host, tmp_path, monkeypatch, AsyncMock(side_effect=_capture)
    )

    # Missing context: generates uuid7
    result = await fn(butler="general", prompt="hello")
    assert result["status"] == "accepted"
    payload = {k: v for k, v in captured.items() if k != "__switchboard_route_context"}
    assert parse_route_envelope(payload).request_context.request_id.version == 7

    # Runtime session context fallback
    runtime_session_id = "sess-route-to-butler-fallback"
    set_runtime_session_routing_context(
        runtime_session_id,
        {
            "source_metadata": {
                "channel": "telegram_bot",
                "identity": "telegram:bot-main",
                "tool_name": "ingest",
            },
            "request_context": {
                "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
                "source_channel": "telegram_bot",
                "source_sender_identity": "123456789",
                "source_thread_identity": "123456789:999",
            },
            "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
        },
    )
    token = set_current_runtime_session_id(runtime_session_id)
    captured.clear()
    try:
        result2 = await fn(butler="health", prompt="track breakfast")
    finally:
        reset_current_runtime_session_id(token)
        clear_runtime_session_routing_context(runtime_session_id)
    assert result2["status"] == "accepted"
    assert captured["request_context"]["source_channel"] == "telegram_bot"

    # Complexity: reasoning explicit; invalid → workhorse; missing → workhorse
    captured.clear()
    await fn(butler="health", prompt="test", complexity="reasoning")
    assert captured["input"]["complexity"] == "reasoning"

    captured.clear()
    await fn(butler="health", prompt="test", complexity="extreme")
    assert captured["input"]["complexity"] == "workhorse"

    captured.clear()
    await fn(butler="health", prompt="test")
    assert captured["input"]["complexity"] == "workhorse"

    # Legacy pre-core_092 vocabulary (bu-h3cwc): remapped to its correct
    # canonical equivalent via the shared deprecated-tier shim, not blanket
    # -defaulted to workhorse (e.g. "high" -> "reasoning", not "workhorse").
    captured.clear()
    await fn(butler="health", prompt="test", complexity="high")
    assert captured["input"]["complexity"] == "reasoning"

    captured.clear()
    await fn(butler="health", prompt="test", complexity="medium")
    assert captured["input"]["complexity"] == "workhorse"


# ---------------------------------------------------------------------------
# Permissions-matrix enforcement (public.permissions: cross_butler) [bu-tzlq6]
# ---------------------------------------------------------------------------


async def test_route_to_butler_blocked_when_cross_butler_revoked(
    tool_host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revoked cross_butler blocks the dispatch before the switchboard route runs.

    Mirrors the spawn gate: a granted=false cell denies the cross-butler call
    outright (observable error), and the underlying route is never invoked.
    Pre-fix this fails: the matrix was ignored, so the route proceeded.
    """
    from butlers.core.permissions import CROSS_BUTLER_PERMISSION
    from butlers.testing.db import open_pool

    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})
    host, fn = await _route_to_butler(tool_host, tmp_path, monkeypatch, mock_route, db=True)
    admin = await open_pool(host.admin_url)
    try:
        await admin.execute(
            "INSERT INTO public.permissions (butler, permission, granted, reason)"
            " VALUES ('switchboard', $1, false, 'revoked by owner')",
            CROSS_BUTLER_PERMISSION,
        )
    finally:
        await admin.close()

    result = await fn(butler="health", prompt="test")

    assert result["status"] == "error"
    assert "permission denied" in result.get("error", "").lower()
    assert result["butler"] == "health"
    mock_route.assert_not_awaited()


async def test_route_to_butler_allowed_when_cross_butler_granted(
    tool_host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Granted/default cross_butler lets the dispatch proceed to the route."""
    mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})
    _, fn = await _route_to_butler(tool_host, tmp_path, monkeypatch, mock_route, db=True)
    # No permissions row: the default cell is granted.

    result = await fn(butler="health", prompt="test")

    assert result["status"] == "accepted"
    mock_route.assert_awaited_once()
