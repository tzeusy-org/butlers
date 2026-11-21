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
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest
from fastmcp import FastMCP
from pydantic import BaseModel, ValidationError

import butlers.modules._roster_home.actuation as ha_actuation
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
from butlers.testing.mcp import registered_tools

pytestmark = pytest.mark.unit

EXPECTED_HA_TOOLS = {
    "ha_get_entity_state",
    "ha_list_entities",
    "ha_list_areas",
    "ha_list_services",
    "ha_get_history",
    "ha_get_recorded_history",
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
    "ha_get_recorded_history",
    "ha_get_statistics",
    "ha_render_template",
    "ha_maintenance_list",
}


class TestRecordedHistory:
    async def test_named_ordered_durable_history_and_source_freshness(self, mock_mcp) -> None:
        start = datetime(2026, 10, 1, tzinfo=UTC)
        rows = [
            {"entity_id": "sensor.room", "state": "20", "recorded_at": start},
            {"entity_id": "sensor.room", "state": "21", "recorded_at": start + timedelta(hours=1)},
        ]
        pool = SimpleNamespace(
            fetch=AsyncMock(return_value=rows),
            fetchrow=AsyncMock(
                return_value={
                    "status": "healthy",
                    "last_success_at": start,
                    "lease_current": True,
                }
            ),
        )
        module = HomeAssistantModule()
        await module.register_tools(mock_mcp, {}, SimpleNamespace(pool=pool), butler_name="home")
        tool = (await registered_tools(mock_mcp))["ha_get_recorded_history"]
        module._client = MagicMock()
        module._client.get = AsyncMock(side_effect=AssertionError("must not contact HA"))
        result = await tool(
            ["sensor.room"], start.isoformat(), (start + timedelta(days=1)).isoformat()
        )
        assert result == {
            "status": "ok",
            "transitions": rows,
            "truncated": False,
            "source": {"status": "healthy", "last_success_at": start},
        }
        sql, entities, lower, upper, count = pool.fetch.await_args.args
        assert entities == ["sensor.room"] and lower == start and upper == start + timedelta(days=1)
        assert count == 501
        assert "entity_id = ANY($1::text[])" in sql
        assert "recorded_at >= $2 AND recorded_at < $3" in sql
        assert "ORDER BY recorded_at ASC, id ASC" in sql and "LIMIT $4" in sql
        assert "attributes" not in sql
        assert "now()" in pool.fetchrow.await_args.args[0]
        pool.fetch.return_value = [{**rows[0], "attributes": {"unit": "C"}}]
        with_attributes = await tool(
            ["sensor.room"],
            start.isoformat(),
            (start + timedelta(days=31)).isoformat(),
            limit=2000,
            include_attributes=True,
        )
        assert with_attributes["transitions"][0]["attributes"] == {"unit": "C"}
        assert "attributes" in pool.fetch.await_args.args[0]
        assert pool.fetch.await_args.args[-1] == 2001
        module._client.get.assert_not_awaited()

    async def test_stored_history_survives_outage_and_missing_or_expired_health(
        self, mock_mcp
    ) -> None:
        last = datetime(2026, 10, 1, tzinfo=UTC)
        rows = [{"entity_id": "person.owner", "state": "home", "recorded_at": last}]
        pool = SimpleNamespace(fetch=AsyncMock(return_value=rows), fetchrow=AsyncMock())
        module = HomeAssistantModule()
        await module.register_tools(
            mock_mcp, {"read_only": True}, SimpleNamespace(pool=pool), butler_name="home"
        )
        tool = (await registered_tools(mock_mcp))["ha_get_recorded_history"]
        for health, expected in [
            ({"status": "error", "last_success_at": last, "lease_current": False}, "error"),
            ({"status": "healthy", "last_success_at": last, "lease_current": False}, "stale"),
            ({"status": "healthy", "last_success_at": None, "lease_current": False}, "unknown"),
            (None, "unknown"),
        ]:
            pool.fetchrow.return_value = health
            result = await tool(
                ["person.owner"], last.isoformat(), (last + timedelta(days=1)).isoformat()
            )
            assert result["status"] == "ok" and result["transitions"] == rows
            assert result["source"] == {
                "status": expected,
                "last_success_at": health["last_success_at"] if health else None,
            }
        pool.fetch.return_value = []
        empty = await tool(
            ["person.owner"], last.isoformat(), (last + timedelta(days=1)).isoformat()
        )
        assert empty["status"] == "ok" and empty["transitions"] == []
        assert empty["source"]["status"] == "unknown"

    @pytest.mark.parametrize(
        "change,refused",
        [
            ({"entity_ids": []}, True),
            ({"entity_ids": [""]}, True),
            ({"start": "not-a-date"}, True),
            ({"start": "2026-10-01T00:00:00"}, True),
            ({"end": "2026-10-01T00:00:00Z"}, True),
            ({"end": "2026-11-02T00:00:00Z"}, True),
            ({"limit": 2001}, True),
            ({"limit": 0}, True),
            ({"limit": True}, True),
            ({"limit": 1}, False),
        ],
    )
    async def test_recorded_history_bounds_and_truncation(self, mock_mcp, change, refused) -> None:
        pool = SimpleNamespace(
            fetch=AsyncMock(
                return_value=[
                    {
                        "entity_id": "sensor.room",
                        "state": str(i),
                        "recorded_at": datetime(2026, 10, 1, i, tzinfo=UTC),
                    }
                    for i in range(2)
                ]
            ),
            fetchrow=AsyncMock(return_value=None),
        )
        module = HomeAssistantModule()
        await module.register_tools(mock_mcp, {}, SimpleNamespace(pool=pool), butler_name="home")
        tool = (await registered_tools(mock_mcp))["ha_get_recorded_history"]
        args = {
            "entity_ids": ["sensor.room"],
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
            **change,
        }
        if refused:
            with pytest.raises(ValueError):
                await tool(**args)
            pool.fetch.assert_not_awaited()
            pool.fetchrow.assert_not_awaited()
        else:
            result = await tool(**args)
            assert result["truncated"] is True
            assert result["transitions"] == pool.fetch.return_value[:1]
            assert pool.fetch.await_args.args[-1] == 2

    async def test_missing_storage_or_grant_is_typed_unavailable(self, mock_mcp) -> None:
        module = HomeAssistantModule()
        pool = SimpleNamespace(fetch=AsyncMock(), fetchrow=AsyncMock(return_value=None))
        await module.register_tools(mock_mcp, {}, SimpleNamespace(pool=pool), butler_name="home")
        tool = (await registered_tools(mock_mcp))["ha_get_recorded_history"]
        args = (["sensor.room"], "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
        for error, reason in [
            (asyncpg.UndefinedTableError("private SQL details"), "missing_table"),
            (asyncpg.InsufficientPrivilegeError("private SQL details"), "permission_denied"),
            (asyncpg.PostgresConnectionError("private endpoint"), "database_unavailable"),
        ]:
            for phase in ("health", "history"):
                pool.fetchrow.side_effect = error if phase == "health" else None
                pool.fetch.side_effect = error if phase == "history" else None
                result = await tool(*args)
                assert result == {"status": "unavailable", "reason": reason}
                assert "transitions" not in result
        module._db = None
        assert await tool(*args) == {"status": "unavailable", "reason": "database_unavailable"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ha_module() -> HomeAssistantModule:
    return HomeAssistantModule()


@pytest.fixture
def mock_mcp() -> FastMCP:
    return FastMCP("providers8-home_assistant-tools")


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
        assert ha_actuation.effective_actuation_risk(domain, service, None, None).base is expected
        assert ha_actuation.effective_actuation_risk(domain, service, None, None).risk is (
            ActuationRisk.CONSEQUENTIAL if expected is ActuationRisk.REVERSIBLE else expected
        )

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
        assert set((await registered_tools(mock_mcp)).keys()) == EXPECTED_HA_TOOLS

    async def test_read_only_registers_only_query_tools(self, mock_mcp: MagicMock) -> None:
        module = HomeAssistantModule()
        await module.register_tools(
            mcp=mock_mcp, config={"read_only": True}, db=None, butler_name="test-butler"
        )
        assert set((await registered_tools(mock_mcp)).keys()) == EXPECTED_HA_READ_ONLY_TOOLS

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
            with patch("httpx.AsyncClient", return_value=MagicMock()) as factory:
                with patch.object(HomeAssistantModule, "_ws_connect_and_seed", new=AsyncMock()):
                    await ha_module.on_startup(config={}, db=mock_db)

        assert ha_module._url == "http://ha.local"
        assert ha_module._token == "test-token"
        assert factory.call_args.kwargs["verify"] is False
        assert factory.call_args.kwargs["timeout"] == httpx.Timeout(5.0)
        assert factory.call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"

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
        pool.fetch = AsyncMock(return_value=[])
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
        """Connect and post-auth setup failures both record the outage."""
        ha_module._config = HomeAssistantConfig()
        ha_module._ws_connect = AsyncMock(side_effect=RuntimeError("ws down"))
        ha_module._record_ha_source_error = AsyncMock()
        ha_module._start_snapshot_task = MagicMock()
        ha_module._start_poll_fallback = MagicMock()
        ha_module._schedule_reconnect = MagicMock()

        await ha_module._ws_connect_and_seed()

        ha_module._record_ha_source_error.assert_awaited_once()
        assert "ws down" in ha_module._record_ha_source_error.await_args.args[0]

        ha_module._ws_connect = AsyncMock(return_value=None)
        ha_module._seed_entity_cache_from_rest = AsyncMock(side_effect=RuntimeError("seed down"))
        ha_module._start_ws_message_loop = MagicMock()
        ha_module._start_ws_ping_task = MagicMock()
        ha_module._ws_close = AsyncMock()
        ha_module._record_ha_source_error.reset_mock()

        await ha_module._ws_connect_and_seed()

        ha_module._record_ha_source_error.assert_awaited_once()
        assert "seed down" in ha_module._record_ha_source_error.await_args.args[0]
        ha_module._ws_close.assert_awaited_once()

    async def test_ws_liveness_refreshes_and_revokes_source_health(
        self, ha_module: HomeAssistantModule
    ) -> None:
        """A pong renews the lease; transport loss revokes it before fallback polling."""
        ha_module._record_ha_source_success = AsyncMock()
        await ha_module._dispatch_ws_message({"type": "pong"})
        ha_module._record_ha_source_success.assert_awaited_once()

        ha_module._shutdown = False
        ha_module._ws_connected = True
        ha_module._ws_connection = MagicMock(closed=True)
        ha_module._record_ha_source_error = AsyncMock()
        ha_module._start_poll_fallback = MagicMock()
        ha_module._schedule_reconnect = MagicMock()

        await ha_module._ws_message_loop()

        ha_module._record_ha_source_error.assert_awaited_once()
        ha_module._start_poll_fallback.assert_called_once()
        ha_module._schedule_reconnect.assert_called_once()

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


# Argument-aware actuation cohorts exercise the registered tool, not a classifier stub.
async def _registered_actuation(
    mock_mcp, monkeypatch, args, *, units=None, registry=(), cache=None
):
    from butlers.modules._roster_home import CachedEntity

    module = HomeAssistantModule()
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)  # not a departure fix: the replay gate passes
    # An empty critical registry changes nothing; an Exception makes it unreadable.
    if isinstance(registry, Exception):
        pool.fetch = AsyncMock(side_effect=registry)
    else:
        pool.fetch = AsyncMock(return_value=[{"entity_id": entity_id} for entity_id in registry])
    db = MagicMock()
    db.pool = pool
    await module.register_tools(mock_mcp, {}, db, "home")
    response = MagicMock()
    response.content = b"json"
    response.json.return_value = []
    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    async def observe(path):
        result = MagicMock()
        result.json.return_value = {
            "state": "on" if args["service"] == "turn_on" else "off",
            "attributes": {"temperature": (args.get("data") or {}).get("temperature")},
        }
        return result

    client.get = AsyncMock(side_effect=observe)
    module._client = client
    for entity_id, unit in (units or {}).items():
        module._entity_cache[entity_id] = CachedEntity(
            entity_id=entity_id, state="on", attributes={"temperature_unit": unit}
        )
    for entity_id, attributes in (cache or {}).items():
        module._entity_cache[entity_id] = CachedEntity(
            entity_id=entity_id, state="on", attributes=attributes
        )
    park = AsyncMock()
    audit = AsyncMock()
    event = AsyncMock()
    monkeypatch.setattr(
        "butlers.core.approvals_hooks.is_approval_parking_available", lambda pool: True
    )
    monkeypatch.setattr("butlers.core.approvals_hooks.park_pending_action", park)
    monkeypatch.setattr("butlers.modules._roster_home.record_approval_event", audit)
    monkeypatch.setattr(module, "_emit_actuation_event", event)
    return SimpleNamespace(
        module=module,
        tool=(await registered_tools(mock_mcp))["ha_call_service"],
        client=client,
        pool=pool,
        park=park,
        audit=audit,
        event=event,
    )


def _assert_parked(case, args, result, case_id):
    assert result["status"] == "pending_approval", case_id
    assert result["risk"] == "consequential", case_id
    case.client.post.assert_not_awaited()
    case.client.get.assert_not_awaited()
    case.pool.execute.assert_not_awaited()
    assert case.park.await_count == 1, case_id
    stored = case.park.await_args.kwargs
    assert stored["tool_args"] == args, case_id
    assert stored["why"], case_id


def _assert_automatic(case, args, result, case_id):
    assert result["status"] == "succeeded", case_id
    assert result["risk"] == "reversible", case_id
    case.park.assert_not_awaited()
    expected = dict(args["data"] or {})
    expected["target"] = args["target"]
    case.client.post.assert_awaited_once_with(
        f"/api/services/{args['domain']}/{args['service']}", json=expected
    )
    start = case.pool.execute.await_args_list[0].args
    assert start[4:7] == (args["target"], args["data"], "reversible"), case_id
    assert start[10] == args, case_id
    assert case.pool.execute.await_args_list[1].args[2] == "succeeded", case_id
    case.event.assert_awaited_once_with(
        pool=case.pool,
        attempt_id=uuid.UUID(result["attempt_id"]),
        domain=args["domain"],
        service=args["service"],
        risk=ActuationRisk.REVERSIBLE,
        status="succeeded",
        attention_required=False,
    )


_TARGET_RISK_CASES = [
    ("area", {"area_id": "fixture_area"}),
    ("device", {"device_id": "fixture_device"}),
    ("label", {"label_id": "fixture_label"}),
    ("all_scalar", {"entity_id": "all"}),
    ("all_list", {"entity_id": ["all"]}),
    ("mixed_domain", {"entity_id": ["switch.fixture", "light.fixture"]}),
    ("no_target", None),
    ("empty_target", {}),
    ("empty_entities", {"entity_id": []}),
    ("extra_target_key", {"entity_id": "switch.fixture", "area_id": "fixture_area"}),
    ("malformed_id", {"entity_id": "switch.fixture, switch.other"}),
    ("nonstring_entity_member", {"entity_id": ["switch.fixture", True]}),
]


async def test_registered_reversible_target_risk_cohort(mock_mcp, monkeypatch):
    for case_id, target in _TARGET_RISK_CASES:
        args = {"domain": "switch", "service": "turn_off", "target": deepcopy(target), "data": None}
        case = await _registered_actuation(mock_mcp, monkeypatch, args)
        _assert_parked(case, args, await case.tool(**args), case_id)


_DATA_RISK_CASES = [
    ("hidden_entity_id", "light", "turn_off", None, {"entity_id": "all"}),
    ("hidden_area_id", "light", "turn_off", None, {"area_id": "fixture_area"}),
    ("hidden_device_id", "light", "turn_off", None, {"device_id": "fixture_device"}),
    ("hidden_label_id", "light", "turn_off", None, {"label_id": "fixture_label"}),
    ("hidden_target", "light", "turn_off", None, {"target": {"area_id": "fixture_area"}}),
    ("unknown_key", "light", "turn_on", {"entity_id": "light.fixture"}, {"unlisted_setting": 1}),
    ("brightness_bool", "light", "turn_on", {"entity_id": "light.fixture"}, {"brightness": True}),
    (
        "brightness_string",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"brightness": "100"},
    ),
    ("brightness_negative", "light", "turn_on", {"entity_id": "light.fixture"}, {"brightness": -1}),
    ("brightness_256", "light", "turn_on", {"entity_id": "light.fixture"}, {"brightness": 256}),
    (
        "brightness_pct_101",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"brightness_pct": 101},
    ),
    (
        "rgb_wrong_arity",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"rgb_color": [0, 255]},
    ),
    ("rgb_bool", "light", "turn_on", {"entity_id": "light.fixture"}, {"rgb_color": [0, True, 255]}),
    ("rgb_256", "light", "turn_on", {"entity_id": "light.fixture"}, {"rgb_color": [0, 255, 256]}),
    ("kelvin_999", "light", "turn_on", {"entity_id": "light.fixture"}, {"color_temp_kelvin": 999}),
    ("transition_negative", "light", "turn_on", {"entity_id": "light.fixture"}, {"transition": -1}),
    ("transition_61", "light", "turn_on", {"entity_id": "light.fixture"}, {"transition": 61}),
    (
        "dual_brightness",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"brightness": 100, "brightness_pct": 50},
    ),
    (
        "dual_color",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"rgb_color": [0, 255, 0], "color_temp_kelvin": 4000},
    ),
    ("fan_percentage", "fan", "turn_on", {"entity_id": "fan.fixture"}, {"percentage": 20}),
    (
        "humidifier_humidity",
        "humidifier",
        "turn_on",
        {"entity_id": "humidifier.fixture"},
        {"humidity": 50},
    ),
]


