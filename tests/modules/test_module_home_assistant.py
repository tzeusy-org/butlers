"""Condensed Home Assistant module tests — behavioral contract only.

Replaces 190 tests with ~25 focused behavioral tests.

Covers:
- Module ABC compliance (instantiation, name, config_schema, dependencies)
- HomeAssistantConfig validation (defaults, extra=forbid, read_only)
- Tool registration (expected tools, read_only mode)
- Registry integration
- Startup credential resolution (happy path, missing url, missing token)
- Shutdown lifecycle (cleanup, idempotent)
- WebSocket URL derivation (http→ws, https→wss)
- Entity cache state_changed event (update + removal)
- Key tool behaviors (entity_id validation, list_areas, get_entity_state)
- HA source health recording: keyed upsert (success/error), no-DB no-op,
  upsert-failure swallowed, wiring from WS-connect and REST-poll failure paths
  (bu-8cdl1.12 slice 1)

[bu-7sd7a]
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules._roster_home import (
    HomeAssistantConfig,
    HomeAssistantModule,
)
from butlers.modules._roster_home.actuation import (
    ActuationRisk,
    classify_actuation,
    rollback_hint,
    verify_post_condition,
)
from butlers.modules.approvals.execution_context import (
    ApprovalExecutionContext,
    approval_tool_args_digest,
    reset_approval_execution_context,
    set_approval_execution_context,
)
from butlers.modules.base import Module, ToolMeta

pytestmark = pytest.mark.unit

EXPECTED_HA_TOOLS = {
    "ha_get_entity_state",
    "ha_list_entities",
    "ha_list_areas",
    "ha_list_services",
    "ha_get_history",
    "ha_get_statistics",
    "ha_render_template",
    "ha_call_service",
    "ha_activate_scene",
    "ha_maintenance_create",
    "ha_maintenance_complete",
    "ha_maintenance_list",
    "ha_maintenance_remove",
}

EXPECTED_HA_READ_ONLY_TOOLS = {
    "ha_get_entity_state",
    "ha_list_entities",
    "ha_list_areas",
    "ha_list_services",
    "ha_get_history",
    "ha_get_statistics",
    "ha_render_template",
    "ha_maintenance_list",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ha_module() -> HomeAssistantModule:
    return HomeAssistantModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        declared_name = kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_module_contract(self, ha_module: HomeAssistantModule) -> None:
        """HomeAssistantModule satisfies Module ABC: name, config_schema, dependencies."""
        assert issubclass(HomeAssistantModule, Module)
        assert ha_module.name == "home_assistant"
        assert ha_module.config_schema is HomeAssistantConfig
        assert issubclass(ha_module.config_schema, BaseModel)
        assert ha_module.dependencies == ["contacts", "approvals"]
        assert ha_module.migration_revisions() == "home"

    def test_tool_metadata_call_service_sensitive(self, ha_module: HomeAssistantModule) -> None:
        meta = ha_module.tool_metadata()
        assert "ha_call_service" in meta
        assert isinstance(meta["ha_call_service"], ToolMeta)
        assert meta["ha_call_service"].arg_sensitivities.get("domain") is True
        assert meta["ha_call_service"].arg_sensitivities.get("service") is True

    def test_tool_metadata_empty_when_read_only(self) -> None:
        module = HomeAssistantModule()
        module._config = HomeAssistantConfig(read_only=True)
        assert module.tool_metadata() == {}


class TestPhysicalRiskContract:
    @pytest.mark.parametrize(
        ("domain", "service", "expected"),
        [
            ("homeassistant", "update_entity", ActuationRisk.SAFE),
            ("light", "turn_on", ActuationRisk.REVERSIBLE),
            ("scene", "turn_on", ActuationRisk.CONSEQUENTIAL),
            ("lock", "unlock", ActuationRisk.PROTECTED),
            ("unknown_domain", "do_thing", ActuationRisk.PROTECTED),
        ],
    )
    def test_risk_map_is_explicit_and_unknown_calls_fail_closed(
        self, domain: str, service: str, expected: ActuationRisk
    ) -> None:
        assert classify_actuation(domain, service) is expected

    def test_reversible_action_has_concrete_rollback_hint(self) -> None:
        assert rollback_hint(
            "light", "turn_on", {"entity_id": "light.kitchen"}, {"brightness_pct": 80}
        ) == {
            "domain": "light",
            "service": "turn_off",
            "target": {"entity_id": "light.kitchen"},
            "data": None,
        }

    def test_post_condition_mismatch_is_never_verified(self) -> None:
        outcome = verify_post_condition(
            "light",
            "turn_on",
            None,
            {"light.kitchen": {"state": "off", "attributes": {}}},
        )
        assert outcome.verified is False
        assert "mismatch" in (outcome.reason or "")


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestHomeAssistantConfig:
    def test_url_optional(self) -> None:
        config = HomeAssistantConfig()
        assert config.url is None

    def test_defaults(self) -> None:
        config = HomeAssistantConfig()
        assert config.verify_ssl is False
        assert config.websocket_ping_interval == 30
        assert config.poll_interval_seconds == 60
        assert config.snapshot_interval_seconds == 300
        assert config.read_only is False

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HomeAssistantConfig(unknown_field="boom")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_registers_expected_tools(
        self, ha_module: HomeAssistantModule, mock_mcp: MagicMock
    ) -> None:
        await ha_module.register_tools(
            mcp=mock_mcp, config={"url": "http://ha.local"}, db=None, butler_name="test-butler"
        )
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_HA_TOOLS

    async def test_read_only_registers_only_query_tools(self, mock_mcp: MagicMock) -> None:
        module = HomeAssistantModule()
        await module.register_tools(
            mcp=mock_mcp, config={"read_only": True}, db=None, butler_name="test-butler"
        )
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_HA_READ_ONLY_TOOLS

    def test_default_registry_includes_home_assistant(self) -> None:
        from butlers.modules.registry import default_registry

        assert "home_assistant" in default_registry().available_modules


# ---------------------------------------------------------------------------
# Startup credential resolution
# ---------------------------------------------------------------------------


class TestOnStartup:
    async def test_startup_resolves_url_and_token(self, ha_module: HomeAssistantModule) -> None:
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        async def _resolve(pool: Any, info_type: str) -> str | None:
            return "http://ha.local" if info_type == "home_assistant_url" else "test-token"

        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(side_effect=_resolve),
        ):
            with patch("httpx.AsyncClient", return_value=MagicMock()):
                with patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()):
                    await ha_module.on_startup(config={}, db=mock_db)

        assert ha_module._url == "http://ha.local"
        assert ha_module._token == "test-token"

    async def test_startup_raises_if_url_missing(self, ha_module: HomeAssistantModule) -> None:
        mock_db = MagicMock()
        mock_db.pool = MagicMock()
        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError, match="home_assistant_url"):
                await ha_module.on_startup(config={}, db=mock_db)

    async def test_startup_raises_if_token_missing(self, ha_module: HomeAssistantModule) -> None:
        mock_db = MagicMock()
        mock_db.pool = MagicMock()

        async def _resolve(pool: Any, info_type: str) -> str | None:
            return "http://ha.local" if info_type == "home_assistant_url" else None

        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(side_effect=_resolve),
        ):
            with pytest.raises(RuntimeError, match="home_assistant_token"):
                await ha_module.on_startup(config={}, db=mock_db)


# ---------------------------------------------------------------------------
# Shutdown lifecycle
# ---------------------------------------------------------------------------


class TestShutdown:
    async def test_shutdown_cleans_up_client(self, ha_module: HomeAssistantModule) -> None:
        mock_client = AsyncMock()
        ha_module._client = mock_client
        ha_module._url = "http://ha.local"
        ha_module._token = "tok"
        ha_module._config = HomeAssistantConfig()

        await ha_module.on_shutdown()

        mock_client.aclose.assert_awaited_once()
        assert ha_module._client is None
        assert ha_module._url is None
        assert ha_module._token is None


# ---------------------------------------------------------------------------
# WebSocket URL derivation
# ---------------------------------------------------------------------------


class TestWebSocketUrlDerivation:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("http://homeassistant.local:8123", "ws://homeassistant.local:8123/api/websocket"),
            ("https://ha.example.com:8123", "wss://ha.example.com:8123/api/websocket"),
            ("http://ha.local/", "ws://ha.local/api/websocket"),
        ],
    )
    def test_ws_url_derivation(
        self, ha_module: HomeAssistantModule, url: str, expected: str
    ) -> None:
        ha_module._url = url
        assert ha_module._ws_url() == expected


# ---------------------------------------------------------------------------
# Entity cache — state_changed events
# ---------------------------------------------------------------------------


class TestEntityCache:
    async def test_state_changed_updates_cache(self, ha_module: HomeAssistantModule) -> None:
        from butlers.modules._roster_home import CachedEntity

        ha_module._entity_cache["light.kitchen"] = CachedEntity(
            entity_id="light.kitchen", state="off"
        )

        await ha_module._dispatch_ws_message(
            {
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {
                        "entity_id": "light.kitchen",
                        "new_state": {
                            "entity_id": "light.kitchen",
                            "state": "on",
                            "attributes": {"brightness": 200},
                            "last_changed": "2024-01-01T10:00:00+00:00",
                            "last_updated": "2024-01-01T10:00:00+00:00",
                        },
                    },
                },
            }
        )

        assert ha_module._entity_cache["light.kitchen"].state == "on"

    async def test_state_changed_null_removes_entity(self, ha_module: HomeAssistantModule) -> None:
        from butlers.modules._roster_home import CachedEntity

        ha_module._entity_cache["sensor.gone"] = CachedEntity(entity_id="sensor.gone", state="42")

        await ha_module._dispatch_ws_message(
            {
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {"entity_id": "sensor.gone", "new_state": None},
                },
            }
        )

        assert "sensor.gone" not in ha_module._entity_cache

    async def test_coalesced_ws_payload_resolves_pending_results(
        self, ha_module: HomeAssistantModule
    ) -> None:
        loop = asyncio.get_running_loop()
        first: asyncio.Future[dict[str, Any]] = loop.create_future()
        second: asyncio.Future[dict[str, Any]] = loop.create_future()
        ha_module._ws_pending[7] = first
        ha_module._ws_pending[8] = second

        await ha_module._dispatch_ws_payload(
            [
                {"type": "result", "id": 7, "success": True, "result": {"ok": "first"}},
                {"type": "result", "id": 8, "success": True, "result": {"ok": "second"}},
            ]
        )

        assert first.result() == {"ok": "first"}
        assert second.result() == {"ok": "second"}
        assert ha_module._ws_pending == {}

    @pytest.mark.parametrize(
        ("event_type", "method_name", "task_attr"),
        [
            ("area_registry_updated", "_fetch_area_registry", "_area_refresh_task"),
            ("entity_registry_updated", "_fetch_entity_registry", "_entity_refresh_task"),
        ],
    )
    async def test_registry_update_events_refresh_in_background(
        self,
        ha_module: HomeAssistantModule,
        event_type: str,
        method_name: str,
        task_attr: str,
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def _refresh() -> None:
            started.set()
            await release.wait()

        with patch.object(ha_module, method_name, new=AsyncMock(side_effect=_refresh)) as refresh:
            dispatch_task = asyncio.create_task(
                ha_module._dispatch_ws_message(
                    {"type": "event", "event": {"event_type": event_type, "data": {}}}
                )
            )
            await asyncio.sleep(0)
            assert dispatch_task.done()
            await dispatch_task
            await asyncio.wait_for(started.wait(), timeout=0.1)

            task = getattr(ha_module, task_attr)
            assert task is not None
            assert not task.done()
            assert refresh.await_count == 1

            release.set()
            await asyncio.wait_for(task, timeout=0.1)
            assert getattr(ha_module, task_attr) is None

    @pytest.mark.parametrize(
        ("event_type", "method_name"),
        [
            ("area_registry_updated", "_fetch_area_registry"),
            ("entity_registry_updated", "_fetch_entity_registry"),
        ],
    )
    async def test_registry_update_events_dedupe_inflight_refresh(
        self,
        ha_module: HomeAssistantModule,
        event_type: str,
        method_name: str,
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def _refresh() -> None:
            started.set()
            await release.wait()

        with patch.object(ha_module, method_name, new=AsyncMock(side_effect=_refresh)) as refresh:
            await ha_module._dispatch_ws_message(
                {"type": "event", "event": {"event_type": event_type, "data": {}}}
            )
            await asyncio.wait_for(started.wait(), timeout=0.1)
            await ha_module._dispatch_ws_message(
                {"type": "event", "event": {"event_type": event_type, "data": {}}}
            )

            assert refresh.await_count == 1

            release.set()
            task = ha_module._area_refresh_task or ha_module._entity_refresh_task
            assert task is not None
            await asyncio.wait_for(task, timeout=0.1)

    @pytest.mark.parametrize(
        ("method_name", "message"),
        [
            ("_fetch_area_registry", "area registry fetch timed out"),
            ("_fetch_entity_registry", "entity registry fetch timed out"),
        ],
    )
    async def test_registry_fetch_timeout_is_not_warning(
        self,
        ha_module: HomeAssistantModule,
        caplog: pytest.LogCaptureFixture,
        method_name: str,
        message: str,
    ) -> None:
        from butlers.modules._roster_home import (
            CachedArea,
            CachedEntity,
            CachedEntityRegistryEntry,
        )

        ha_module._ws_connected = True
        caplog.set_level(logging.INFO, logger="butlers.modules._roster_home")
        initial_area_cache = {
            "kitchen": CachedArea(area_id="kitchen", name="Kitchen"),
        }
        initial_entity_registry = {
            "sensor.kitchen": CachedEntityRegistryEntry(
                entity_id="sensor.kitchen",
                area_id="kitchen",
                device_id="device-1",
                platform="sensor",
            ),
        }
        initial_entity_area_map = {"sensor.kitchen": "kitchen"}
        initial_entity_cache = {
            "sensor.kitchen": CachedEntity(
                entity_id="sensor.kitchen",
                state="21",
                attributes={"unit_of_measurement": "°C"},
                area_id="kitchen",
            ),
        }
        ha_module._area_cache = dict(initial_area_cache)
        ha_module._entity_registry = dict(initial_entity_registry)
        ha_module._entity_area_map = dict(initial_entity_area_map)
        ha_module._entity_cache = dict(initial_entity_cache)

        with patch.object(ha_module, "_ws_command", new=AsyncMock(side_effect=TimeoutError)):
            await getattr(ha_module, method_name)()

        assert ha_module._area_cache == initial_area_cache
        assert ha_module._entity_registry == initial_entity_registry
        assert ha_module._entity_area_map == initial_entity_area_map
        assert ha_module._entity_cache == initial_entity_cache
        assert any(
            record.levelno == logging.INFO and message in record.message
            for record in caplog.records
        )
        assert not [
            record
            for record in caplog.records
            if record.name == "butlers.modules._roster_home" and record.levelno >= logging.WARNING
        ]


# ---------------------------------------------------------------------------
# Key tool behaviors
# ---------------------------------------------------------------------------


class TestToolBehaviors:
    @pytest.mark.parametrize(
        ("authorized_tool", "authorized_args"),
        [
            (
                "ha_activate_scene",
                {
                    "domain": "lock",
                    "service": "unlock",
                    "target": {"entity_id": "lock.front_door"},
                    "data": None,
                },
            ),
            (
                "ha_call_service",
                {
                    "domain": "lock",
                    "service": "lock",
                    "target": {"entity_id": "lock.front_door"},
                    "data": None,
                },
            ),
        ],
    )
    async def test_wrong_tool_or_args_cannot_bypass_home_physical_gate(
        self,
        ha_module: HomeAssistantModule,
        authorized_tool: str,
        authorized_args: dict[str, object],
    ) -> None:
        client = MagicMock()
        client.post = AsyncMock()
        pool = MagicMock()
        db = MagicMock()
        db.pool = pool
        ha_module._db = db
        ha_module._client = client
        task = asyncio.current_task()
        assert task is not None
        context = ApprovalExecutionContext(
            action_id=uuid.uuid4(),
            session_id=None,
            actor="human:owner",
            tool_name=authorized_tool,
            tool_args_digest=approval_tool_args_digest(authorized_args),
            authorized_task=task,
        )
        token = set_approval_execution_context(context)
        try:
            with (
                patch(
                    "butlers.core.approvals_hooks.is_approval_parking_available",
                    return_value=True,
                ),
                patch(
                    "butlers.core.approvals_hooks.park_pending_action",
                    new=AsyncMock(),
                ),
                patch(
                    "butlers.modules._roster_home.record_approval_event",
                    new=AsyncMock(),
                ),
            ):
                result = await ha_module._call_service(
                    "lock", "unlock", target={"entity_id": "lock.front_door"}
                )
        finally:
            reset_approval_execution_context(token)

        assert result["status"] == "pending_approval"
        client.post.assert_not_awaited()

    async def test_child_task_inheriting_context_cannot_bypass_home_physical_gate(
        self, ha_module: HomeAssistantModule
    ) -> None:
        tool_args = {
            "domain": "lock",
            "service": "unlock",
            "target": {"entity_id": "lock.front_door"},
            "data": None,
        }
        client = MagicMock()
        client.post = AsyncMock()
        pool = MagicMock()
        db = MagicMock()
        db.pool = pool
        ha_module._db = db
        ha_module._client = client
        task = asyncio.current_task()
        assert task is not None
        context = ApprovalExecutionContext(
            action_id=uuid.uuid4(),
            session_id=None,
            actor="human:owner",
            tool_name="ha_call_service",
            tool_args_digest=approval_tool_args_digest(tool_args),
            authorized_task=task,
        )
        token = set_approval_execution_context(context)
        try:
            with (
                patch(
                    "butlers.core.approvals_hooks.is_approval_parking_available",
                    return_value=True,
                ),
                patch(
                    "butlers.core.approvals_hooks.park_pending_action",
                    new=AsyncMock(),
                ),
                patch(
                    "butlers.modules._roster_home.record_approval_event",
                    new=AsyncMock(),
                ),
            ):
                result = await asyncio.create_task(ha_module._call_service(**tool_args))
        finally:
            reset_approval_execution_context(token)

        assert result["status"] == "pending_approval"
        client.post.assert_not_awaited()

    async def test_receipt_failure_refuses_physical_call(
        self, ha_module: HomeAssistantModule
    ) -> None:
        client = MagicMock()
        client.post = AsyncMock()
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("receipt store unavailable"))
        db = MagicMock()
        db.pool = pool
        ha_module._db = db
        ha_module._client = client

        with pytest.raises(RuntimeError, match="receipt store unavailable"):
            await ha_module._call_service("light", "turn_on", target={"entity_id": "light.kitchen"})

        client.post.assert_not_awaited()

    async def test_get_statistics_uses_current_recorder_command(
        self, ha_module: HomeAssistantModule
    ) -> None:
        statistics = {
            "sensor.energy": [{"start": 1_785_171_600_000, "sum": 1_012.5, "change": 7.5}]
        }
        command = AsyncMock(return_value=statistics)
        ha_module._ws_command = command

        result = await ha_module._get_statistics(
            statistic_ids=["sensor.energy"],
            start="2026-07-20T00:00:00+00:00",
            end="2026-07-27T00:00:00+00:00",
            period="hour",
        )

        assert result == statistics
        payload = command.await_args.args[0]
        assert payload["type"] == "recorder/statistics_during_period"
        assert payload["types"] == ["mean", "min", "max", "sum", "state", "change"]

    async def test_invalid_entity_id_raises(self, ha_module: HomeAssistantModule) -> None:
        ha_module._client = MagicMock()
        with pytest.raises(ValueError):
            await ha_module._get_entity_state("light/invalid")

    async def test_list_areas_returns_sorted(self, ha_module: HomeAssistantModule) -> None:
        from butlers.modules._roster_home import CachedArea

        ha_module._area_cache["z_area"] = CachedArea(area_id="z", name="Zebra Room")
        ha_module._area_cache["a_area"] = CachedArea(area_id="a", name="Alpha Room")
        result = await ha_module._list_areas()
        names = [a["name"] for a in result]
        assert names == sorted(names)

    async def test_get_entity_state_from_cache(self, ha_module: HomeAssistantModule) -> None:
        from butlers.modules._roster_home import CachedEntity

        ha_module._entity_cache["sensor.temp"] = CachedEntity(
            entity_id="sensor.temp", state="22.5", attributes={"unit": "°C"}
        )
        ha_module._client = MagicMock()
        result = await ha_module._get_entity_state("sensor.temp")
        assert result is not None
        assert result["state"] == "22.5"


# ---------------------------------------------------------------------------
# HA source health recording (bu-8cdl1.12 slice 1)
# ---------------------------------------------------------------------------


class TestHASourceHealthRecording:
    async def test_record_success_upserts_healthy_row(self, ha_module: HomeAssistantModule) -> None:
        pool = AsyncMock()
        db = MagicMock()
        db.pool = pool
        ha_module._db = db

        await ha_module._record_ha_source_success()

        pool.execute.assert_awaited_once()
        query = pool.execute.await_args.args[0]
        assert "ON CONFLICT (source) DO UPDATE" in query
        assert "'healthy'" in query
        assert pool.execute.await_args.args[1] == "home_assistant"

    async def test_record_error_upserts_error_row_with_message(
        self, ha_module: HomeAssistantModule
    ) -> None:
        pool = AsyncMock()
        db = MagicMock()
        db.pool = pool
        ha_module._db = db

        await ha_module._record_ha_source_error("connection refused")

        pool.execute.assert_awaited_once()
        args = pool.execute.await_args.args
        assert "'error'" in args[0]
        assert args[1] == "home_assistant"
        assert args[2] == "connection refused"

    async def test_record_success_then_error_is_a_keyed_upsert(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """Repeated calls target the same source key — no unbounded row growth."""
        pool = AsyncMock()
        db = MagicMock()
        db.pool = pool
        ha_module._db = db

        await ha_module._record_ha_source_success()
        await ha_module._record_ha_source_error("boom")
        await ha_module._record_ha_source_success()

        assert pool.execute.await_count == 3
        for call in pool.execute.await_args_list:
            assert call.args[1] == "home_assistant"

    async def test_record_is_noop_without_a_pool(self, ha_module: HomeAssistantModule) -> None:
        ha_module._db = None
        # Neither call should raise when no DB pool is available.
        await ha_module._record_ha_source_success()
        await ha_module._record_ha_source_error("boom")

    async def test_record_failure_is_swallowed(self, ha_module: HomeAssistantModule) -> None:
        """A health-upsert failure logs a warning and never propagates to the caller."""
        pool = AsyncMock()
        pool.execute.side_effect = RuntimeError("db down")
        db = MagicMock()
        db.pool = pool
        ha_module._db = db

        await ha_module._record_ha_source_success()  # does not raise
        await ha_module._record_ha_source_error("boom")  # does not raise

    async def test_ws_connect_and_seed_failure_records_error(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """A WebSocket connect failure at startup records the outage, not just a log line."""
        ha_module._config = HomeAssistantConfig()
        ha_module._ws_connect = AsyncMock(side_effect=RuntimeError("ws down"))
        ha_module._record_ha_source_error = AsyncMock()
        ha_module._start_snapshot_task = MagicMock()
        ha_module._start_poll_fallback = MagicMock()
        ha_module._schedule_reconnect = MagicMock()

        await ha_module._ws_connect_and_seed()

        ha_module._record_ha_source_error.assert_awaited_once()
        assert "ws down" in ha_module._record_ha_source_error.await_args.args[0]

    async def test_poll_loop_failure_records_error(self, ha_module: HomeAssistantModule) -> None:
        """A REST poll failure in the fallback loop records the outage (previously swallowed)."""
        ha_module._config = HomeAssistantConfig(poll_interval_seconds=0)
        ha_module._shutdown = False
        ha_module._ws_connected = False
        ha_module._record_ha_source_error = AsyncMock()

        async def _raise_and_stop(*_args: Any, **_kwargs: Any) -> None:
            ha_module._shutdown = True
            raise RuntimeError("poll boom")

        ha_module._seed_entity_cache_from_rest = AsyncMock(side_effect=_raise_and_stop)

        await asyncio.wait_for(ha_module._poll_loop(), timeout=5.0)

        ha_module._record_ha_source_error.assert_awaited_once()
        assert "poll boom" in ha_module._record_ha_source_error.await_args.args[0]

    async def test_seed_entity_cache_from_rest_success_records_health(
        self, ha_module: HomeAssistantModule
    ) -> None:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=[{"entity_id": "light.kitchen", "state": "on"}])
        ha_module._client = AsyncMock()
        ha_module._client.get = AsyncMock(return_value=resp)
        ha_module._record_ha_source_success = AsyncMock()

        await ha_module._seed_entity_cache_from_rest()

        ha_module._record_ha_source_success.assert_awaited_once()
