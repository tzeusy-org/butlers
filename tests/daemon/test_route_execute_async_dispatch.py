"""Tests for route.execute accept-then-process async dispatch (butlers-963.6).

Verifies:
1. Accept phase: route.execute returns {"status": "accepted"} in <50ms
2. Accept phase: route envelope is persisted to route_inbox before returning
3. Background dispatch: spawner.trigger() is called asynchronously
4. Failure recording: processing failures stored in route_inbox (not lost)
5. Switchboard spawner lock decoupling: route.execute returns before trigger
6. Crash recovery: _recover_route_inbox is called on daemon startup
7. Messenger butlers: not affected (still synchronous delivery path)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
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


def _make_butler_toml(
    tmp_path: Path,
    *,
    butler_name: str = "health",
    port: int = 9200,
    modules: dict[str, dict] | None = None,
) -> Path:
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
        "",
        "[butler.db]",
        f'name = "butler_{butler_name}"',
        "",
        "[[butler.schedule]]",
        'name = "daily-check"',
        'cron = "0 9 * * *"',
        'prompt = "Do the daily check"',
    ]
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            toml_lines.append(f"{k} = {_toml_value(v)}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra(butler_name: str = "health"):
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
    mock_db.db_name = f"butler_{butler_name}"

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
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


async def _start_daemon_with_route_execute(butler_dir: Path, patches: dict):
    """Boot a daemon and capture the route.execute handler function."""
    route_execute_fn = None
    mock_mcp = MagicMock()

    def tool_decorator(*_decorator_args, **decorator_kwargs):
        declared_name = decorator_kwargs.get("name")

        def decorator(fn):
            nonlocal route_execute_fn
            resolved_name = declared_name or fn.__name__
            if resolved_name == "route.execute":
                route_execute_fn = fn
            return fn

        return decorator

    mock_mcp.tool = tool_decorator

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patch("butlers.daemon.FastMCP", return_value=mock_mcp),
        patch("butlers.daemon.Spawner", return_value=patches["mock_spawner"]),
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_execute_fn


def _route_request_context(
    *,
    source_endpoint_identity: str = "switchboard",
    source_sender_identity: str = "health",
    source_channel: str = "telegram",
) -> dict[str, Any]:
    return {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
        "received_at": "2026-02-18T10:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }


# ---------------------------------------------------------------------------
# 1. Accept phase: returns {"status": "accepted"} quickly
# ---------------------------------------------------------------------------


class TestRouteExecuteAcceptPhase:
    """Verify route.execute returns accepted status on the non-messenger path."""

    async def test_returns_accepted_status(self, tmp_path: Path) -> None:
        """Non-messenger route.execute returns {status: accepted} immediately."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        inserted_id = uuid.uuid4()
        with patch(
            "butlers.daemon.route_inbox_insert", new_callable=AsyncMock, return_value=inserted_id
        ):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )

        assert result["status"] == "accepted"
        assert result["schema_version"] == "route_response.v1"
        assert "request_context" in result
        assert "timing" in result
        assert "inbox_id" in result
        assert result["inbox_id"] == str(inserted_id)

    async def test_accept_phase_is_fast(self, tmp_path: Path) -> None:
        """Accept phase returns in well under 50ms (no spawner trigger)."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        # route_inbox_insert is fast (immediate return)
        with patch(
            "butlers.daemon.route_inbox_insert", new_callable=AsyncMock, return_value=uuid.uuid4()
        ):
            t0 = time.monotonic()
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

        assert result["status"] == "accepted"
        # Should complete well under 100ms (generous for CI)
        assert elapsed_ms < 100, f"Accept phase took {elapsed_ms:.0f}ms, expected <100ms"

    async def test_accept_phase_does_not_await_trigger(self, tmp_path: Path) -> None:
        """route.execute returns before spawner.trigger() completes."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        trigger_started = asyncio.Event()
        trigger_allowed = asyncio.Event()

        async def slow_trigger(**kwargs):
            trigger_started.set()
            await trigger_allowed.wait()
            result = MagicMock()
            result.session_id = uuid.uuid4()
            return result

        daemon.spawner.trigger = slow_trigger

        with (
            patch(
                "butlers.daemon.route_inbox_insert",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
            patch("butlers.daemon.route_inbox_mark_processing", new_callable=AsyncMock),
            patch("butlers.daemon.route_inbox_mark_processed", new_callable=AsyncMock),
        ):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )

        # route.execute returned accepted WITHOUT trigger completing
        assert result["status"] == "accepted"
        # Allow trigger to finish (cleanup)
        trigger_allowed.set()
        # Give background task time to run
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# 2. Accept phase: route_inbox_insert is called before returning
# ---------------------------------------------------------------------------