async def test_registered_reversible_data_risk_cohort(mock_mcp, monkeypatch):
    for case_id, domain, service, target, data in _DATA_RISK_CASES:
        args = deepcopy({"domain": domain, "service": service, "target": target, "data": data})
        case = await _registered_actuation(mock_mcp, monkeypatch, args)
        _assert_parked(case, args, await case.tool(**args), case_id)


_ALLOWED_CONTROL_CASES = [
    ("light_on_no_data", "light", "turn_on", {"entity_id": "light.fixture"}, None),
    ("brightness_255", "light", "turn_on", {"entity_id": "light.fixture"}, {"brightness": 255}),
    (
        "brightness_pct_100",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"brightness_pct": 100},
    ),
    (
        "rgb_boundary",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"rgb_color": [0, 255, 0]},
    ),
    (
        "kelvin_1000",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"color_temp_kelvin": 1000},
    ),
    (
        "kelvin_10000",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"color_temp_kelvin": 10000},
    ),
    (
        "light_off_transition_0",
        "light",
        "turn_off",
        {"entity_id": "light.fixture"},
        {"transition": 0},
    ),
    (
        "light_off_transition_60",
        "light",
        "turn_off",
        {"entity_id": "light.fixture"},
        {"transition": 60},
    ),
    ("switch_on", "switch", "turn_on", {"entity_id": "switch.fixture"}, None),
    ("switch_off", "switch", "turn_off", {"entity_id": "switch.fixture"}, None),
    ("fan_on", "fan", "turn_on", {"entity_id": "fan.fixture"}, None),
    ("fan_off", "fan", "turn_off", {"entity_id": "fan.fixture"}, None),
    ("humidifier_on", "humidifier", "turn_on", {"entity_id": "humidifier.fixture"}, None),
    ("humidifier_off", "humidifier", "turn_off", {"entity_id": "humidifier.fixture"}, None),
    (
        "explicit_entity_list",
        "light",
        "turn_on",
        {"entity_id": ["light.fixture", "light.other", "light.fixture"]},
        None,
    ),
    (
        "original_brightness_pct_80",
        "light",
        "turn_on",
        {"entity_id": "light.fixture"},
        {"brightness_pct": 80},
    ),
]


