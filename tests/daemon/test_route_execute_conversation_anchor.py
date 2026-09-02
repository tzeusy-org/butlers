"""Tests for the conversation-anchor wiring in route.execute (bu-bkthr).

Follow-up to bu-ep4ks.8 (PR #3582): every inbound thread that already
normalizes a ``source_thread_identity`` at ingest (Telegram, email, ...)
should get a durable ``dashboard_conversations`` anchor row on the TARGET
butler, via ``conversation_get_or_create_by_thread``, and the resulting
conversation id should be forwarded to ``spawner.trigger(conversation_id=...)``
so the spawner can attach a provider resume handle to it.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


class _NoopLeaseHeartbeat:
    async def __aenter__(self) -> asyncio.Event:
        return asyncio.Event()

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _ReclaimedLeaseHeartbeat:
    """Controllable heartbeat used to model takeover during pre-spawn I/O."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.lease_lost = asyncio.Event()

    async def __aenter__(self) -> asyncio.Event:
        self.entered.set()
        return self.lease_lost

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _ResumeRecordingAdapter(RuntimeAdapter):
    supports_resume = True

    def __init__(self) -> None:
        self.invoke_calls: list[str | None] = []
        self._last_process_info: dict[str, Any] | None = None
        self.two_invocations = asyncio.Event()

    @property
    def binary_name(self) -> str:
        return "mock"

    @property
    def last_process_info(self) -> dict[str, Any] | None:
        return self._last_process_info

    async def invoke(self, *_args: Any, **kwargs: Any) -> tuple[str, list, None]:
        self.invoke_calls.append(kwargs.get("resume_session_id"))
        if len(self.invoke_calls) == 2:
            self.two_invocations.set()
        self._last_process_info = {
            "runtime_type": DEFAULT_RUNTIME_TYPE,
            "provider_session_id": "provider-session-from-first-turn",
        }
        return "ok", [], None

    async def reset(self) -> None:
        return None

    def build_config_file(self, _mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        config_file = tmp_dir / "runtime.json"
        config_file.write_text("{}")
        return config_file

    def parse_system_prompt_file(self, _config_dir: Path) -> str:
        return ""


# ---------------------------------------------------------------------------
# Helpers (mirrors tests/daemon/test_route_execute_async_dispatch.py)
# ---------------------------------------------------------------------------


def _make_butler_toml(tmp_path: Path, *, butler_name: str = "health", port: int = 9200) -> Path:
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
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
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
        patch("butlers.lifecycle.Spawner", return_value=patches["mock_spawner"]),
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
    source_thread_identity: str | None = "12345:678",
    external_conversation_id: str | None = "telegram:12345",
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
        "received_at": "2026-02-18T10:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }
    if source_thread_identity is not None:
        ctx["source_thread_identity"] = source_thread_identity
    if external_conversation_id is not None:
        ctx["external_conversation_id"] = external_conversation_id
    return ctx


def _make_trigger_mock():
    trigger_mock = AsyncMock()
    trigger_result = MagicMock()
    trigger_result.session_id = uuid.uuid4()
    trigger_mock.return_value = trigger_result
    return trigger_mock


def _renew_processing_claim_patch():
    """Keep route tests focused on routing rather than the DB lease primitive."""
    return patch(
        "butlers.core.route_inbox.route_inbox_renew_processing_claim",
        new_callable=AsyncMock,
        return_value=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_creates_conversation_anchor_and_forwards_conversation_id(
    tmp_path: Path,
) -> None:
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    trigger_mock = _make_trigger_mock()
    daemon.spawner.trigger = trigger_mock

    conversation_id = uuid.uuid4()
    mock_get_or_create = AsyncMock(return_value=({"id": conversation_id, "is_new": True}, True))

    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
            side_effect=lambda *_args, **_kwargs: _NoopLeaseHeartbeat(),
        ),
        _renew_processing_claim_patch(),
        patch("butlers.core_tools._routing.route_inbox_mark_processed", new_callable=AsyncMock),
        patch(
            "butlers.api.conversations.conversation_get_or_create_by_thread",
            mock_get_or_create,
        ),
    ):
        await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_channel="telegram_bot", source_thread_identity="12345:678"
            ),
            input={"prompt": "Hello from Telegram."},
        )
        await asyncio.sleep(0.05)

    mock_get_or_create.assert_awaited_once()
    call_kwargs = mock_get_or_create.call_args.kwargs
    assert call_kwargs["butler_name"] == "health"
    assert call_kwargs["source_channel"] == "telegram_bot"
    assert call_kwargs["external_conversation_id"] == "telegram:12345"
    # The raw prompt, not the <routed_message>-wrapped text sent to the runtime.
    assert call_kwargs["first_message"] == "Hello from Telegram."
    assert "<routed_message>" not in call_kwargs["first_message"]

    trigger_mock.assert_awaited()
    assert trigger_mock.call_args.kwargs["conversation_id"] == conversation_id


