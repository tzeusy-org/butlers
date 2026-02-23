"""Tests for route_to_butler 'accepted' status passthrough.

Verifies that when a target butler's route.execute returns {status: 'accepted'},
the switchboard's route_to_butler tool passes through {status: 'accepted', butler: ...}
instead of the generic {status: 'ok', butler: ...}.

This ensures telemetry can distinguish async-accepted routes from sync-ok routes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
    set_runtime_session_routing_context,
)
from butlers.daemon import ButlerDaemon
from butlers.modules.pipeline import _routing_ctx_var
from butlers.tools.switchboard.routing.contracts import parse_route_envelope

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_route_execute_authz.py)
# ---------------------------------------------------------------------------


def _toml_value(v: Any) -> str:
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
        return f"[{items}]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _make_switchboard_dir(tmp_path: Path) -> Path:
    """Create a minimal switchboard butler directory."""
    toml_lines = [
        "[butler]",
        'name = "switchboard"',
        "port = 9100",
        'description = "Routes messages"',
        "",
        "[butler.db]",
        'name = "butler_switchboard"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra():
    """Return a dict of patches for all infrastructure dependencies."""
    mock_pool = AsyncMock()

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butler_switchboard"

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "validate_core_credentials": patch(
            "butlers.daemon.validate_core_credentials_async",
            new_callable=AsyncMock,
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_switchboard_and_capture_route_to_butler(
    butler_dir: Path,
    patches: dict,
    mock_route: AsyncMock | None = None,
) -> tuple[ButlerDaemon, Any]:
    """Boot a switchboard daemon and capture the route_to_butler handler.

    Parameters
    ----------
    butler_dir:
        Path to the butler config directory.
    patches:
        Infrastructure patches from _patch_infra().
    mock_route:
        Optional AsyncMock to replace the underlying ``route`` function.
        This must be applied during daemon.start() so that the from-import
        inside _register_tools picks up the mock.
    """
    route_to_butler_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            nonlocal route_to_butler_fn
            resolved_name = declared_name or fn.__name__
            if resolved_name == "route_to_butler":
                route_to_butler_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    route_patch = (
        patch("butlers.tools.switchboard.routing.route.route", new=mock_route)
        if mock_route is not None
        else patch("butlers.tools.switchboard.routing.route.route")
    )

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["validate_core_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        route_patch,
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_to_butler_fn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRouteToButlerAcceptedStatusPassthrough:
    """Verify that route_to_butler passes through 'accepted' from the target butler.

    Implementation note: The ``_switchboard_route`` local in daemon.py is bound
    via ``from ... import route`` inside ``_register_tools``. We supply the mock
    DURING ``daemon.start()`` so the closure captures the mock. After start()
    returns, the closure retains its reference to the mock regardless of the
    patch context.
    """

    async def test_ok_status_returned_for_sync_success(self, tmp_path: Path) -> None:
        """When target butler returns a non-accepted result, status is 'ok'."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        mock_route = AsyncMock(return_value={"result": {"status": "ok", "message": "processed"}})
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None, "route_to_butler not registered on switchboard"

        result = await route_to_butler_fn(butler="health", prompt="track my meds")

        assert result["status"] == "ok"
        assert result["butler"] == "health"

    async def test_accepted_status_passed_through(self, tmp_path: Path) -> None:
        """When target butler returns {status: 'accepted'}, route_to_butler returns 'accepted'."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None, "route_to_butler not registered on switchboard"

        result = await route_to_butler_fn(butler="health", prompt="track my meds")

        assert result["status"] == "accepted"
        assert result["butler"] == "health"

    async def test_accepted_status_not_mapped_to_ok(self, tmp_path: Path) -> None:
        """'accepted' must NOT be downgraded to 'ok' — they are distinct statuses."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        mock_route = AsyncMock(return_value={"result": {"status": "accepted"}})
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None

        result = await route_to_butler_fn(butler="relationship", prompt="call Mom")

        assert result["status"] != "ok"
        assert result["status"] == "accepted"

    async def test_error_result_still_returns_error_status(self, tmp_path: Path) -> None:
        """Error responses from target butler are still mapped to 'error' status."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        mock_route = AsyncMock(return_value={"error": "Butler 'health' not found in registry"})
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None

        result = await route_to_butler_fn(butler="health", prompt="track my meds")

        assert result["status"] == "error"
        assert result["butler"] == "health"
        assert "not found" in result["error"]

    async def test_non_accepted_inner_status_returns_ok(self, tmp_path: Path) -> None:
        """Inner result without a status key is treated as generic success ('ok')."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        # Inner result without a status key — should still be treated as ok
        mock_route = AsyncMock(return_value={"result": {"message_id": "abc123"}})
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None

        result = await route_to_butler_fn(butler="general", prompt="hello")

        assert result["status"] == "ok"
        assert result["butler"] == "general"

    async def test_inner_error_status_propagated(self, tmp_path: Path) -> None:
        """When target butler's route.execute returns {status: 'error'}, propagate it."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        mock_route = AsyncMock(
            return_value={
                "result": {
                    "schema_version": "route_response.v1",
                    "status": "error",
                    "error": {
                        "class": "internal_error",
                        "message": "route.execute: failed to persist to "
                        "route_inbox: connection lost",
                    },
                }
            }
        )
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None

        result = await route_to_butler_fn(butler="relationship", prompt="test")

        assert result["status"] == "error"
        assert result["butler"] == "relationship"
        assert "route_inbox" in result["error"]

    async def test_inner_error_status_with_string_error(self, tmp_path: Path) -> None:
        """When error detail is a plain string, it is still propagated."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        mock_route = AsyncMock(
            return_value={"result": {"status": "error", "error": "something went wrong"}}
        )
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None

        result = await route_to_butler_fn(butler="health", prompt="test")

        assert result["status"] == "error"
        assert result["butler"] == "health"
        assert "something went wrong" in result["error"]

    async def test_route_to_butler_serializes_uuid7_when_context_missing(
        self, tmp_path: Path
    ) -> None:
        """Missing routing context should not emit non-UUID request_context.request_id."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        captured_envelope: dict[str, Any] = {}

        async def _capture_route(*_args, **kwargs):
            captured_envelope.update(kwargs["args"])
            return {"result": {"status": "accepted"}}

        mock_route = AsyncMock(side_effect=_capture_route)
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None

        result = await route_to_butler_fn(butler="general", prompt="hello")

        assert result["status"] == "accepted"
        route_payload = dict(captured_envelope)
        route_payload.pop("__switchboard_route_context", None)
        parsed = parse_route_envelope(route_payload)
        assert parsed.request_context.request_id.version == 7

    async def test_route_to_butler_rewrites_invalid_context_request_id_to_uuid7(
        self, tmp_path: Path
    ) -> None:
        """Invalid request_id values in routing context are normalized before route dispatch."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        captured_envelope: dict[str, Any] = {}

        async def _capture_route(*_args, **kwargs):
            captured_envelope.update(kwargs["args"])
            return {"result": {"status": "accepted"}}

        mock_route = AsyncMock(side_effect=_capture_route)
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None

        token = _routing_ctx_var.set(
            {
                "source_metadata": {"channel": "telegram", "identity": "user-123"},
                "request_context": {"source_thread_identity": "chat-456"},
                "request_id": "unknown",
            }
        )
        try:
            result = await route_to_butler_fn(butler="general", prompt="hello")
        finally:
            _routing_ctx_var.reset(token)

        assert result["status"] == "accepted"
        route_payload = dict(captured_envelope)
        route_payload.pop("__switchboard_route_context", None)
        parsed = parse_route_envelope(route_payload)
        assert parsed.request_context.request_id.version == 7
        assert captured_envelope["request_context"]["request_id"] != "unknown"

    async def test_route_to_butler_uses_runtime_session_routing_context_fallback(
        self, tmp_path: Path
    ) -> None:
        """When task-local routing context is missing, fallback to runtime session lineage."""
        patches = _patch_infra()
        butler_dir = _make_switchboard_dir(tmp_path)
        captured_envelope: dict[str, Any] = {}

        async def _capture_route(*_args, **kwargs):
            captured_envelope.update(kwargs["args"])
            return {"result": {"status": "accepted"}}

        mock_route = AsyncMock(side_effect=_capture_route)
        _, route_to_butler_fn = await _start_switchboard_and_capture_route_to_butler(
            butler_dir, patches, mock_route=mock_route
        )
        assert route_to_butler_fn is not None

        runtime_session_id = "sess-route-to-butler-fallback"
        set_runtime_session_routing_context(
            runtime_session_id,
            {
                "source_metadata": {
                    "channel": "telegram",
                    "identity": "telegram:bot-main",
                    "tool_name": "ingest",
                },
                "request_context": {
                    "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
                    "source_channel": "telegram",
                    "source_sender_identity": "123456789",
                    "source_thread_identity": "123456789:999",
                },
                "request_id": "019c8812-fb0f-77f3-88b9-5763c1336b27",
            },
        )
        token = set_current_runtime_session_id(runtime_session_id)
        try:
            result = await route_to_butler_fn(butler="health", prompt="track breakfast reminder")
        finally:
            reset_current_runtime_session_id(token)
            clear_runtime_session_routing_context(runtime_session_id)

        assert result["status"] == "accepted"
        assert captured_envelope["request_context"]["source_channel"] == "telegram"
        assert captured_envelope["request_context"]["source_sender_identity"] == "123456789"
        assert captured_envelope["request_context"]["source_thread_identity"] == "123456789:999"