async def test_registered_explicit_allowed_control_cohort(mock_mcp, monkeypatch):
    for case_id, domain, service, target, data in _ALLOWED_CONTROL_CASES:
        args = deepcopy({"domain": domain, "service": service, "target": target, "data": data})
        case = await _registered_actuation(mock_mcp, monkeypatch, args)
        _assert_automatic(case, args, await case.tool(**args), case_id)


_CLIMATE_RISK_CASES = [
    ("C_16", "climate.fixture", {"temperature": 16}, "°C", False),
    ("C_28", "climate.fixture", {"temperature": 28}, "°C", False),
    ("C_22", "climate.fixture", {"temperature": 22}, "°C", False),
    ("F_60_8", "climate.fixture", {"temperature": 60.8}, "°F", False),
    ("F_82_4", "climate.fixture", {"temperature": 82.4}, "°F", False),
    ("F_72", "climate.fixture", {"temperature": 72}, "°F", False),
    ("C_15_9", "climate.fixture", {"temperature": 15.9}, "°C", True),
    ("C_28_1", "climate.fixture", {"temperature": 28.1}, "°C", True),
    ("F_100", "climate.fixture", {"temperature": 100}, "°F", True),
    ("missing_unit", "climate.fixture", {"temperature": 22}, None, True),
    ("unknown_unit", "climate.fixture", {"temperature": 22}, "kelvin", True),
    ("mixed_units", ["climate.fixture", "climate.other"], {"temperature": 22}, ["°C", "°F"], True),
    ("bool_temperature", "climate.fixture", {"temperature": True}, "°C", True),
    ("string_temperature", "climate.fixture", {"temperature": "22"}, "°C", True),
    ("missing_temperature", "climate.fixture", {}, "°C", True),
    ("target_temp_low", "climate.fixture", {"temperature": 22, "target_temp_low": 22}, "°C", True),
    (
        "target_temp_high",
        "climate.fixture",
        {"temperature": 22, "target_temp_high": 22},
        "°C",
        True,
    ),
    ("hvac_mode", "climate.fixture", {"temperature": 22, "hvac_mode": "heat"}, "°C", True),
]