class TestRouteExecutePersistBeforeReturn:
    """Verify that route_inbox_insert is called before route.execute returns."""

    async def test_route_inbox_insert_is_called(self, tmp_path: Path) -> None:
        """route_inbox_insert is awaited during accept phase."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_insert = AsyncMock(return_value=uuid.uuid4())
        with patch("butlers.daemon.route_inbox_insert", mock_insert):
            await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )

        mock_insert.assert_awaited_once()

    async def test_route_inbox_insert_receives_envelope(self, tmp_path: Path) -> None:
        """route_inbox_insert receives the full route envelope."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_insert = AsyncMock(return_value=uuid.uuid4())
        with patch("butlers.daemon.route_inbox_insert", mock_insert):
            await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )

        call_kwargs = mock_insert.call_args.kwargs
        envelope = call_kwargs["route_envelope"]
        assert envelope["schema_version"] == "route.v1"
        assert "request_context" in envelope
        assert envelope["input"]["prompt"] == "Run health check."

    async def test_insert_failure_returns_error(self, tmp_path: Path) -> None:
        """If route_inbox_insert fails, route.execute returns an error response."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_insert = AsyncMock(side_effect=Exception("DB connection lost"))
        with patch("butlers.daemon.route_inbox_insert", mock_insert):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )

        assert result["status"] == "error"
        assert result["error"]["class"] == "internal_error"
        assert "route_inbox" in result["error"]["message"]


# ---------------------------------------------------------------------------
# 3. Background dispatch: spawner.trigger() called asynchronously
# ---------------------------------------------------------------------------


class TestRouteExecuteBackgroundDispatch:
    """Verify that spawner.trigger() is called in the background."""

    async def test_trigger_called_eventually(self, tmp_path: Path) -> None:
        """spawner.trigger() is eventually called by the background task."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        trigger_mock = AsyncMock()
        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        trigger_mock.return_value = trigger_result
        daemon.spawner.trigger = trigger_mock

        with (
            patch(
                "butlers.daemon.route_inbox_insert",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
            patch("butlers.daemon.route_inbox_mark_processing", new_callable=AsyncMock),
            patch("butlers.daemon.route_inbox_mark_processed", new_callable=AsyncMock),
        ):
            await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
            # Let the event loop run the background task
            await asyncio.sleep(0.05)

        trigger_mock.assert_awaited()

    async def test_trigger_called_with_route_trigger_source(self, tmp_path: Path) -> None:
        """Background trigger uses trigger_source='route' not 'trigger'."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        trigger_mock = AsyncMock()
        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        trigger_mock.return_value = trigger_result
        daemon.spawner.trigger = trigger_mock

        with (
            patch(
                "butlers.daemon.route_inbox_insert",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
            patch("butlers.daemon.route_inbox_mark_processing", new_callable=AsyncMock),
            patch("butlers.daemon.route_inbox_mark_processed", new_callable=AsyncMock),
        ):
            await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
            await asyncio.sleep(0.05)

        trigger_mock.assert_awaited()
        call_kwargs = trigger_mock.call_args.kwargs
        assert call_kwargs["trigger_source"] == "route"

    async def test_trigger_receives_request_id(self, tmp_path: Path) -> None:
        """Background trigger receives the request_id from route_context."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        trigger_mock = AsyncMock()
        trigger_result = MagicMock()
        trigger_result.session_id = uuid.uuid4()
        trigger_mock.return_value = trigger_result
        daemon.spawner.trigger = trigger_mock

        with (
            patch(
                "butlers.daemon.route_inbox_insert",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
            patch("butlers.daemon.route_inbox_mark_processing", new_callable=AsyncMock),
            patch("butlers.daemon.route_inbox_mark_processed", new_callable=AsyncMock),
        ):
            await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
            await asyncio.sleep(0.05)

        call_kwargs = trigger_mock.call_args.kwargs
        assert call_kwargs["request_id"] == "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"


# ---------------------------------------------------------------------------
# 4. Failure recording: route_inbox_mark_errored is called on trigger failure
# ---------------------------------------------------------------------------


class TestRouteExecuteFailureRecording:
    """Verify that processing failures are recorded in route_inbox."""

    async def test_trigger_failure_calls_mark_errored(self, tmp_path: Path) -> None:
        """If spawner.trigger() raises, route_inbox_mark_errored is called."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        trigger_mock = AsyncMock(side_effect=RuntimeError("spawner crash"))
        daemon.spawner.trigger = trigger_mock

        mock_errored = AsyncMock()
        with (
            patch(
                "butlers.daemon.route_inbox_insert",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
            patch("butlers.daemon.route_inbox_mark_processing", new_callable=AsyncMock),
            patch("butlers.daemon.route_inbox_mark_errored", mock_errored),
        ):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
            # Result is accepted (failure happens asynchronously)
            assert result["status"] == "accepted"
            await asyncio.sleep(0.05)

        mock_errored.assert_awaited_once()
        call_args = mock_errored.call_args
        error_msg = call_args.args[2]
        assert "RuntimeError" in error_msg or "spawner crash" in error_msg

    async def test_trigger_success_calls_mark_processed(self, tmp_path: Path) -> None:
        """On success, route_inbox_mark_processed is called with session_id."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        session_id = uuid.uuid4()
        trigger_result = MagicMock()
        trigger_result.session_id = session_id
        daemon.spawner.trigger = AsyncMock(return_value=trigger_result)

        mock_processed = AsyncMock()
        inbox_id = uuid.uuid4()
        with (
            patch(
                "butlers.daemon.route_inbox_insert", new_callable=AsyncMock, return_value=inbox_id
            ),
            patch("butlers.daemon.route_inbox_mark_processing", new_callable=AsyncMock),
            patch("butlers.daemon.route_inbox_mark_processed", mock_processed),
        ):
            await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
            await asyncio.sleep(0.05)

        mock_processed.assert_awaited_once()
        call_args = mock_processed.call_args
        assert call_args.args[1] == inbox_id  # row_id
        assert call_args.args[2] == session_id  # session_id


# ---------------------------------------------------------------------------
# 5. Messenger butler: unaffected (still synchronous delivery path)
# ---------------------------------------------------------------------------


class TestMessengerRouteExecuteUnaffected:
    """Verify that the messenger butler is NOT changed (still synchronous)."""

    async def test_messenger_does_not_use_route_inbox(self, tmp_path: Path) -> None:
        """Messenger butler route.execute does not call route_inbox_insert."""
        patches = _patch_infra("messenger")
        butler_dir = _make_butler_toml(
            tmp_path,
            butler_name="messenger",
            port=9201,
            modules={"telegram": {}, "email": {}},
        )
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        mock_insert = AsyncMock(return_value=uuid.uuid4())
        telegram_module = next((m for m in daemon._modules if m.name == "telegram"), None)
        if telegram_module is not None:
            telegram_module._send_message = AsyncMock(return_value={"result": {"message_id": 1}})

        valid_notify = {
            "schema_version": "notify.v1",
            "origin_butler": "health",
            "delivery": {
                "intent": "send",
                "channel": "telegram",
                "message": "Hello.",
                "recipient": "12345",
            },
        }

        with patch("butlers.daemon.route_inbox_insert", mock_insert):
            await route_execute_fn(
                schema_version="route.v1",
                request_context={
                    "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
                    "received_at": "2026-02-18T10:00:00Z",
                    "source_channel": "mcp",
                    "source_endpoint_identity": "switchboard",
                    "source_sender_identity": "health",
                },
                input={
                    "prompt": "Deliver.",
                    "context": {"notify_request": valid_notify},
                },
            )

        # route_inbox_insert must NOT be called for messenger butler
        mock_insert.assert_not_awaited()


# ---------------------------------------------------------------------------
# 6. Crash recovery: _recover_route_inbox called on startup
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    """Verify that _recover_route_inbox is wired into the startup sequence."""

    async def test_recover_route_inbox_called_on_startup(self, tmp_path: Path) -> None:
        """_recover_route_inbox is called during daemon startup for non-switchboard butlers."""
        # We need to not mock _recover_route_inbox for this test
        patches = _patch_infra("health")
        del patches["recover_route_inbox"]  # Don't mock it; use a spy

        butler_dir = _make_butler_toml(tmp_path, butler_name="health")

        mock_mcp = MagicMock()
        mock_mcp.tool = lambda *a, **kw: lambda fn: fn

        recovery_called = False

        async def mock_recover(self_daemon, pool):
            nonlocal recovery_called
            recovery_called = True

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patch("butlers.daemon.Spawner", return_value=patches["mock_spawner"]),
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patch.object(ButlerDaemon, "_recover_route_inbox", mock_recover),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert recovery_called, "_recover_route_inbox was not called on startup"

    async def test_switchboard_skips_route_inbox_recovery(self, tmp_path: Path) -> None:
        """Switchboard butler does not call _recover_route_inbox (it uses DurableBuffer)."""
        patches = _patch_infra("switchboard")

        butler_dir = _make_butler_toml(tmp_path, butler_name="switchboard", port=9301)

        mock_mcp = MagicMock()
        mock_mcp.tool = lambda *a, **kw: lambda fn: fn

        recovery_called = False

        async def mock_recover(self_daemon, pool):
            nonlocal recovery_called
            recovery_called = True

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patch("butlers.daemon.FastMCP", return_value=mock_mcp),
            patch("butlers.daemon.Spawner", return_value=patches["mock_spawner"]),
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patch.object(ButlerDaemon, "_recover_route_inbox", mock_recover),
            # Suppress DurableBuffer start
            patch.object(ButlerDaemon, "_wire_pipelines"),
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert not recovery_called, "_recover_route_inbox should NOT be called for switchboard"
