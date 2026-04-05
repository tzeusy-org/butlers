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
    butler_type: str | None = None,
    modules: dict[str, dict] | None = None,
) -> Path:
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{butler_name}"',
        f"port = {port}",
        'description = "A test butler"',
    ]
    if butler_type is not None:
        toml_lines.append(f'type = "{butler_type}"')
    toml_lines += [
        "",
        "[butler.db]",
        'name = "butlers"',
        f'schema = "{butler_name}"',
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
    mock_pool.fetchval.return_value = None
    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

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
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(
            ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock
        ),
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
        patches["validate_module_credentials"],
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
    source_channel: str = "telegram_bot",
) -> dict[str, Any]:
    return {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
        "received_at": "2026-02-18T10:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }


def _make_trigger_mock():
    trigger_mock = AsyncMock()
    trigger_result = MagicMock()
    trigger_result.session_id = uuid.uuid4()
    trigger_mock.return_value = trigger_result
    return trigger_mock


# ---------------------------------------------------------------------------
# 1. Accept phase: returns {"status": "accepted"} quickly
# ---------------------------------------------------------------------------


class TestRouteExecuteAcceptPhase:
    """Verify route.execute returns accepted status on the non-messenger path."""

    async def test_accept_phase(self, tmp_path: Path) -> None:
        """Returns {status: accepted} with expected fields in <100ms; does not await trigger."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        inserted_id = uuid.uuid4()
        with patch(
            "butlers.daemon.route_inbox_insert", new_callable=AsyncMock, return_value=inserted_id
        ):
            t0 = time.monotonic()
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
            elapsed_ms = (time.monotonic() - t0) * 1000

        assert result["status"] == "accepted"
        assert result["schema_version"] == "route_response.v1"
        assert "request_context" in result and "timing" in result
        assert result["inbox_id"] == str(inserted_id)
        assert elapsed_ms < 100, f"Accept phase took {elapsed_ms:.0f}ms, expected <100ms"

        # Does not await trigger before returning
        trigger_started = asyncio.Event()
        trigger_allowed = asyncio.Event()

        async def slow_trigger(**kwargs):
            trigger_started.set()
            await trigger_allowed.wait()
            r = MagicMock()
            r.session_id = uuid.uuid4()
            return r

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
            result2 = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )

        assert result2["status"] == "accepted"
        trigger_allowed.set()
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# 1b. Dedup + 2. Persist + 3. Background dispatch + 4. Failure recording
# ---------------------------------------------------------------------------


class TestRouteExecuteDedup:
    """Verify route.execute rejects duplicate request_ids that already succeeded."""

    async def test_dedup(self, tmp_path: Path) -> None:
        """Skips insert when successful session exists; allows when no prior session."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        existing_session_id = uuid.uuid4()
        patches["mock_pool"].fetchval.return_value = existing_session_id
        mock_insert = AsyncMock(return_value=uuid.uuid4())
        with patch("butlers.daemon.route_inbox_insert", mock_insert):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
        assert result["status"] == "accepted"
        assert result.get("dedup") is True
        assert result["existing_session_id"] == str(existing_session_id)
        mock_insert.assert_not_awaited()

        # No prior session → normal insert
        patches["mock_pool"].fetchval.return_value = None
        inserted_id = uuid.uuid4()
        with patch(
            "butlers.daemon.route_inbox_insert", new_callable=AsyncMock, return_value=inserted_id
        ):
            result2 = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
        assert result2["status"] == "accepted"
        assert result2.get("dedup") is None
        assert "inbox_id" in result2


class TestRouteExecutePersistBeforeReturn:
    """Verify that route_inbox_insert is called before route.execute returns."""

    async def test_route_inbox_insert(self, tmp_path: Path) -> None:
        """route_inbox_insert is awaited; receives full envelope; failure returns error."""
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
        envelope = mock_insert.call_args.kwargs["route_envelope"]
        assert envelope["schema_version"] == "route.v1"
        assert "request_context" in envelope
        assert envelope["input"]["prompt"] == "Run health check."

        # Insert failure → error response
        with patch(
            "butlers.daemon.route_inbox_insert",
            new_callable=AsyncMock,
            side_effect=Exception("DB connection lost"),
        ):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(),
                input={"prompt": "Run health check."},
            )
        assert result["status"] == "error"
        assert result["error"]["class"] == "internal_error"
        assert "route_inbox" in result["error"]["message"]