async def test_registered_climate_risk_cohort(mock_mcp, monkeypatch):
    for case_id, entity_ids, data, unit, parks in _CLIMATE_RISK_CASES:
        args = deepcopy(
            {
                "domain": "climate",
                "service": "set_temperature",
                "target": {"entity_id": entity_ids},
                "data": data,
            }
        )
        entities = [entity_ids] if isinstance(entity_ids, str) else entity_ids
        units = unit if isinstance(unit, list) else [unit] * len(entities)
        case = await _registered_actuation(
            mock_mcp, monkeypatch, args, units=dict(zip(entities, units, strict=True))
        )
        result = await case.tool(**args)
        if parks:
            _assert_parked(case, args, result, case_id)
        else:
            _assert_automatic(case, args, result, case_id)


async def test_registered_non_json_command_refusal_cohort(mock_mcp, monkeypatch, caplog):
    cycle = {}
    cycle["cycle"] = cycle
    cases = [
        ("nan", {"temperature": float("nan")}, {"entity_id": "light.fixture"}),
        ("positive_infinity", {"temperature": float("inf")}, {"entity_id": "light.fixture"}),
        ("negative_infinity", {"temperature": float("-inf")}, {"entity_id": "light.fixture"}),
        ("nonobject_target", None, []),
        ("nonobject_data", [], {"entity_id": "light.fixture"}),
        ("nonstring_object_key", {1: 2}, {"entity_id": "light.fixture"}),
        ("cyclic_value", cycle, {"entity_id": "light.fixture"}),
        ("custom_value", {"value": object()}, {"entity_id": "light.fixture"}),
        (
            "nul_string_value",
            {"unlisted_setting": "private\x00value"},
            {"entity_id": "light.fixture"},
        ),
        (
            "high_surrogate_value",
            {"nested": ["private\ud800value"]},
            {"entity_id": "light.fixture"},
        ),
        (
            "low_surrogate_value",
            {"nested": {"value": "private\udfff"}},
            {"entity_id": "light.fixture"},
        ),
        ("nul_object_key", {"private\x00key": 1}, {"entity_id": "light.fixture"}),
        ("high_surrogate_key", {"nested": {"private\ud800key": 1}}, {"entity_id": "light.fixture"}),
        (
            "low_surrogate_key",
            {"nested": [{"private\udfffkey": 1}]},
            {"entity_id": "light.fixture"},
        ),
    ]
    for case_id, data, target in cases:
        caplog.clear()
        args = {"domain": "light", "service": "turn_on", "target": target, "data": data}
        case = await _registered_actuation(mock_mcp, monkeypatch, args)
        with pytest.raises(ValueError) as exc:
            await case.tool(**args)
        assert "Home Assistant" in str(exc.value), case_id
        assert len(str(exc.value)) < 100, case_id
        case.park.assert_not_awaited()
        case.pool.execute.assert_not_awaited()
        case.client.get.assert_not_awaited()
        case.client.post.assert_not_awaited()
        case.audit.assert_not_awaited()
        case.event.assert_not_awaited()
        assert "private" not in caplog.text, case_id
        if case_id in {
            "nul_string_value",
            "high_surrogate_value",
            "low_surrogate_value",
            "nul_object_key",
            "high_surrogate_key",
            "low_surrogate_key",
        }:
            assert (
                str(exc.value) == "Home Assistant argument strings must be representable in JSONB"
            )

    # Real Unicode scalar values and ordinary control characters remain exact.
    args = {
        "domain": "light",
        "service": "turn_on",
        "target": {"entity_id": "light.fixture"},
        "data": {"unicode_☀": ["synthetic-😀\n\t"]},
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args)
    _assert_parked(case, args, await case.tool(**args), "representable_unicode_control")


async def test_argument_snapshot_and_diagnostic_privacy(mock_mcp, monkeypatch, caplog):
    args = {
        "domain": "light",
        "service": "turn_on",
        "target": {"entity_id": "light.fixture"},
        "data": {"brightness_pct": 80},
    }
    original = deepcopy(args)
    case = await _registered_actuation(mock_mcp, monkeypatch, args)
    entered, release = asyncio.Event(), asyncio.Event()

    async def write_receipt(*values):
        if "INSERT INTO ha_command_log" in values[0]:
            entered.set()
            await release.wait()
        return "UPDATE 1"

    case.pool.execute.side_effect = write_receipt
    call = asyncio.create_task(case.tool(**args))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        args["target"]["entity_id"] = "all"
        args["data"]["brightness_pct"] = 101
    finally:
        release.set()
    result = await asyncio.wait_for(call, 1)
    _assert_automatic(case, original, result, "mutable_nested_args_and_private_marker")

    private_value = "synthetic-private-value"
    private_key = "synthetic-private-key\n"
    args = {
        "domain": "light",
        "service": "turn_on",
        "target": {"entity_id": "light.private_fixture"},
        "data": {private_key: private_value, **{f"unlisted_{i}": private_value for i in range(10)}},
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args)
    result = await case.tool(**args)
    _assert_parked(case, args, result, "private_marker")
    why = case.park.await_args.kwargs["why"]
    assert "unlisted_data_key" in why
    assert "omitted data keys: 3" in why
    diagnostics = why + repr(case.audit.await_args) + caplog.text
    assert private_value not in diagnostics
    assert private_key not in diagnostics
    assert "light.private_fixture" not in diagnostics
    case.event.assert_not_awaited()


def _authorize_actuation(args, *, tool="ha_call_service"):
    task = asyncio.current_task()
    assert task is not None
    return set_approval_execution_context(
        ApprovalExecutionContext(
            action_id=uuid.uuid4(),
            session_id=None,
            actor="human:owner",
            tool_name=tool,
            tool_args_digest=approval_tool_args_digest(args),
            authorized_task=task,
        )
    )


async def test_an_approved_departure_fix_whose_premise_ended_dispatches_nothing(
    mock_mcp, monkeypatch
):
    key = "home:departure-fix:11111111-1111-1111-1111-111111111111:light.fixture"
    args = {
        "domain": "light",
        "service": "turn_off",
        "target": {"entity_id": "light.fixture"},
        "data": None,
    }
    for premise, expected in [("owner_returned", "premise_owner_returned"), ("unknown", None)]:
        case = await _registered_actuation(mock_mcp, monkeypatch, args)
        case.pool.fetchval = AsyncMock(return_value=key)
        monkeypatch.setattr(
            "butlers.jobs.home_departure_fixes.away_premise", AsyncMock(return_value=premise)
        )
        token = _authorize_actuation(args)
        try:
            outcome = await case.tool(**args)
        finally:
            reset_approval_execution_context(token)

        assert outcome["status"] == "not_run"
        assert outcome["reason"] == (expected or "premise_unknown")
        assert "undone" not in outcome["error"] and "reverted" not in outcome["error"]
        case.client.post.assert_not_awaited()
        case.pool.execute.assert_not_awaited()  # no receipt row was started
        case.park.assert_not_awaited()