async def test_two_routed_telegram_ingests_reuse_anchor_and_resume_provider_session(
    tmp_path: Path,
) -> None:
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    anchors: dict[tuple[str, str, str], uuid.UUID] = {}

    async def get_or_create_anchor(
        _pool: Any,
        *,
        butler_name: str,
        source_channel: str,
        external_conversation_id: str,
        first_message: str,
    ) -> tuple[dict[str, Any], bool]:
        del first_message
        key = (butler_name, source_channel, external_conversation_id)
        is_new = key not in anchors
        conversation_id = anchors.setdefault(key, uuid.uuid4())
        return {"id": conversation_id}, is_new

    provider_state: dict[str, Any] | None = None

    async def get_provider_session(*_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
        return provider_state

    async def set_provider_session(
        _pool: Any,
        _conversation_id: uuid.UUID,
        *,
        provider_session_id: str,
        provider_runtime_type: str,
        **_kwargs: Any,
    ) -> None:
        nonlocal provider_state
        provider_state = {
            "provider_session_id": provider_session_id,
            "provider_runtime_type": provider_runtime_type,
            "provider_session_updated_at": __import__("datetime").datetime.now(
                __import__("datetime").UTC
            ),
        }

    adapter = _ResumeRecordingAdapter()
    real_spawner = Spawner(
        config=daemon.config,
        config_dir=butler_dir,
        pool=patches["mock_pool"],
        runtime=adapter,
    )
    real_spawner._ensure_mcp_endpoints_warmed = AsyncMock(return_value=None)
    daemon.spawner.trigger.side_effect = real_spawner.trigger
    quota_allowed = QuotaStatus(
        allowed=True,
        usage_24h=0,
        limit_24h=None,
        usage_30d=0,
        limit_30d=None,
    )

    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            side_effect=[uuid.uuid4(), uuid.uuid4()],
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            side_effect=[uuid.uuid4(), uuid.uuid4()],
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
            side_effect=lambda *_args, **_kwargs: _NoopLeaseHeartbeat(),
        ),
        _renew_processing_claim_patch(),
        patch("butlers.core_tools._routing.route_inbox_mark_processed", new_callable=AsyncMock),
        patch(
            "butlers.api.conversations.conversation_get_or_create_by_thread",
            new_callable=AsyncMock,
            side_effect=get_or_create_anchor,
        ) as anchor_lookup,
        patch(
            "butlers.core.spawner.session_create",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
        patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=(
                DEFAULT_RUNTIME_TYPE,
                "test-model",
                [],
                uuid.uuid4(),
                1800,
                "workhorse",
            ),
        ),
        patch(
            "butlers.core.spawner.check_token_quota",
            new_callable=AsyncMock,
            return_value=quota_allowed,
        ),
        patch(
            "butlers.core.spawner.conversation_get_provider_session",
            new_callable=AsyncMock,
            side_effect=get_provider_session,
        ),
        patch(
            "butlers.core.spawner.conversation_set_provider_session",
            new_callable=AsyncMock,
            side_effect=set_provider_session,
        ),
    ):
        for message_id, prompt in (("1", "first turn"), ("2", "second turn")):
            await route_execute_fn(
                schema_version="route.v1",
                request_context=_route_request_context(
                    source_channel="telegram_bot",
                    source_thread_identity=f"12345:{message_id}",
                    external_conversation_id="telegram:12345",
                ),
                input={"prompt": prompt},
            )
            await asyncio.sleep(0.1)

        await asyncio.wait_for(adapter.two_invocations.wait(), timeout=5)

    assert anchor_lookup.await_count == 2
    assert len(anchors) == 1
    assert [call.kwargs["external_conversation_id"] for call in anchor_lookup.await_args_list] == [
        "telegram:12345",
        "telegram:12345",
    ]
    assert adapter.invoke_calls == [None, "provider-session-from-first-turn"]


async def test_skips_conversation_anchor_when_no_thread_identity(tmp_path: Path) -> None:
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    trigger_mock = _make_trigger_mock()
    daemon.spawner.trigger = trigger_mock

    mock_get_or_create = AsyncMock()

    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
            side_effect=lambda *_args, **_kwargs: _NoopLeaseHeartbeat(),
        ),
        _renew_processing_claim_patch(),
        patch("butlers.core_tools._routing.route_inbox_mark_processed", new_callable=AsyncMock),
        patch(
            "butlers.api.conversations.conversation_get_or_create_by_thread",
            mock_get_or_create,
        ),
    ):
        await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_channel="api",
                source_thread_identity=None,
                external_conversation_id=None,
            ),
            input={"prompt": "Run health check."},
        )
        await asyncio.sleep(0.05)

    mock_get_or_create.assert_not_awaited()
    trigger_mock.assert_awaited()
    assert trigger_mock.call_args.kwargs["conversation_id"] is None