class TestRouteExecuteBackgroundDispatch:
    """Verify that spawner.trigger() is called in the background."""

    async def test_background_trigger_params(self, tmp_path: Path) -> None:
        """trigger() called eventually; uses trigger_source='route'; passes request_id."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        trigger_mock = _make_trigger_mock()
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
        assert call_kwargs["request_id"] == "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"


class TestRouteExecuteFailureRecording:
    """Verify that processing failures are recorded in route_inbox."""

    async def test_trigger_failure_and_success_recording(self, tmp_path: Path) -> None:
        """Failure calls mark_errored with error msg; success calls mark_processed with IDs."""
        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        # Failure path
        daemon.spawner.trigger = AsyncMock(side_effect=RuntimeError("spawner crash"))
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
            assert result["status"] == "accepted"
            await asyncio.sleep(0.05)

        mock_errored.assert_awaited_once()
        error_msg = mock_errored.call_args.args[2]
        assert "RuntimeError" in error_msg or "spawner crash" in error_msg

        # Success path
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
        assert mock_processed.call_args.args[1] == inbox_id
        assert mock_processed.call_args.args[2] == session_id


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

        mock_insert.assert_not_awaited()


# ---------------------------------------------------------------------------
# 6. Crash recovery: _recover_route_inbox called on startup
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    """Verify that _recover_route_inbox is wired into the startup sequence."""

    async def test_recover_route_inbox_called_on_startup(self, tmp_path: Path) -> None:
        """_recover_route_inbox called for non-staffer; not called for staffer type."""
        patches = _patch_infra("health")
        del patches["recover_route_inbox"]  # Use a spy instead

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
            patches["validate_module_credentials"],
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
            await asyncio.sleep(0)

        assert recovery_called, "_recover_route_inbox was not called on startup"

        # Staffer (type="staffer") does NOT schedule recovery
        for butler_name, port in [("switchboard", 9301), ("infratool", 9302)]:
            patches2 = _patch_infra(butler_name)
            butler_dir2 = _make_butler_toml(
                tmp_path, butler_name=butler_name, port=port, butler_type="staffer"
            )
            mock_mcp2 = MagicMock()
            mock_mcp2.tool = lambda *a, **kw: lambda fn: fn
            with (
                patches2["db_from_env"],
                patches2["run_migrations"],
                patches2["validate_credentials"],
                patches2["validate_module_credentials"],
                patches2["init_telemetry"],
                patches2["sync_schedules"],
                patch("butlers.daemon.FastMCP", return_value=mock_mcp2),
                patch("butlers.daemon.Spawner", return_value=patches2["mock_spawner"]),
                patches2["get_adapter"],
                patches2["shutil_which"],
                patches2["start_mcp_server"],
                patches2["connect_switchboard"],
                patches2["recover_route_inbox"],
                patch.object(ButlerDaemon, "_wire_pipelines"),
            ):
                daemon2 = ButlerDaemon(butler_dir2)
                await daemon2.start()
            assert daemon2._route_inbox_recovery_task is None, (
                "_recover_route_inbox should NOT be scheduled for staffer"
            )


# ---------------------------------------------------------------------------
# Complexity plumbing: route.execute extracts complexity and passes to spawner
# ---------------------------------------------------------------------------


class TestRouteExecuteComplexityPlumbing:
    """Verify complexity from route.v1 envelope is forwarded to spawner.trigger()."""

    async def _run_with_complexity(
        self, route_execute_fn, spawner, complexity_str: str | None = None
    ):
        """Run route_execute_fn and return captured trigger kwargs."""
        trigger_mock = _make_trigger_mock()
        spawner.trigger = trigger_mock
        input_payload = {"prompt": "Test."}
        if complexity_str:
            input_payload["complexity"] = complexity_str
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
                input=input_payload,
            )
            await asyncio.sleep(0.05)
        return trigger_mock.call_args.kwargs

    async def test_complexity_routing(self, tmp_path: Path) -> None:
        """high, extra_high, absent (→ medium) all forwarded correctly; invalid falls back to MEDIUM."""
        from butlers.core.model_routing import Complexity

        patches = _patch_infra("health")
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        kwargs = await self._run_with_complexity(route_execute_fn, daemon.spawner, "high")
        assert kwargs["complexity"] == Complexity.HIGH

        kwargs2 = await self._run_with_complexity(route_execute_fn, daemon.spawner, "extra_high")
        assert kwargs2["complexity"] == Complexity.EXTRA_HIGH

        kwargs3 = await self._run_with_complexity(route_execute_fn, daemon.spawner)
        assert kwargs3["complexity"] == Complexity.MEDIUM

        # Invalid complexity falls back to MEDIUM
        trigger_mock = _make_trigger_mock()
        daemon.spawner.trigger = trigger_mock

        import butlers.daemon as daemon_module

        orig_parse = daemon_module.parse_route_envelope

        def _mock_parse(payload):
            envelope = orig_parse(payload)
            from butlers.tools.switchboard.routing.contracts import RouteInputV1

            bad_input = object.__new__(RouteInputV1)
            object.__setattr__(bad_input, "prompt", envelope.input.prompt)
            object.__setattr__(bad_input, "context", envelope.input.context)
            conv_hist = envelope.input.conversation_history
            object.__setattr__(bad_input, "conversation_history", conv_hist)
            object.__setattr__(bad_input, "complexity", "not_a_valid_value")
            object.__setattr__(bad_input, "attachments", envelope.input.attachments)
            object.__setattr__(envelope, "input", bad_input)
            return envelope

        with (
            patch("butlers.daemon.parse_route_envelope", side_effect=_mock_parse),
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
        assert trigger_mock.call_args.kwargs["complexity"] == Complexity.MEDIUM