async def test_the_fix_gate_runs_after_argument_binding_and_before_any_receipt(
    mock_mcp, monkeypatch
):
    key = "home:departure-fix:11111111-1111-1111-1111-111111111111:lock.fixture"
    bound = {
        "domain": "lock",
        "service": "lock",
        "target": {"entity_id": "lock.fixture"},
        "data": None,
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, bound)
    case.pool.fetchval = AsyncMock(return_value=key)
    gate = AsyncMock(return_value="premise_owner_returned")
    monkeypatch.setattr("butlers.jobs.home_departure_fixes.replay_refusal", gate)

    # An authorization bound to different arguments is no approval context at all: the call
    # parks like any unapproved one and the fix gate is never consulted.
    token = _authorize_actuation({**bound, "service": "unlock"})
    try:
        parked = await case.tool(**bound)
    finally:
        reset_approval_execution_context(token)
    assert parked["status"] == "pending_approval"
    gate.assert_not_awaited()

    # With the exact binding the gate runs and its refusal precedes every receipt write.
    token = _authorize_actuation(bound)
    try:
        refused = await case.tool(**bound)
    finally:
        reset_approval_execution_context(token)
    gate.assert_awaited_once()
    assert refused["status"] == "not_run"
    case.pool.execute.assert_not_awaited()
    case.pool.fetchrow.assert_not_awaited()  # not even the prior-receipt lookup
    case.client.post.assert_not_awaited()


async def test_an_approved_departure_fix_whose_premise_holds_dispatches_as_approved(
    mock_mcp, monkeypatch
):
    key = "home:departure-fix:11111111-1111-1111-1111-111111111111:light.fixture"
    args = {
        "domain": "light",
        "service": "turn_off",
        "target": {"entity_id": "light.fixture"},
        "data": None,
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args)
    case.pool.fetchval = AsyncMock(return_value=key)
    monkeypatch.setattr(
        "butlers.jobs.home_departure_fixes.away_premise", AsyncMock(return_value="holds")
    )
    token = _authorize_actuation(args)
    try:
        outcome = await case.tool(**args)
    finally:
        reset_approval_execution_context(token)

    assert outcome["status"] != "not_run"
    case.client.post.assert_awaited_once()


async def test_approved_dual_target_sources_are_not_silently_merged(mock_mcp, monkeypatch):
    for case_id, target in [
        ("exact_approved_ambiguous_sources", {"entity_id": "light.fixture"}),
        ("exact_approved_data_only_selector", None),
    ]:
        args = {
            "domain": "light",
            "service": "turn_off",
            "target": target,
            "data": {"entity_id": "all"},
        }
        case = await _registered_actuation(mock_mcp, monkeypatch, args)
        token = _authorize_actuation(args)
        try:
            if target is not None:
                with pytest.raises(ValueError, match="ambiguous target sources"):
                    await case.tool(**args)
                case.client.post.assert_not_awaited()
                case.pool.execute.assert_not_awaited()
            else:
                outcome = await case.tool(**args)
                assert outcome["status"] == "unverified", case_id
                assert outcome["risk"] == "consequential", case_id
                case.client.post.assert_awaited_once_with(
                    "/api/services/light/turn_off", json={"entity_id": "all"}
                )
                case.pool.fetchrow.return_value = {
                    "attempt_id": uuid.UUID(outcome["attempt_id"]),
                    "status": "unverified",
                    "failure_reason": "no explicit entity target",
                }
                replay = await case.tool(**args)
                assert replay["receipt_status"] == "unverified", case_id
                assert case.client.post.await_count == 1, case_id
                assert case.pool.execute.await_count == 2, case_id
            case.client.get.assert_not_awaited()
            case.park.assert_not_awaited()
        finally:
            reset_approval_execution_context(token)


async def test_promoted_gate_failure_never_reaches_ha(mock_mcp, monkeypatch):
    for case_id in [
        "receipt_pool_missing",
        "parking_hook_unavailable",
        "park_admission_raises",
        "post_park_audit_raises",
    ]:
        args = {
            "domain": "switch",
            "service": "turn_off",
            "target": {"area_id": "fixture_area"},
            "data": None,
        }
        case = await _registered_actuation(mock_mcp, monkeypatch, args)
        if case_id == "receipt_pool_missing":
            case.module._db = None
        elif case_id == "parking_hook_unavailable":
            monkeypatch.setattr(
                "butlers.core.approvals_hooks.is_approval_parking_available", lambda pool: False
            )
        elif case_id == "park_admission_raises":
            case.park.side_effect = RuntimeError("synthetic park admission failure")
        else:
            case.audit.side_effect = RuntimeError("synthetic post-park audit failure")
        with pytest.raises(RuntimeError):
            await case.tool(**args)
        case.client.get.assert_not_awaited()
        case.client.post.assert_not_awaited()
        case.pool.execute.assert_not_awaited()
        expected_parks = int(case_id in {"park_admission_raises", "post_park_audit_raises"})
        assert case.park.await_count == expected_parks, case_id
        if case_id == "post_park_audit_raises":
            # The completed park is not rolled back by a separate audit failure.
            assert case.park.await_args.kwargs["tool_args"] == args
            assert case.audit.await_count == 1


async def test_promoted_approval_context_is_exact(mock_mcp, monkeypatch):
    original = {
        "domain": "switch",
        "service": "turn_off",
        "target": {"area_id": "fixture_area"},
        "data": None,
    }
    for case_id in [
        "wrong_tool",
        "wrong_domain",
        "wrong_service",
        "wrong_target",
        "wrong_data",
        "inherited_child",
        "none_empty_mismatch",
    ]:
        args = deepcopy(original)
        if case_id == "wrong_domain":
            args["domain"] = "light"
        elif case_id == "wrong_service":
            args["service"] = "turn_on"
        elif case_id == "wrong_target":
            args["target"] = {"area_id": "other_fixture_area"}
        elif case_id == "wrong_data":
            args["data"] = {"unlisted_setting": 1}
        elif case_id == "none_empty_mismatch":
            args["data"] = {}
        case = await _registered_actuation(mock_mcp, monkeypatch, args)
        token = _authorize_actuation(
            original, tool="ha_activate_scene" if case_id == "wrong_tool" else "ha_call_service"
        )
        try:
            if case_id == "inherited_child":
                result = await asyncio.create_task(case.tool(**args))
            else:
                result = await case.tool(**args)
        finally:
            reset_approval_execution_context(token)
        _assert_parked(case, args, result, case_id)


