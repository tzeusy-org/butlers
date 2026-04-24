"""Google Health module tests — behavioral contract.

Covers:
- Module ABC compliance (name, config_schema, dependencies, migration_revisions)
- GoogleHealthConfig validation (empty config, extra fields rejected)
- Tool registration (all eight tools always registered)
- Registry inclusion
- Startup: no credentials → degraded, tools still registered
- Startup: missing scopes → degraded, tools return error
- Startup: all scopes present → scopes_ok=True
- Each tool returns predicate filter with scope='health'
- Each tool returns error when scopes not granted

[bu-k5l35.3.1]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.google_health import (
    _NOT_CONNECTED_ERROR,
    GoogleHealthConfig,
    GoogleHealthModule,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Expected tools
# ---------------------------------------------------------------------------

EXPECTED_HEALTH_TOOLS = {
    "health_sleep_latest",
    "health_sleep_history",
    "health_hr_history",
    "health_hrv_history",
    "health_spo2_history",
    "health_breathing_rate_history",
    "health_activity_summary",
    "health_vo2_max_latest",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def health_module() -> GoogleHealthModule:
    return GoogleHealthModule()


@pytest.fixture
def mock_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        name = kwargs.get("name")

        def decorator(fn):
            tools[name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_module_contract(self, health_module: GoogleHealthModule) -> None:
        """GoogleHealthModule satisfies Module ABC."""
        assert issubclass(GoogleHealthModule, Module)
        assert health_module.name == "google_health"
        assert health_module.config_schema is GoogleHealthConfig
        assert issubclass(health_module.config_schema, BaseModel)
        assert health_module.dependencies == []
        assert health_module.migration_revisions() is None

    def test_default_registry_includes_google_health(self) -> None:
        from butlers.modules.registry import default_registry

        assert "google_health" in default_registry().available_modules


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestGoogleHealthConfig:
    def test_empty_config_is_valid(self) -> None:
        cfg = GoogleHealthConfig()
        assert isinstance(cfg, GoogleHealthConfig)

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GoogleHealthConfig(unknown_key="value")  # type: ignore[call-arg]

    async def test_dict_config_accepted_in_register_tools(
        self, health_module: GoogleHealthModule, mock_mcp: MagicMock
    ) -> None:
        """register_tools accepts {} dict config without raising."""
        await health_module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="health")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_registers_all_eight_tools(
        self, health_module: GoogleHealthModule, mock_mcp: MagicMock
    ) -> None:
        """All eight tools are registered regardless of credentials."""
        await health_module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="health")
        assert set(mock_mcp._registered_tools.keys()) == EXPECTED_HEALTH_TOOLS

    async def test_registers_tools_when_not_connected(
        self, health_module: GoogleHealthModule, mock_mcp: MagicMock
    ) -> None:
        """Tools are registered even when credentials are absent (degraded mode)."""
        # Module never had on_startup called — _scopes_ok is False
        await health_module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="health")
        assert len(mock_mcp._registered_tools) == len(EXPECTED_HEALTH_TOOLS)


# ---------------------------------------------------------------------------
# Startup behaviour
# ---------------------------------------------------------------------------


class TestOnStartup:
    async def test_startup_without_credential_store_is_degraded(
        self, health_module: GoogleHealthModule
    ) -> None:
        """Module starts in degraded mode when no credential_store provided."""
        await health_module.on_startup(config={}, db=MagicMock(pool=MagicMock()))
        assert health_module._scopes_ok is False

    async def test_startup_without_db_is_degraded(self, health_module: GoogleHealthModule) -> None:
        """Module starts in degraded mode when db is None."""
        await health_module.on_startup(config={}, db=None, credential_store=AsyncMock())
        assert health_module._scopes_ok is False

    async def test_startup_missing_primary_account_is_degraded(
        self, health_module: GoogleHealthModule
    ) -> None:
        """Module starts in degraded mode when no primary Google account exists."""
        from butlers.google_credentials import MissingGoogleCredentialsError

        with patch(
            "butlers.google_credentials.resolve_google_credentials",
            new_callable=AsyncMock,
            side_effect=MissingGoogleCredentialsError("no primary account"),
        ):
            await health_module.on_startup(
                config={},
                db=MagicMock(pool=MagicMock()),
                credential_store=AsyncMock(),
            )
        assert health_module._scopes_ok is False

    async def test_startup_missing_scopes_is_degraded(
        self, health_module: GoogleHealthModule
    ) -> None:
        """Module starts in degraded mode when Google Health scopes are absent."""
        from butlers.google_credentials import GoogleCredentials

        creds = MagicMock(spec=GoogleCredentials)
        creds.scope = "https://www.googleapis.com/auth/gmail.readonly"  # no Health scopes

        with (
            patch(
                "butlers.google_credentials.resolve_google_credentials",
                new_callable=AsyncMock,
                return_value=creds,
            ),
            patch(
                "butlers.google_credentials.resolve_google_account_entity",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await health_module.on_startup(
                config={},
                db=MagicMock(pool=MagicMock()),
                credential_store=AsyncMock(),
            )
        assert health_module._scopes_ok is False

    async def test_startup_all_scopes_present_sets_ok(
        self, health_module: GoogleHealthModule
    ) -> None:
        """Module is healthy when all three Google Health scopes are present."""
        from butlers.google_credentials import GoogleCredentials

        all_scopes = " ".join(
            [
                "https://www.googleapis.com/auth/googlehealth.sleep",
                "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
                "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
            ]
        )
        creds = MagicMock(spec=GoogleCredentials)
        creds.scope = all_scopes

        with (
            patch(
                "butlers.google_credentials.resolve_google_credentials",
                new_callable=AsyncMock,
                return_value=creds,
            ),
            patch(
                "butlers.google_credentials.resolve_google_account_entity",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            await health_module.on_startup(
                config={},
                db=MagicMock(pool=MagicMock()),
                credential_store=AsyncMock(),
            )
        assert health_module._scopes_ok is True


# ---------------------------------------------------------------------------
# Tool behaviour when not connected
# ---------------------------------------------------------------------------


class TestToolsNotConnected:
    """All tools return _NOT_CONNECTED_ERROR dict when _scopes_ok is False."""

    async def _register_and_call(
        self, tool_name: str, mock_mcp: MagicMock, **kwargs: Any
    ) -> dict[str, Any]:
        module = GoogleHealthModule()
        # _scopes_ok defaults to False
        await module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="health")
        fn = mock_mcp._registered_tools[tool_name]
        return await fn(**kwargs)

    async def test_sleep_latest_returns_error(self, mock_mcp: MagicMock) -> None:
        result = await self._register_and_call("health_sleep_latest", mock_mcp)
        assert result == {"error": _NOT_CONNECTED_ERROR}

    async def test_sleep_history_returns_error(self, mock_mcp: MagicMock) -> None:
        result = await self._register_and_call("health_sleep_history", mock_mcp, days=7)
        assert result == {"error": _NOT_CONNECTED_ERROR}

    async def test_hr_history_returns_error(self, mock_mcp: MagicMock) -> None:
        result = await self._register_and_call("health_hr_history", mock_mcp, days=30)
        assert result == {"error": _NOT_CONNECTED_ERROR}

    async def test_hrv_history_returns_error(self, mock_mcp: MagicMock) -> None:
        result = await self._register_and_call("health_hrv_history", mock_mcp, days=30)
        assert result == {"error": _NOT_CONNECTED_ERROR}

    async def test_spo2_history_returns_error(self, mock_mcp: MagicMock) -> None:
        result = await self._register_and_call("health_spo2_history", mock_mcp, days=30)
        assert result == {"error": _NOT_CONNECTED_ERROR}

    async def test_breathing_rate_history_returns_error(self, mock_mcp: MagicMock) -> None:
        result = await self._register_and_call("health_breathing_rate_history", mock_mcp, days=30)
        assert result == {"error": _NOT_CONNECTED_ERROR}

    async def test_activity_summary_returns_error(self, mock_mcp: MagicMock) -> None:
        result = await self._register_and_call("health_activity_summary", mock_mcp, days=7)
        assert result == {"error": _NOT_CONNECTED_ERROR}

    async def test_vo2_max_latest_returns_error(self, mock_mcp: MagicMock) -> None:
        result = await self._register_and_call("health_vo2_max_latest", mock_mcp)
        assert result == {"error": _NOT_CONNECTED_ERROR}


# ---------------------------------------------------------------------------
# Tool behaviour when connected — predicate and scope checks
# ---------------------------------------------------------------------------


def _make_connected_module() -> tuple[GoogleHealthModule, MagicMock]:
    """Return a module with _scopes_ok=True and a fresh mock_mcp."""
    module = GoogleHealthModule()
    module._scopes_ok = True

    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        name = kwargs.get("name")

        def decorator(fn):
            tools[name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return module, mcp


class TestToolPredicateFilters:
    """Verify each tool carries the correct predicate and scope='health'."""

    async def test_sleep_latest_predicate(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_sleep_latest"]()
        assert result["predicate"] == "sleep_session"
        assert result["scope"] == "health"

    async def test_sleep_history_predicate_and_days(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_sleep_history"](days=14)
        assert result["predicate"] == "sleep_session"
        assert result["scope"] == "health"
        assert result["days"] == 14
        assert "time_from" in result

    async def test_sleep_history_default_days(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_sleep_history"]()
        assert result["days"] == 7

    async def test_sleep_history_clamps_days(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_sleep_history"](days=999)
        assert result["days"] == 90

    async def test_hr_history_predicate(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_hr_history"](days=30)
        assert result["predicate"] == "resting_hr_daily"
        assert result["scope"] == "health"
        assert result["days"] == 30

    async def test_hrv_history_predicate(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_hrv_history"](days=30)
        assert result["predicate"] == "hrv_daily"
        assert result["scope"] == "health"

    async def test_spo2_history_predicate(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_spo2_history"](days=30)
        assert result["predicate"] == "spo2_daily"
        assert result["scope"] == "health"

    async def test_breathing_rate_predicate(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_breathing_rate_history"](days=30)
        assert result["predicate"] == "breathing_rate_daily"
        assert result["scope"] == "health"

    async def test_activity_summary_predicates(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_activity_summary"](days=7)
        assert "steps_daily" in result["predicates"]
        assert "active_minutes_daily" in result["predicates"]
        assert result["scope"] == "health"
        assert result["days"] == 7

    async def test_activity_summary_clamps_days(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_activity_summary"](days=200)
        assert result["days"] == 90

    async def test_vo2_max_predicate(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_vo2_max_latest"]()
        assert result["predicate"] == "vo2_max"
        assert result["scope"] == "health"


# ---------------------------------------------------------------------------
# Aggregate shape hints in instruction text
# ---------------------------------------------------------------------------


class TestAggregateInstructions:
    """Verify tools carry aggregation shape hints in their instruction text."""

    async def test_sleep_history_aggregation_hint(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_sleep_history"]()
        instruction = result["instruction"]
        assert "avg_duration_minutes" in instruction
        assert "avg_efficiency" in instruction
        assert "avg_deep_minutes" in instruction
        assert "avg_rem_minutes" in instruction

    async def test_hr_history_aggregation_hint(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_hr_history"]()
        instruction = result["instruction"]
        assert "min" in instruction
        assert "max" in instruction
        assert "avg" in instruction
        assert "slope" in instruction

    async def test_hrv_history_aggregation_hint(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_hrv_history"]()
        instruction = result["instruction"]
        assert "avg_rmssd" in instruction
        assert "coverage" in instruction
        assert "trend" in instruction

    async def test_activity_summary_aggregation_hint(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_activity_summary"]()
        instruction = result["instruction"]
        assert "avg_steps" in instruction
        assert "avg_active_minutes" in instruction
        assert "days_meeting_10k_steps" in instruction

    async def test_vo2_max_range_hint(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_vo2_max_latest"]()
        instruction = result["instruction"]
        assert "range_low" in instruction
        assert "range_high" in instruction
        assert "midpoint" in instruction

    async def test_sleep_latest_no_data_message(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        result = await mcp._registered_tools["health_sleep_latest"]()
        # The no-data message should be embedded in the instruction
        assert "No sleep data ingested yet" in result["instruction"]


# ---------------------------------------------------------------------------
# Security contract: no direct health API calls
# ---------------------------------------------------------------------------


class TestNoDirectApiCalls:
    """Verify no tool result contains health.googleapis.com."""

    async def test_no_tool_contains_googleapis_url(self) -> None:
        module, mcp = _make_connected_module()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="health")
        for name, fn in mcp._registered_tools.items():
            # Call each tool with default args
            if name in ("health_sleep_latest", "health_vo2_max_latest"):
                result = await fn()
            else:
                result = await fn()
            result_str = str(result)
            assert "health.googleapis.com" not in result_str, (
                f"Tool {name!r} references health.googleapis.com"
            )