async def test_conversation_anchor_lookup_failure_does_not_block_routing(
    tmp_path: Path,
) -> None:
    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    trigger_mock = _make_trigger_mock()
    daemon.spawner.trigger = trigger_mock

    mock_get_or_create = AsyncMock(side_effect=RuntimeError("db unavailable"))

    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
            side_effect=lambda *_args, **_kwargs: _NoopLeaseHeartbeat(),
        ),
        _renew_processing_claim_patch(),
        patch("butlers.core_tools._routing.route_inbox_mark_processed", new_callable=AsyncMock),
        patch(
            "butlers.api.conversations.conversation_get_or_create_by_thread",
            mock_get_or_create,
        ),
    ):
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_channel="telegram_bot", source_thread_identity="12345:678"
            ),
            input={"prompt": "Hello from Telegram."},
        )
        await asyncio.sleep(0.05)

    assert result["status"] == "accepted"
    mock_get_or_create.assert_awaited_once()
    trigger_mock.assert_awaited()
    assert trigger_mock.call_args.kwargs["conversation_id"] is None


async def test_reclaimed_lease_during_anchor_never_invokes_original_worker(
    tmp_path: Path,
) -> None:
    """A recovery takeover while anchor I/O waits fences the original route worker."""

    from butlers.core.route_inbox import RouteInboxLeaseLost

    patches = _patch_infra("health")
    butler_dir = _make_butler_toml(tmp_path, butler_name="health")
    daemon, route_execute_fn = await _start_daemon_with_route_execute(butler_dir, patches)
    assert route_execute_fn is not None

    trigger_mock = _make_trigger_mock()
    trigger_mock.return_value.success = True
    trigger_mock.return_value.error = None
    daemon.spawner.trigger = trigger_mock
    heartbeat = _ReclaimedLeaseHeartbeat()
    anchor_saw_heartbeat = False
    anchor_called = asyncio.Event()

    async def reclaim_during_anchor(
        _pool: object,
        **_kwargs: object,
    ) -> tuple[dict[str, object], bool]:
        nonlocal anchor_saw_heartbeat
        anchor_saw_heartbeat = heartbeat.entered.is_set()
        anchor_called.set()
        if anchor_saw_heartbeat:
            # This models a recovery worker replacing the claim while the
            # original worker is still waiting on optional anchor I/O.
            heartbeat.lease_lost.set()
        return {"id": uuid.uuid4()}, True

    async def wait_while_reclaiming(*args: object) -> object:
        if len(args) == 2:
            lease_lost, invocation = args
        else:
            _pool, _row_id, _claim_id, lease_lost, invocation = args
        assert isinstance(lease_lost, asyncio.Event)
        assert callable(invocation)
        if lease_lost.is_set():
            raise RouteInboxLeaseLost("reclaimed during anchor")
        return await invocation()

    mark_processed = AsyncMock(return_value=True)
    mark_errored = AsyncMock(return_value=True)
    with (
        patch(
            "butlers.core_tools._routing.route_inbox_insert",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_claim_processing",
            new_callable=AsyncMock,
            return_value=uuid.uuid4(),
        ),
        patch(
            "butlers.core_tools._routing.route_inbox_processing_lease_heartbeat",
            return_value=heartbeat,
        ),
        patch("butlers.core_tools._routing.route_inbox_wait_while_claimed", wait_while_reclaiming),
        patch("butlers.core_tools._routing.route_inbox_mark_processed", mark_processed),
        patch("butlers.core_tools._routing.route_inbox_mark_errored", mark_errored),
        patch(
            "butlers.api.conversations.conversation_get_or_create_by_thread",
            reclaim_during_anchor,
        ),
    ):
        result = await route_execute_fn(
            schema_version="route.v1",
            request_context=_route_request_context(
                source_channel="telegram_bot", source_thread_identity="12345:678"
            ),
            input={"prompt": "Do not spawn after the reclaimed anchor."},
        )
        assert result["status"] == "accepted"
        tasks = tuple(daemon._route_inbox_tasks)
        assert len(tasks) == 1
        await asyncio.wait_for(tasks[0], timeout=1)
        assert anchor_called.is_set()

    assert anchor_saw_heartbeat is True
    trigger_mock.assert_not_awaited()
    mark_processed.assert_not_awaited()
    mark_errored.assert_not_awaited()