@pytest.mark.parametrize(
    "domain,service,target",
    [
        ("notify", "mobile_app_guest", None),
        ("tts", "speak", {"entity_id": "media_player.shared_speaker"}),
    ],
)
async def test_message_audience_is_classified_independently_of_physical_risk(
    mock_mcp, monkeypatch, domain, service, target
):
    args = {
        "domain": domain,
        "service": service,
        "target": target,
        "data": {"message": "Synthetic announcement"},
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args)
    resolver = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "butlers.identity.resolve_channel_contact_with_owner_corroboration", resolver
    )
    outcome = await case.tool(**args)
    assert outcome["status"] == "pending_approval"
    assert outcome["risk"] == "protected"  # The physical boundary is conserved.
    resolver.assert_awaited()
    assert outcome["communication"] == {"effect_class": "communicate", "owner_only": False}
    assert "non-owner communication" in case.park.await_args.kwargs["why"]
    assert case.park.await_args.kwargs["tool_args"] == args
    case.client.post.assert_not_awaited()
    case.client.get.assert_not_awaited()


async def test_owner_notification_keeps_its_independent_protected_gate(mock_mcp, monkeypatch):
    args = {
        "domain": "notify",
        "service": "mobile_app_owner",
        "target": None,
        "data": {"message": "Synthetic owner message", "title": "Synthetic"},
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args)
    resolver = AsyncMock(return_value=SimpleNamespace(roles=["owner"]))
    monkeypatch.setattr(
        "butlers.identity.resolve_channel_contact_with_owner_corroboration", resolver
    )
    result = await case.tool(**args)
    resolver.assert_awaited_once_with(case.pool, "other", "home_assistant:notify:mobile_app_owner")
    assert result["communication"] == {"effect_class": "communicate", "owner_only": True}
    assert result["risk"] == "protected"
    assert result["status"] == "pending_approval"
    assert "non-owner communication" not in case.park.await_args.kwargs["why"]
    assert case.park.await_args.kwargs["tool_args"] == args
    case.client.post.assert_not_awaited()


@pytest.mark.parametrize("selector", ["target", "area_id", "label_id"])
async def test_owner_notifier_does_not_authorize_unresolved_data_targets(
    mock_mcp, monkeypatch, selector
):
    from butlers.roster_imports import roster_module

    actuation = roster_module("home", "modules/actuation")
    args = {
        "domain": "notify",
        "service": "generic_owner_notifier",
        "target": None,
        "data": {"message": "Synthetic routed message", selector: ["guest-destination"]},
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args)
    monkeypatch.setattr(
        "butlers.identity.resolve_channel_contact_with_owner_corroboration",
        AsyncMock(return_value=SimpleNamespace(roles=["owner"])),
    )
    outcome = await case.tool(**args)
    assert outcome["communication"] == {"effect_class": "communicate", "owner_only": False}
    assert actuation.communication_parties(
        "notify", "generic_owner_notifier", None, args["data"]
    ) == (True, None)
    # This corrects classification; the pre-existing physical gate already parks.
    assert outcome["risk"] == "protected"
    assert outcome["status"] == "pending_approval"
    assert "non-owner communication" in case.park.await_args.kwargs["why"]
    assert case.park.await_args.kwargs["tool_args"] == args
    case.client.post.assert_not_awaited()


async def test_ws_malformed_frame_withholds_content_and_keeps_valid_delivery(
    ha_module: HomeAssistantModule, caplog: pytest.LogCaptureFixture
) -> None:
    from types import SimpleNamespace

    import aiohttp

    secret = "SYNTHETIC-HA-PRIVATE-FRAME"
    ha_module._shutdown = False
    ha_module._ws_connected = True
    ha_module._record_ha_source_error = AsyncMock()
    ha_module._start_poll_fallback = MagicMock()
    ha_module._schedule_reconnect = MagicMock()
    ha_module._dispatch_ws_message = AsyncMock()
    ha_module._ws_connection = SimpleNamespace(
        closed=False,
        receive=AsyncMock(
            side_effect=[
                SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=secret),
                SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data='{"type":"pong"}'),
                SimpleNamespace(type=aiohttp.WSMsgType.CLOSE),
            ]
        ),
    )
    await ha_module._ws_message_loop()
    ha_module._dispatch_ws_message.assert_awaited_once_with({"type": "pong"})
    assert secret not in caplog.text
    assert any("invalid" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Critical entities: the registry gate in _call_service (bu-jv1moa.122,
# REQ-module-home-assistant-002, RFC 0028 Amendment 1)
# ---------------------------------------------------------------------------


def _light(entity_id="light.fixture", service="turn_on", data=None):
    return {
        "domain": entity_id.split(".")[0],
        "service": service,
        "target": {"entity_id": entity_id},
        "data": data,
    }


def _assert_parked_critical(case, args, result, *, risk="consequential"):
    assert result["status"] == "pending_approval"
    assert result["risk"] == risk
    case.client.post.assert_not_awaited()
    case.client.get.assert_not_awaited()
    case.pool.execute.assert_not_awaited()
    assert case.park.await_count == 1
    stored = case.park.await_args.kwargs
    assert stored["tool_args"] == args
    assert "critical_entity" in stored["why"]


async def test_a_reversible_call_on_a_critical_entity_parks_and_others_still_run(
    mock_mcp, monkeypatch
):
    critical = _light("light.freezer_lamp")
    case = await _registered_actuation(
        mock_mcp, monkeypatch, critical, registry=["light.freezer_lamp"]
    )
    result = await case.tool(**critical)
    _assert_parked_critical(case, critical, result)
    assert "freezer_lamp" not in case.park.await_args.kwargs["why"]

    ordinary = _light("light.fixture")
    case = await _registered_actuation(
        mock_mcp,
        monkeypatch,
        ordinary,
        registry=["light.freezer_lamp"],
        cache={"light.fixture": {}},
    )
    _assert_automatic(case, ordinary, await case.tool(**ordinary), "non_critical_light")


@pytest.mark.parametrize(
    "args",
    [
        {"domain": "light", "service": "turn_off", "target": {"area_id": "kitchen"}, "data": None},
        {"domain": "light", "service": "turn_off", "target": {"entity_id": "all"}, "data": None},
        {"domain": "scene", "service": "turn_on", "target": {"entity_id": "scene.x"}, "data": None},
        {"domain": "script", "service": "night", "target": None, "data": None},
        {
            "domain": "automation",
            "service": "trigger",
            "target": {"entity_id": "automation.x"},
            "data": None,
        },
        {
            "domain": "homeassistant",
            "service": "turn_off",
            "target": {"entity_id": "light.fixture"},
            "data": None,
        },
        {
            "domain": "button",
            "service": "press",
            "target": {"entity_id": "button.x"},
            "data": None,
        },
    ],
    ids=["area", "all", "scene", "script", "automation", "homeassistant", "button"],
)
async def test_broad_and_indirect_calls_park_as_critical_while_the_registry_is_non_empty(
    mock_mcp, monkeypatch, args
):
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["switch.freezer"])
    result = await case.tool(**args)
    assert result["status"] == "pending_approval"
    assert "critical_entity" in case.park.await_args.kwargs["why"]
    case.client.post.assert_not_awaited()


async def test_a_group_with_a_critical_member_parks_and_an_unrelated_group_runs(
    mock_mcp, monkeypatch
):
    cache = {
        "switch.bad_group": {"entity_id": ["switch.lamp", "switch.freezer"]},
        "switch.good_group": {"entity_id": ["switch.lamp", "switch.fan_plug"]},
        "switch.lamp": {},
        "switch.fan_plug": {},
        "switch.freezer": {},
    }
    bad = _light("switch.bad_group", "turn_off")
    case = await _registered_actuation(
        mock_mcp, monkeypatch, bad, registry=["switch.freezer"], cache=cache
    )
    _assert_parked_critical(case, bad, await case.tool(**bad))

    good = _light("switch.good_group", "turn_off")
    case = await _registered_actuation(
        mock_mcp, monkeypatch, good, registry=["switch.freezer"], cache=cache
    )
    _assert_automatic(case, good, await case.tool(**good), "unrelated_group")


async def test_an_entity_missing_from_the_cache_parks_while_the_registry_is_non_empty(
    mock_mcp, monkeypatch
):
    args = _light("light.unseen")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["switch.freezer"])
    _assert_parked_critical(case, args, await case.tool(**args))


