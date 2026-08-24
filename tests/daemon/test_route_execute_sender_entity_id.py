"""Tests for sender entity_id injection via route.execute.

Verifies that when route.execute receives a request_context with
source_sender_entity_id, the value is captured and injected into
_routing_ctx_var before spawner.trigger() is called, so that
memory_store_fact can use it as the default entity_id.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


class _NoopLeaseHeartbeat:
    async def __aenter__(self) -> asyncio.Event:
        return asyncio.Event()

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _RecordingSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def is_recording(self) -> bool:
        return True

    def get_span_context(self) -> Any:
        return MagicMock(is_valid=False)


class _RecordingSpanContext:
    def __init__(self, span: _RecordingSpan) -> None:
        self.span = span

    def __enter__(self) -> _RecordingSpan:
        return self.span

    def __exit__(self, *_args: Any) -> bool:
        return False


class _RecordingTracer:
    def __init__(self) -> None:
        self.spans: dict[str, _RecordingSpan] = {}

    def start_as_current_span(self, name: str, **_kwargs: Any) -> _RecordingSpanContext:
        span = _RecordingSpan(name)
        self.spans[name] = span
        return _RecordingSpanContext(span)


# ---------------------------------------------------------------------------
# Helpers (shared with other route_execute test modules)
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
    port: int = 9700,
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


def _patch_infra():
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
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
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
        patch("butlers.lifecycle.FastMCP", return_value=mock_mcp),
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["recover_route_inbox"],
    ):
        daemon = ButlerDaemon(butler_dir)
        await daemon.start()

    return daemon, route_execute_fn


@pytest.fixture(autouse=True)
def _mock_route_inbox(monkeypatch):
    """Patch route_inbox functions to avoid DB calls in all tests here."""
    mock_insert = AsyncMock(return_value=uuid.uuid4())
    mock_claim_processing = AsyncMock(return_value=uuid.uuid4())
    mock_mark_processed = AsyncMock()
    mock_mark_errored = AsyncMock()
    monkeypatch.setattr("butlers.core_tools._routing.route_inbox_insert", mock_insert)
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_claim_processing", mock_claim_processing
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
        MagicMock(side_effect=lambda *_args, **_kwargs: _NoopLeaseHeartbeat()),
    )
    monkeypatch.setattr(
        "butlers.core.route_inbox.route_inbox_renew_processing_claim",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "butlers.core_tools._routing.route_inbox_mark_processed", mock_mark_processed
    )
    monkeypatch.setattr("butlers.core_tools._routing.route_inbox_mark_errored", mock_mark_errored)
    return mock_insert


def _base_request_context(
    *,
    source_sender_entity_id: str | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-aabbccddee00",
        "received_at": "2026-03-10T00:00:00Z",
        "source_channel": "telegram_bot",
        "source_endpoint_identity": "switchboard",
        "source_sender_identity": "owner",
        "source_thread_identity": "12345",
    }
    if source_sender_entity_id is not None:
        ctx["source_sender_entity_id"] = source_sender_entity_id
    return ctx


class TestRouteExecuteSenderEntityIdInjection:
    """Verify that source_sender_entity_id is injected into _routing_ctx_var."""

    async def test_sender_entity_id_injected_into_routing_ctx_var(self, tmp_path: Path) -> None:
        """_routing_ctx_var is set with source_entity_id when source_sender_entity_id present."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        sender_entity_id = "550e8400-e29b-41d4-a716-446655440000"
        captured_routing_ctx: list[Any] = []

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10

        async def _capture_and_trigger(*args, **kwargs):
            from butlers.modules.pipeline import _routing_ctx_var

            captured_routing_ctx.append(_routing_ctx_var.get())
            return mock_trigger_result

        daemon.spawner.trigger = _capture_and_trigger

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_base_request_context(source_sender_entity_id=sender_entity_id),
            input={"prompt": "Store some info about me."},
        )
        await asyncio.sleep(0.05)

        assert result["status"] == "accepted"
        assert len(captured_routing_ctx) == 1
        ctx = captured_routing_ctx[0]
        assert ctx is not None
        assert isinstance(ctx, dict)
        assert ctx.get("source_entity_id") == sender_entity_id

    async def test_no_sender_entity_id_leaves_routing_ctx_unset(self, tmp_path: Path) -> None:
        """When source_sender_entity_id is absent, _routing_ctx_var is not set by route."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        captured_routing_ctx: list[Any] = []

        mock_trigger_result = MagicMock()
        mock_trigger_result.output = "ok"
        mock_trigger_result.success = True
        mock_trigger_result.error = None
        mock_trigger_result.duration_ms = 10

        async def _capture_and_trigger(*args, **kwargs):
            from butlers.modules.pipeline import _routing_ctx_var

            captured_routing_ctx.append(_routing_ctx_var.get())
            return mock_trigger_result

        daemon.spawner.trigger = _capture_and_trigger

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_base_request_context(),  # no entity_id
            input={"prompt": "Just a message."},
        )
        await asyncio.sleep(0.05)

        assert result["status"] == "accepted"
        assert len(captured_routing_ctx) == 1
        # When no entity_id, routing context should be None (not set by route.execute)
        assert captured_routing_ctx[0] is None

    async def test_conceptual_message_is_persisted_and_available_as_structured_context(
        self,
        tmp_path: Path,
        _mock_route_inbox: AsyncMock,
    ) -> None:
        """Spec: REQ-conversation-decomposition-001, REQ-entity-identity-001."""
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        conceptual_message = {
            "signal_type": "health",
            "confidence": "HIGH",
            "excerpts": [
                {
                    "message_id": "m1",
                    "sender": "Known speaker",
                    "sender_identity": "15551234567@s.whatsapp.net",
                    "sender_entity_id": "11111111-1111-1111-1111-111111111111",
                    "text": "My knee hurts",
                    "timestamp": "2026-08-24T10:00:00Z",
                }
            ],
        }
        captured_routing_ctx: list[Any] = []
        trigger_result = MagicMock(success=True, error=None, session_id="session-1")

        async def _capture_and_trigger(**_kwargs: Any) -> Any:
            from butlers.core.routing_context import _routing_ctx_var

            captured_routing_ctx.append(_routing_ctx_var.get())
            return trigger_result

        daemon.spawner.trigger = _capture_and_trigger

        assert "internal_context" not in inspect.signature(route_execute_fn).parameters

        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_base_request_context(),
            input={
                "prompt": "Store health facts from the conceptual excerpt.",
                "context": {"conceptual_message": conceptual_message},
            },
        )
        await asyncio.sleep(0.05)

        assert result["status"] == "accepted"
        persisted_envelope = _mock_route_inbox.await_args.kwargs["route_envelope"]
        assert persisted_envelope["input"]["context"]["conceptual_message"] == conceptual_message
        assert captured_routing_ctx == [{"conceptual_message": conceptual_message}]

    async def test_route_inbox_insert_failure_is_content_blind(
        self,
        tmp_path: Path,
        _mock_route_inbox: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sentinel = "222222222222222@lid PRIVATE MESSAGE SQL SELECT"
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        _daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None
        _mock_route_inbox.side_effect = RuntimeError(sentinel)

        with caplog.at_level("DEBUG"):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_base_request_context(),
                input={"prompt": "Store a fact."},
            )

        assert result["status"] == "error"
        assert result["error"]["class"] == "internal_error"
        assert sentinel not in result["error"]["message"]
        assert sentinel not in caplog.text

    async def test_background_runtime_failure_is_content_blind(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        _mock_route_inbox: AsyncMock,
    ) -> None:
        sentinel = "15551234567@s.whatsapp.net PRIVATE MESSAGE SQL SELECT"
        request_uuid = _base_request_context()["request_id"]
        inbox_uuid = uuid.UUID("11111111-1111-4111-8111-111111111111")
        _mock_route_inbox.return_value = inbox_uuid
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None
        daemon.spawner.trigger = AsyncMock(side_effect=RuntimeError(sentinel))
        mark_errored = AsyncMock(return_value=True)
        monkeypatch.setattr("butlers.core_tools._routing.route_inbox_mark_errored", mark_errored)
        tracer = _RecordingTracer()

        with (
            patch("butlers.core_tools._routing.trace.get_tracer", return_value=tracer),
            patch(
                "butlers.core_tools._routing.trace.get_current_span",
                side_effect=lambda *_args, **_kwargs: tracer.spans["butler.tool.route.execute"],
            ),
            patch("butlers.core_tools._routing.logger.warning") as route_warning,
            caplog.at_level("DEBUG"),
        ):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_base_request_context(),
                input={
                    "prompt": "Store a fact.",
                    "context": {"conceptual_message": {"excerpts": []}},
                },
            )
            await asyncio.gather(*daemon._route_inbox_tasks)

        assert result["status"] == "accepted"
        stored_error = mark_errored.await_args.args[2]
        assert stored_error == "route runtime failed (RuntimeError)"
        observability = (
            caplog.text
            + repr([record.__dict__ for record in caplog.records])
            + repr(route_warning.call_args_list)
            + repr({name: span.attributes for name, span in tracer.spans.items()})
        )
        assert sentinel not in observability
        assert request_uuid not in observability
        assert str(inbox_uuid) not in observability

    @pytest.mark.parametrize(
        ("result_error", "expected_class"),
        [
            (
                "RuntimeError: 15551234567@s.whatsapp.net PRIVATE MESSAGE SQL SELECT",
                "RuntimeError",
            ),
            ("provider returned opaque failure text", "runtime_unsuccessful"),
        ],
    )
    async def test_unsuccessful_runtime_result_uses_bounded_failure_class(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        _mock_route_inbox: AsyncMock,
        result_error: str,
        expected_class: str,
    ) -> None:
        request_uuid = _base_request_context()["request_id"]
        inbox_uuid = uuid.UUID("22222222-2222-4222-8222-222222222222")
        _mock_route_inbox.return_value = inbox_uuid
        patches = _patch_infra()
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None
        daemon.spawner.trigger = AsyncMock(
            return_value=MagicMock(success=False, error=result_error, session_id=uuid.uuid4())
        )
        mark_errored = AsyncMock(return_value=True)
        monkeypatch.setattr("butlers.core_tools._routing.route_inbox_mark_errored", mark_errored)

        with (
            patch("butlers.core_tools._routing.logger.warning") as route_warning,
            caplog.at_level("DEBUG"),
        ):
            await route_execute_fn(
                schema_version="route.v1",
                request_context=_base_request_context(),
                input={
                    "prompt": "Store a fact.",
                    "context": {"conceptual_message": {"excerpts": []}},
                },
            )
            await asyncio.gather(*daemon._route_inbox_tasks)

        assert mark_errored.await_args.args[2] == (
            f"route runtime returned unsuccessful result ({expected_class})"
        )
        observability = (
            caplog.text
            + repr([record.__dict__ for record in caplog.records])
            + repr(route_warning.call_args_list)
        )
        assert "15551234567@s.whatsapp.net" not in observability
        assert "PRIVATE MESSAGE SQL SELECT" not in observability
        assert "provider returned opaque failure text" not in observability
        assert request_uuid not in observability
        assert str(inbox_uuid) not in observability

    async def test_conceptual_route_dedup_log_omits_request_and_session_uuids(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        request_uuid = _base_request_context()["request_id"]
        session_uuid = uuid.UUID("33333333-3333-4333-8333-333333333333")
        patches = _patch_infra()
        patches["mock_pool"].fetchval.return_value = session_uuid
        butler_dir = _make_butler_toml(tmp_path, butler_name="health")
        _daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
        assert route_execute_fn is not None

        with (
            patch("butlers.core_tools._routing.logger.info") as route_info,
            caplog.at_level("INFO"),
        ):
            result = await route_execute_fn(
                schema_version="route.v1",
                request_context=_base_request_context(),
                input={
                    "prompt": "Store a fact.",
                    "context": {"conceptual_message": {"excerpts": []}},
                },
            )

        assert result["dedup"] is True
        observability = (
            caplog.text
            + repr([record.__dict__ for record in caplog.records])
            + repr(route_info.call_args_list)
        )
        assert request_uuid not in observability
        assert str(session_uuid) not in observability
