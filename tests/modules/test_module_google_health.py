"""Google Health module tests — behavioral contract.

Covers:
- Module ABC compliance (name, config_schema, dependencies, migration_revisions)
- GoogleHealthConfig validation (empty config, extra fields rejected)
- Tool registration (all eight tools always registered)
- Registry inclusion
- Startup: no credentials → degraded, tools still registered
- Startup: missing scopes → degraded, tools return error
- Startup: all scopes present → scopes_ok=True
- Each tool returns daemon-computed numbers from seeded facts, never an
  {'instruction': ...} recipe for the calling model to execute
- Each tool returns an explicit, honest empty result when no facts exist
- Each tool returns error when scopes not granted

[bu-k5l35.3.1] [bu-2jtfw.2]
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
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


class _FakePool:
    """Minimal asyncpg-pool double: routes fetch/fetchrow/fetchval by canned data.

    ``rows_by_predicate`` maps a predicate to the list of raw row dicts
    ``fetch``/``fetchrow`` should serve for queries mentioning that predicate
    in their SQL text — good enough for these single-predicate-per-query tools
    without needing a real Postgres connection (matches the mocked-pool
    convention already used in tests/jobs/test_health_jobs.py).
    """

    def __init__(self, rows_by_predicate: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._rows_by_predicate = rows_by_predicate or {}

    def _rows_for(self, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        # Predicate is either inlined literally in the SQL text (sleep, spo2,
        # vo2_max, activity) or passed as the first bind parameter
        # (the shared _daily_numeric_rollup helper used by hr/hrv/breathing).
        haystack = sql if not args else f"{sql} {args[0]}"
        for predicate, rows in self._rows_by_predicate.items():
            if predicate in haystack:
                return rows
        return []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        return self._rows_for(sql, args)

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        rows = self._rows_for(sql, args)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        rows = self._rows_for(sql, args)
        if not rows:
            return None
        row = rows[0]
        return row.get("valid_at")


def _make_connected_module(pool: _FakePool | None = None) -> tuple[GoogleHealthModule, MagicMock]:
    """Return a module with _scopes_ok=True and a fresh mock_mcp bound to *pool*."""
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
    mcp._db = SimpleNamespace(pool=pool or _FakePool())
    return module, mcp


async def _register(module: GoogleHealthModule, mcp: MagicMock) -> None:
    await module.register_tools(mcp=mcp, config={}, db=mcp._db, butler_name="health")


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
                "butlers.google_account_registry.get_google_account",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(granted_scopes=[]),
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
                "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
                "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
                "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
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
                "butlers.google_account_registry.get_google_account",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(granted_scopes=all_scopes.split()),
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

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_HEALTH_TOOLS))
    async def test_every_tool_returns_not_connected_error(
        self, tool_name: str, mock_mcp: MagicMock
    ) -> None:
        """Every health tool degrades to _NOT_CONNECTED_ERROR when _scopes_ok is False."""
        module = GoogleHealthModule()
        # _scopes_ok defaults to False
        await module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="health")
        result = await mock_mcp._registered_tools[tool_name]()
        assert result == {"error": _NOT_CONNECTED_ERROR}


# ---------------------------------------------------------------------------
# Tool behaviour when connected but no facts exist — honest empty results
# ---------------------------------------------------------------------------


class TestToolsEmptyData:
    """Every tool reports absence explicitly (never a fabricated zero/average)."""

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_HEALTH_TOOLS))
    async def test_every_tool_reports_found_false_on_empty_pool(self, tool_name: str) -> None:
        module, mcp = _make_connected_module(_FakePool({}))
        await _register(module, mcp)
        result = await mcp._registered_tools[tool_name]()
        assert result["found"] is False
        assert "message" in result


# ---------------------------------------------------------------------------
# Tool behaviour when connected — real computed numbers from seeded facts
# ---------------------------------------------------------------------------


class TestToolComputation:
    async def test_sleep_latest_returns_computed_numbers(self) -> None:
        now = datetime.now(tz=UTC)
        rows = {
            "sleep_session": [
                {
                    "valid_at": now,
                    "content": "Sleep: 7h 32m",
                    "metadata": {
                        "duration_ms": 27120000,
                        "efficiency": 91,
                        "stages": {"deep": 95, "light": 220, "rem": 137, "wake": 40},
                    },
                }
            ]
        }
        module, mcp = _make_connected_module(_FakePool(rows))
        await _register(module, mcp)
        result = await mcp._registered_tools["health_sleep_latest"]()
        assert result["found"] is True
        assert result["duration_minutes"] == 452.0
        assert result["efficiency"] == 91
        assert result["stages"] == {"deep": 95, "light": 220, "rem": 137, "wake": 40}

    async def test_sleep_history_computes_averages_over_seeded_sessions(self) -> None:
        now = datetime.now(tz=UTC)
        rows = {
            "sleep_session": [
                {
                    "valid_at": now - timedelta(days=1),
                    "content": "Sleep A",
                    "metadata": {
                        "duration_ms": 27_000_000,  # 450 min
                        "efficiency": 90,
                        "stages": {"deep": 90, "light": 200, "rem": 130, "wake": 30},
                    },
                },
                {
                    "valid_at": now - timedelta(days=2),
                    "content": "Sleep B",
                    "metadata": {
                        "duration_ms": 25_200_000,  # 420 min
                        "efficiency": 80,
                        "stages": {"deep": 70, "light": 200, "rem": 110, "wake": 40},
                    },
                },
            ]
        }
        module, mcp = _make_connected_module(_FakePool(rows))
        await _register(module, mcp)
        result = await mcp._registered_tools["health_sleep_history"](days=7)
        assert result["found"] is True
        assert result["avg_duration_minutes"] == 435.0
        assert result["avg_efficiency"] == 85.0
        assert result["avg_deep_minutes"] == 80.0
        assert result["avg_rem_minutes"] == 120.0
        assert "instruction" not in result

    async def test_hr_history_computes_summary_and_trend(self) -> None:
        now = datetime.now(tz=UTC)
        rows = {
            "measurement_resting_hr": [
                {
                    "day": (now - timedelta(days=2)).date(),
                    "mean_value": 60,
                    "min_value": 60,
                    "max_value": 60,
                    "n": 1,
                },
                {
                    "day": (now - timedelta(days=1)).date(),
                    "mean_value": 62,
                    "min_value": 62,
                    "max_value": 62,
                    "n": 1,
                },
            ]
        }
        module, mcp = _make_connected_module(_FakePool(rows))
        await _register(module, mcp)
        result = await mcp._registered_tools["health_hr_history"](days=30)
        assert result["found"] is True
        assert result["summary"]["min"] == 60
        assert result["summary"]["max"] == 62
        assert result["summary"]["avg"] == 61.0
        assert result["summary"]["trend_slope"] == 2.0
        assert "instruction" not in result

    async def test_vo2_max_latest_returns_computed_numbers(self) -> None:
        now = datetime.now(tz=UTC)
        rows = {
            "measurement_vo2_max": [
                {
                    "valid_at": now,
                    "metadata": {"range_low": 44.0, "range_high": 49.0, "midpoint": 46.5},
                }
            ]
        }
        module, mcp = _make_connected_module(_FakePool(rows))
        await _register(module, mcp)
        result = await mcp._registered_tools["health_vo2_max_latest"]()
        assert result["found"] is True
        assert result["value"] == 46.5
        assert result["midpoint"] == 46.5
        assert result["range_low"] == 44.0
        assert result["range_high"] == 49.0

    async def test_activity_summary_joins_steps_and_active_minutes_by_day(self) -> None:
        now = datetime.now(tz=UTC)
        day = (now - timedelta(days=1)).date()
        rows = {
            "measurement_steps": [
                {"day": day, "steps": 12000, "distance_km": 9.0, "floors": 5},
            ],
            "measurement_active_minutes": [
                {
                    "day": day,
                    "very_active": 20,
                    "fairly_active": 30,
                    "lightly_active": 90,
                    "sedentary": 700,
                },
            ],
        }
        module, mcp = _make_connected_module(_FakePool(rows))
        await _register(module, mcp)
        result = await mcp._registered_tools["health_activity_summary"](days=7)
        assert result["found"] is True
        assert result["avg_steps"] == 12000.0
        assert result["days_meeting_10k_steps"] == 1
        assert result["daily"][0]["very_active_minutes"] == 20.0


# ---------------------------------------------------------------------------
# Contract: no tool ever returns an 'instruction' key (the bug this fixes)
# ---------------------------------------------------------------------------


class TestNoInstructionContract:
    """No tool delegates arithmetic to the calling model via an instruction dict."""

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_HEALTH_TOOLS))
    async def test_not_connected_result_has_no_instruction_key(
        self, tool_name: str, mock_mcp: MagicMock
    ) -> None:
        module = GoogleHealthModule()
        await module.register_tools(mcp=mock_mcp, config={}, db=None, butler_name="health")
        result = await mock_mcp._registered_tools[tool_name]()
        assert "instruction" not in result

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_HEALTH_TOOLS))
    async def test_empty_data_result_has_no_instruction_key(self, tool_name: str) -> None:
        module, mcp = _make_connected_module(_FakePool({}))
        await _register(module, mcp)
        result = await mcp._registered_tools[tool_name]()
        assert "instruction" not in result


# ---------------------------------------------------------------------------
# Security contract: no direct health API calls
# ---------------------------------------------------------------------------


class TestNoDirectApiCalls:
    """Verify no tool result contains health.googleapis.com."""

    async def test_no_tool_contains_googleapis_url(self) -> None:
        module, mcp = _make_connected_module(_FakePool({}))
        await _register(module, mcp)
        for _name, fn in mcp._registered_tools.items():
            result = await fn()
            result_str = str(result)
            assert "health.googleapis.com" not in result_str, (
                f"Tool {_name!r} references health.googleapis.com"
            )