@pytest.mark.parametrize(
    "failure", [asyncpg.UndefinedTableError("critical_entities"), TimeoutError(), OSError("down")]
)
async def test_an_unreadable_registry_parks_a_reversible_call(mock_mcp, monkeypatch, failure):
    args = _light()
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=failure)
    _assert_parked_critical(case, args, await case.tool(**args))


async def test_a_safe_read_is_not_promoted_even_when_the_registry_is_unreadable(
    mock_mcp, monkeypatch
):
    args = {
        "domain": "homeassistant",
        "service": "update_entity",
        "target": {"entity_id": "light.fixture"},
        "data": None,
    }
    for registry in (["light.fixture"], RuntimeError("down")):
        case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=registry)
        result = await case.tool(**args)
        assert result["risk"] == "safe"
        assert result["status"] != "pending_approval"
        case.park.assert_not_awaited()


async def _replay(case, args, actor):
    task = asyncio.current_task()
    context = ApprovalExecutionContext(
        action_id=uuid.uuid4(),
        session_id=None,
        actor=actor,
        tool_name="ha_call_service",
        tool_args_digest=approval_tool_args_digest(args),
        authorized_task=task,
    )
    token = set_approval_execution_context(context)
    try:
        return await case.tool(**args)
    finally:
        reset_approval_execution_context(token)


@pytest.mark.parametrize("actor", ["rule:7", "system:approval_gate", "system:capability_broker"])
async def test_no_shortcut_approves_a_critical_call(mock_mcp, monkeypatch, actor):
    args = _light("light.freezer_lamp")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    result = await _replay(case, args, actor)

    assert result["status"] == "not_run"
    assert result["reason"] == "critical_needs_owner_approval"
    case.client.post.assert_not_awaited()
    case.pool.execute.assert_not_awaited()
    case.park.assert_not_awaited()


async def test_the_owner_approval_of_the_stored_critical_call_runs_it_once(mock_mcp, monkeypatch):
    args = _light("light.freezer_lamp")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    _script_states(case, ["off", "on"])
    result = await _replay(case, args, "human:owner")

    assert result["status"] == "succeeded"
    case.client.post.assert_awaited_once()


async def test_an_ordinary_call_still_runs_on_a_rule_approval(mock_mcp, monkeypatch):
    args = _light("light.fixture")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["switch.freezer"])
    case.module._entity_cache["light.fixture"] = MagicMock(attributes={})
    result = await _replay(case, args, "rule:7")

    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Pre-call state and already_satisfied (bu-jv1moa.123, REQ-module-home-assistant-003/004)
# ---------------------------------------------------------------------------


def _state_response(state, attributes=None, entity_id=None):
    response = MagicMock()
    payload = {
        "state": state,
        "attributes": attributes or {},
        "last_updated": "2026-11-21T00:00:00Z",
    }
    if entity_id is not None:
        payload["entity_id"] = entity_id
    response.json.return_value = payload
    return response


def _script_states(case, states):
    """GET /api/states answers these in order (then repeats the last); an Exception is raised."""
    queue = list(states)

    async def get(path):
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, MagicMock) else _state_response(item)

    case.client.get = AsyncMock(side_effect=get)


def _receipt_inserts(case):
    return [
        call.args
        for call in case.pool.execute.await_args_list
        if "INSERT INTO ha_command_log" in call.args[0]
    ]


def _critical_case_args(entity="light.freezer_lamp", service="turn_off", data=None):
    return _light(entity, service, data)


@pytest.mark.parametrize(
    ("reads", "reason"),
    [
        ([RuntimeError("down")], "pre_state_unavailable"),
        ([httpx.ConnectError("down")], "pre_state_unavailable"),
        (["unavailable"], "pre_state_unavailable"),
        (["unknown"], "pre_state_unavailable"),
        ([_state_response("on", entity_id="light.other")], "pre_state_unavailable"),
    ],
    ids=["error", "connect", "unavailable", "unknown", "wrong-entity"],
)
async def test_a_failed_or_unusable_pre_read_sends_nothing_and_writes_no_receipt(
    mock_mcp, monkeypatch, reads, reason
):
    args = _critical_case_args()
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    _script_states(case, reads)
    result = await _replay(case, args, "human:owner")

    assert (result["status"], result["reason"]) == ("not_run", reason)
    assert reason in result["error"]
    case.client.post.assert_not_awaited()
    assert _receipt_inserts(case) == []


async def test_a_partial_read_set_sends_nothing(mock_mcp, monkeypatch):
    args = {
        "domain": "light",
        "service": "turn_off",
        "target": {"entity_id": ["light.freezer_lamp", "light.other"]},
        "data": None,
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    _script_states(case, ["on", RuntimeError("second read failed")])
    result = await _replay(case, args, "human:owner")

    assert result["reason"] == "pre_state_unavailable"
    case.client.post.assert_not_awaited()
    assert _receipt_inserts(case) == []


@pytest.mark.parametrize(
    "target",
    [
        {"area_id": "kitchen"},
        {"entity_id": "all"},
        None,
        {"entity_id": "light.freezer_lamp", "area_id": "kitchen"},
    ],
    ids=["area", "all", "absent", "mixed"],
)
async def test_a_target_that_is_not_one_explicit_entity_list_is_unbound(
    mock_mcp, monkeypatch, target
):
    args = {"domain": "light", "service": "turn_off", "target": target, "data": None}
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    result = await _replay(case, args, "human:owner")

    assert result["reason"] == "target_unbound"
    case.client.get.assert_not_awaited()
    case.client.post.assert_not_awaited()
    assert _receipt_inserts(case) == []


async def test_a_stale_read_sends_nothing_and_a_later_fresh_read_sends_once(mock_mcp, monkeypatch):
    args = _critical_case_args(service="turn_on")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    _script_states(case, ["off", "on"])
    monkeypatch.setattr(case.module, "_pre_state_is_fresh", lambda finished: False)
    stale = await _replay(case, args, "human:owner")
    assert stale["reason"] == "pre_state_stale"
    case.client.post.assert_not_awaited()
    assert _receipt_inserts(case) == []

    monkeypatch.setattr(case.module, "_pre_state_is_fresh", lambda finished: True)
    _script_states(case, ["off", "on"])
    fresh = await _replay(case, args, "human:owner")
    assert fresh["status"] == "succeeded"
    case.client.post.assert_awaited_once()
    assert len(_receipt_inserts(case)) == 1


def test_the_freshness_bound_is_ten_seconds():
    import time

    module = HomeAssistantModule()
    now = time.monotonic()
    assert module._pre_state_is_fresh(now - 9.5)
    assert not module._pre_state_is_fresh(now - 10.5)


async def test_the_pre_call_state_is_kept_on_the_receipt_and_sent_once(mock_mcp, monkeypatch):
    args = _critical_case_args(service="turn_on")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    _script_states(case, ["off", "on"])
    result = await _replay(case, args, "human:owner")

    assert result["status"] == "succeeded"
    (insert,) = _receipt_inserts(case)
    assert insert[-1]["light.freezer_lamp"]["state"] == "off"
    assert "pre_state" in insert[0]
    case.client.post.assert_awaited_once()


async def test_an_ordinary_approved_call_is_not_pre_read_while_the_registry_is_readable(
    mock_mcp, monkeypatch
):
    args = _light("light.fixture", "turn_on")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["switch.freezer"])
    case.module._entity_cache["light.fixture"] = MagicMock(attributes={})
    result = await _replay(case, args, "human:owner")

    assert result["status"] == "succeeded"
    assert case.client.get.await_count == 1  # the post-call read-back only
    assert _receipt_inserts(case)[0][-1] is None


async def test_an_unreadable_registry_requires_the_pre_read_for_an_ordinary_call(
    mock_mcp, monkeypatch
):
    args = _light("light.fixture", "turn_on")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=RuntimeError("down"))
    _script_states(case, [RuntimeError("down")])
    result = await _replay(case, args, "human:owner")

    assert result["reason"] == "pre_state_unavailable"
    case.client.post.assert_not_awaited()


async def test_a_satisfied_pre_state_sends_nothing_and_settles_already_satisfied(
    mock_mcp, monkeypatch
):
    args = _critical_case_args(service="turn_off")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    _script_states(case, ["off"])
    result = await _replay(case, args, "human:owner")

    assert result["status"] == "already_satisfied"
    assert result["message"] == "already that way, nothing sent"
    case.client.post.assert_not_awaited()
    (insert,) = _receipt_inserts(case)
    assert "'already_satisfied'" in insert[0] and "'attempting'" not in insert[0]
    assert "observed_state" not in insert[0]
    assert insert[-1]["light.freezer_lamp"]["state"] == "off"
    case.event.assert_awaited_once()
    assert case.event.await_args.kwargs["status"] == "already_satisfied"
    assert case.event.await_args.kwargs["attention_required"] is False


async def test_a_replay_after_already_satisfied_returns_it_with_no_request_or_attention(
    mock_mcp, monkeypatch
):
    args = _critical_case_args(service="turn_off")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    attempt = uuid.uuid4()
    case.pool.fetchrow = AsyncMock(
        return_value={
            "attempt_id": attempt,
            "status": "already_satisfied",
            "result": None,
            "observed_state": None,
            "failure_reason": None,
        }
    )
    result = await _replay(case, args, "human:owner")

    assert result["status"] == "already_satisfied"
    assert result["attempt_id"] == str(attempt)
    assert "attention_required" not in result
    case.client.get.assert_not_awaited()
    case.client.post.assert_not_awaited()


@pytest.mark.parametrize(
    ("domain", "service", "data"),
    [
        ("scene", "turn_on", None),
        ("light", "turn_on", {"brightness_pct": 40}),
    ],
)
async def test_a_call_with_no_verifier_for_its_effect_is_never_already_satisfied(
    mock_mcp, monkeypatch, domain, service, data
):
    entity = f"{domain}.freezer_item"
    args = {"domain": domain, "service": service, "target": {"entity_id": entity}, "data": data}
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=[entity])
    _script_states(case, ["on"])
    result = await _replay(case, args, "human:owner")

    assert result["status"] in {"succeeded", "unverified"}
    assert result["status"] != "already_satisfied"
    case.client.post.assert_awaited_once()


async def test_a_post_condition_that_fails_after_the_request_is_unverified(mock_mcp, monkeypatch):
    args = _critical_case_args(service="turn_on")
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    _script_states(case, ["off", "off"])
    result = await _replay(case, args, "human:owner")

    assert result["status"] == "unverified"
    assert result["attention_required"] is True
    case.client.post.assert_awaited_once()


def test_satisfied_by_pre_state_is_conservative_about_unread_data():
    actuation = ha_actuation
    off = {"light.a": {"state": "off", "attributes": {}}}
    assert actuation.satisfied_by_pre_state("light", "turn_off", None, off)
    assert not actuation.satisfied_by_pre_state("light", "turn_off", {"transition": 2}, off)
    assert not actuation.satisfied_by_pre_state("light", "turn_on", None, off)
    assert not actuation.satisfied_by_pre_state("scene", "turn_on", None, off)
    heat = {"climate.a": {"state": "heat", "attributes": {"temperature": 21}}}
    assert actuation.satisfied_by_pre_state("climate", "set_temperature", {"temperature": 21}, heat)
    assert not actuation.satisfied_by_pre_state(
        "climate", "set_temperature", {"temperature": 22}, heat
    )


async def test_a_group_target_is_never_already_satisfied_from_its_aggregate_state(
    mock_mcp, monkeypatch
):
    """A group reads "on" when any member is on, so its state proves nothing about the rest."""
    args = {
        "domain": "light",
        "service": "turn_on",
        "target": {"entity_id": "light.freezer_group"},
        "data": None,
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args, registry=["light.freezer_lamp"])
    group = _state_response("on", {"entity_id": ["light.freezer_lamp", "light.other"]})
    _script_states(case, [group])
    result = await _replay(case, args, "human:owner")

    assert result["status"] != "already_satisfied"
    case.client.post.assert_awaited_once()

async def test_a_half_on_group_is_unverified_after_turn_on_never_succeeded(mock_mcp, monkeypatch):
    """A group reads "on" when any member is on, so its state cannot prove every member is."""
    args = {
        "domain": "light",
        "service": "turn_on",
        "target": {"entity_id": "light.group"},
        "data": None,
    }
    case = await _registered_actuation(mock_mcp, monkeypatch, args)
    half_on = MagicMock()
    half_on.json.return_value = {
        "state": "on",
        "attributes": {"entity_id": ["light.a", "light.b"]},
        "last_updated": "2026-11-21T00:00:00Z",
    }
    case.client.get = AsyncMock(return_value=half_on)
    result = await case.tool(**args)

    assert result["status"] == "unverified"
    assert result["attention_required"] is True
    assert "group" in result["error"]


def test_verify_post_condition_never_succeeds_on_a_groups_aggregate_state():
    group = {"light.group": {"state": "on", "attributes": {"entity_id": ["light.a", "light.b"]}}}
    assert not ha_actuation.verify_post_condition("light", "turn_on", None, group).verified
    plain = {"light.a": {"state": "on", "attributes": {"brightness": 5}}}
    assert ha_actuation.verify_post_condition("light", "turn_on", None, plain).verified
