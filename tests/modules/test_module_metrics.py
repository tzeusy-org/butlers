"""Tests for the MetricsModule skeleton and storage layer (butlers-lxiq.1),
instrument cache, naming, and on_startup re-registration (butlers-lxiq.2),
and metrics_define / metrics_emit MCP tools (butlers-lxiq.3).

Covers acceptance criteria (lxiq.1):
1. MetricsModule can be instantiated without error
2. migration_revisions() returns None
3. 'metrics' appears in ModuleRegistry.default_registry().available_modules
4. MetricsModuleConfig raises ValidationError when prometheus_query_url is missing
5. storage.py functions operate correctly against the state store

Covers acceptance criteria (lxiq.2):
6. _full_name('finance', 'api_calls') → 'butler_finance_api_calls'
7. Hyphens in butler schema name are replaced with underscores
8. _validate_name rejects uppercase, leading digits, spaces
9. on_startup restores all definitions from state store into instrument cache
10. on_startup with empty state store completes without error

Covers acceptance criteria (lxiq.3):
11. metrics_define creates counter/gauge/histogram correctly
12. Re-define of existing metric returns cached instrument without state store write
13. Cap at 1,000 enforced with clear error message
14. Cardinality advisory present in tool description
15. metrics_emit rejects undefined metric, negative counter, label mismatch
16. metrics_emit calls correct OTEL method for each type
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module
from butlers.modules.metrics import MetricsModule, MetricsModuleConfig
from butlers.modules.registry import default_registry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# MetricsModuleConfig validation
# ---------------------------------------------------------------------------


class TestMetricsModuleConfig:
    """Config model validation."""

    def test_valid_config(self):
        cfg = MetricsModuleConfig(prometheus_query_url="http://lgtm:9090")
        assert cfg.prometheus_query_url == "http://lgtm:9090"

    def test_missing_prometheus_query_url_raises(self):
        with pytest.raises(ValidationError):
            MetricsModuleConfig()

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            MetricsModuleConfig(prometheus_query_url="http://lgtm:9090", unknown_field="x")


# ---------------------------------------------------------------------------
# MetricsModule ABC compliance
# ---------------------------------------------------------------------------


class TestMetricsModuleABC:
    """Verify MetricsModule satisfies the Module abstract base class."""

    def test_is_subclass_of_module(self):
        assert issubclass(MetricsModule, Module)

    def test_instantiates_without_error(self):
        mod = MetricsModule()
        assert isinstance(mod, Module)

    def test_name_is_metrics(self):
        mod = MetricsModule()
        assert mod.name == "metrics"

    def test_config_schema_is_metrics_module_config(self):
        mod = MetricsModule()
        assert mod.config_schema is MetricsModuleConfig
        assert issubclass(mod.config_schema, BaseModel)

    def test_dependencies_empty(self):
        mod = MetricsModule()
        assert mod.dependencies == []

    def test_migration_revisions_returns_none(self):
        mod = MetricsModule()
        assert mod.migration_revisions() is None


# ---------------------------------------------------------------------------
# Module lifecycle
# ---------------------------------------------------------------------------


class TestMetricsModuleLifecycle:
    """on_startup and on_shutdown lifecycle behaviour."""

    def _make_fake_db_no_pool(self):
        """Build a minimal fake db with pool=None (skips cache restoration)."""
        fake_db = MagicMock()
        fake_db.pool = None
        return fake_db

    async def test_on_startup_stores_config_and_db(self):
        mod = MetricsModule()
        fake_db = self._make_fake_db_no_pool()
        cfg = MetricsModuleConfig(prometheus_query_url="http://prom:9090")
        await mod.on_startup(config=cfg, db=fake_db)
        assert mod._config is cfg
        assert mod._db is fake_db

    async def test_on_startup_coerces_dict_config(self):
        mod = MetricsModule()
        fake_db = self._make_fake_db_no_pool()
        await mod.on_startup(config={"prometheus_query_url": "http://prom:9090"}, db=fake_db)
        assert isinstance(mod._config, MetricsModuleConfig)
        assert mod._config.prometheus_query_url == "http://prom:9090"

    async def test_on_startup_accepts_none_config(self):
        """None config is accepted (module not fully configured but doesn't crash)."""
        mod = MetricsModule()
        fake_db = self._make_fake_db_no_pool()
        await mod.on_startup(config=None, db=fake_db)
        assert mod._config is None
        assert mod._db is fake_db

    async def test_on_shutdown_clears_state(self):
        mod = MetricsModule()
        fake_db = self._make_fake_db_no_pool()
        cfg = MetricsModuleConfig(prometheus_query_url="http://prom:9090")
        await mod.on_startup(config=cfg, db=fake_db)
        await mod.on_shutdown()
        assert mod._config is None
        assert mod._db is None

    async def test_register_tools_stores_config_and_db(self):
        mod = MetricsModule()
        fake_mcp = MagicMock()
        fake_db = MagicMock()
        cfg = MetricsModuleConfig(prometheus_query_url="http://prom:9090")
        await mod.register_tools(mcp=fake_mcp, config=cfg, db=fake_db)
        assert mod._config is cfg
        assert mod._db is fake_db

    async def test_on_startup_raises_on_unsupported_config_type(self):
        """_coerce_config raises TypeError for unexpected config types."""
        mod = MetricsModule()
        fake_db = self._make_fake_db_no_pool()
        with pytest.raises(TypeError, match="Unsupported config type for MetricsModule"):
            await mod.on_startup(config=42, db=fake_db)


# ---------------------------------------------------------------------------
# ModuleRegistry auto-discovery
# ---------------------------------------------------------------------------


class TestMetricsModuleRegistryDiscovery:
    """Verify 'metrics' is auto-discovered by the default registry."""

    def test_metrics_in_available_modules(self):
        registry = default_registry()
        assert "metrics" in registry.available_modules


# ---------------------------------------------------------------------------
# Storage layer — unit tests with a mock pool
# ---------------------------------------------------------------------------


class TestStorageMockPool:
    """Unit-test storage functions with a mock asyncpg pool.

    These tests verify the logic without a real database by patching the
    state store helpers.
    """

    async def test_save_definition_calls_state_set(self, monkeypatch):
        from butlers.modules.metrics import storage

        mock_state_set = AsyncMock()
        monkeypatch.setattr(storage, "state_set", mock_state_set)

        mock_pool = MagicMock()
        defn = {"name": "req_count", "type": "counter", "help": "Total requests"}
        await storage.save_definition(mock_pool, "req_count", defn)

        mock_state_set.assert_awaited_once_with(mock_pool, "metrics_catalogue:req_count", defn)

    async def test_load_all_definitions_returns_values(self, monkeypatch):
        from butlers.modules.metrics import storage

        defn1 = {"name": "req_count", "type": "counter"}
        defn2 = {"name": "active_sessions", "type": "gauge"}
        mock_state_list = AsyncMock(
            return_value=[
                {"key": "metrics_catalogue:req_count", "value": defn1},
                {"key": "metrics_catalogue:active_sessions", "value": defn2},
            ]
        )
        monkeypatch.setattr(storage, "state_list", mock_state_list)

        mock_pool = MagicMock()
        result = await storage.load_all_definitions(mock_pool)

        mock_state_list.assert_awaited_once_with(
            mock_pool, prefix="metrics_catalogue:", keys_only=False
        )
        assert result == [defn1, defn2]

    async def test_load_all_definitions_empty_state_store(self, monkeypatch):
        from butlers.modules.metrics import storage

        mock_state_list = AsyncMock(return_value=[])
        monkeypatch.setattr(storage, "state_list", mock_state_list)

        mock_pool = MagicMock()
        result = await storage.load_all_definitions(mock_pool)
        assert result == []

    async def test_count_definitions_counts_keys(self, monkeypatch):
        from butlers.modules.metrics import storage

        mock_state_list = AsyncMock(
            return_value=["metrics_catalogue:a", "metrics_catalogue:b", "metrics_catalogue:c"]
        )
        monkeypatch.setattr(storage, "state_list", mock_state_list)

        mock_pool = MagicMock()
        count = await storage.count_definitions(mock_pool)

        mock_state_list.assert_awaited_once_with(
            mock_pool, prefix="metrics_catalogue:", keys_only=True
        )
        assert count == 3

    async def test_count_definitions_empty_returns_zero(self, monkeypatch):
        from butlers.modules.metrics import storage

        mock_state_list = AsyncMock(return_value=[])
        monkeypatch.setattr(storage, "state_list", mock_state_list)

        mock_pool = MagicMock()
        count = await storage.count_definitions(mock_pool)
        assert count == 0


# ---------------------------------------------------------------------------
# Storage layer — integration tests with a real state store
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# butlers-lxiq.2: naming helpers
# ---------------------------------------------------------------------------


class TestMetricsModuleNaming:
    """Tests for _full_name and _validate_name (butlers-lxiq.2)."""

    # _full_name ---------------------------------------------------------------

    def test_full_name_basic(self):
        assert MetricsModule._full_name("finance", "api_calls") == "butler_finance_api_calls"

    def test_full_name_hyphens_replaced(self):
        """Hyphens in butler schema name must be replaced with underscores."""
        assert MetricsModule._full_name("my-butler", "req_count") == "butler_my_butler_req_count"

    def test_full_name_multiple_hyphens(self):
        assert MetricsModule._full_name("a-b-c", "x") == "butler_a_b_c_x"

    def test_full_name_no_hyphens(self):
        assert MetricsModule._full_name("switchboard", "sessions") == "butler_switchboard_sessions"

    def test_full_name_preserves_underscores_in_metric_name(self):
        assert MetricsModule._full_name("fin", "my_metric_count") == "butler_fin_my_metric_count"

    # _validate_name -----------------------------------------------------------

    def test_validate_name_valid_lowercase(self):
        assert MetricsModule._validate_name("api_calls") is True

    def test_validate_name_valid_starts_with_letter(self):
        assert MetricsModule._validate_name("a") is True

    def test_validate_name_valid_with_digits(self):
        assert MetricsModule._validate_name("req200") is True

    def test_validate_name_rejects_uppercase(self):
        assert MetricsModule._validate_name("ApiCalls") is False

    def test_validate_name_rejects_all_uppercase(self):
        assert MetricsModule._validate_name("API_CALLS") is False

    def test_validate_name_rejects_leading_digit(self):
        assert MetricsModule._validate_name("2fast") is False

    def test_validate_name_rejects_space(self):
        assert MetricsModule._validate_name("api calls") is False

    def test_validate_name_rejects_hyphen(self):
        assert MetricsModule._validate_name("api-calls") is False

    def test_validate_name_rejects_empty_string(self):
        assert MetricsModule._validate_name("") is False

    def test_validate_name_rejects_leading_underscore(self):
        """Leading underscore is not a letter — should be rejected."""
        assert MetricsModule._validate_name("_req_count") is False

    def test_validate_name_rejects_dot(self):
        assert MetricsModule._validate_name("req.count") is False


# ---------------------------------------------------------------------------
# butlers-lxiq.2: _build_instrument
# ---------------------------------------------------------------------------


class TestMetricsModuleBuildInstrument:
    """Tests for _build_instrument covering all three metric types."""

    def _make_mock_meter(self, monkeypatch):
        """Patch get_meter() and return (module, mock_meter)."""
        import butlers.modules.metrics as metrics_mod

        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_updown = MagicMock()
        mock_histogram = MagicMock()
        mock_meter.create_counter.return_value = mock_counter
        mock_meter.create_up_down_counter.return_value = mock_updown
        mock_meter.create_histogram.return_value = mock_histogram
        monkeypatch.setattr(metrics_mod, "get_meter", lambda: mock_meter)
        return mock_meter, mock_counter, mock_updown, mock_histogram

    def test_build_counter(self, monkeypatch):
        mock_meter, mock_counter, _, _ = self._make_mock_meter(monkeypatch)
        mod = MetricsModule()
        result = mod._build_instrument("butler_fin_reqs", "counter", "Total requests")
        mock_meter.create_counter.assert_called_once_with(
            name="butler_fin_reqs", description="Total requests"
        )
        assert result is mock_counter

    def test_build_gauge(self, monkeypatch):
        mock_meter, _, mock_updown, _ = self._make_mock_meter(monkeypatch)
        mod = MetricsModule()
        result = mod._build_instrument("butler_fin_active", "gauge", "Active sessions")
        mock_meter.create_up_down_counter.assert_called_once_with(
            name="butler_fin_active", description="Active sessions"
        )
        assert result is mock_updown

    def test_build_histogram(self, monkeypatch):
        mock_meter, _, _, mock_histogram = self._make_mock_meter(monkeypatch)
        mod = MetricsModule()
        result = mod._build_instrument("butler_fin_latency", "histogram", "Request latency")
        mock_meter.create_histogram.assert_called_once_with(
            name="butler_fin_latency", description="Request latency"
        )
        assert result is mock_histogram

    def test_build_invalid_type_raises(self, monkeypatch):
        self._make_mock_meter(monkeypatch)
        mod = MetricsModule()
        with pytest.raises(ValueError, match="Unsupported metric_type"):
            mod._build_instrument("butler_fin_x", "unknown_type", "Help text")


# ---------------------------------------------------------------------------
# butlers-lxiq.2: on_startup instrument cache restoration
# ---------------------------------------------------------------------------


class TestMetricsModuleOnStartupCache:
    """Tests for on_startup re-registration logic (butlers-lxiq.2)."""

    def _make_fake_db(self, *, schema: str = "finance", pool: Any = None):
        """Build a mock db object with .schema and .pool."""
        fake_db = MagicMock()
        fake_db.schema = schema
        fake_db.pool = pool or MagicMock()
        return fake_db

    def _patch_module(self, monkeypatch, definitions: list[dict]):
        """Patch load_all_definitions and get_meter for a clean environment."""
        import butlers.modules.metrics as metrics_mod

        mock_load = AsyncMock(return_value=definitions)
        monkeypatch.setattr(metrics_mod, "load_all_definitions", mock_load)

        mock_meter = MagicMock()
        mock_meter.create_counter.return_value = MagicMock()
        mock_meter.create_up_down_counter.return_value = MagicMock()
        mock_meter.create_histogram.return_value = MagicMock()
        monkeypatch.setattr(metrics_mod, "get_meter", lambda: mock_meter)

        return mock_load, mock_meter

    async def test_on_startup_derives_butler_name_from_schema(self, monkeypatch):
        self._patch_module(monkeypatch, [])
        mod = MetricsModule()
        fake_db = self._make_fake_db(schema="finance")
        await mod.on_startup(config=None, db=fake_db)
        assert mod._butler_name == "finance"

    async def test_on_startup_replaces_hyphens_in_schema(self, monkeypatch):
        """Hyphens in schema become underscores in _butler_name."""
        self._patch_module(monkeypatch, [])
        mod = MetricsModule()
        fake_db = self._make_fake_db(schema="my-butler")
        await mod.on_startup(config=None, db=fake_db)
        assert mod._butler_name == "my_butler"

    async def test_on_startup_stores_pool(self, monkeypatch):
        self._patch_module(monkeypatch, [])
        mod = MetricsModule()
        mock_pool = MagicMock()
        fake_db = self._make_fake_db(pool=mock_pool)
        await mod.on_startup(config=None, db=fake_db)
        assert mod._pool is mock_pool

    async def test_on_startup_empty_state_store_completes_without_error(self, monkeypatch):
        """Empty state store → no instruments in cache, no errors."""
        self._patch_module(monkeypatch, [])
        mod = MetricsModule()
        fake_db = self._make_fake_db()
        await mod.on_startup(config=None, db=fake_db)
        assert mod._instrument_cache == {}

    async def test_on_startup_restores_counter(self, monkeypatch):
        defn = {"name": "api_calls", "type": "counter", "help": "Total API calls"}
        mock_load, mock_meter = self._patch_module(monkeypatch, [defn])
        mock_counter = MagicMock()
        mock_meter.create_counter.return_value = mock_counter

        mod = MetricsModule()
        fake_db = self._make_fake_db(schema="finance")
        await mod.on_startup(config=None, db=fake_db)

        assert "api_calls" in mod._instrument_cache
        full_name, instrument = mod._instrument_cache["api_calls"]
        assert full_name == "butler_finance_api_calls"
        assert instrument is mock_counter
        mock_meter.create_counter.assert_called_once_with(
            name="butler_finance_api_calls", description="Total API calls"
        )

    async def test_on_startup_restores_gauge(self, monkeypatch):
        defn = {"name": "active_sessions", "type": "gauge", "help": "Active sessions"}
        mock_load, mock_meter = self._patch_module(monkeypatch, [defn])
        mock_updown = MagicMock()
        mock_meter.create_up_down_counter.return_value = mock_updown

        mod = MetricsModule()
        fake_db = self._make_fake_db(schema="finance")
        await mod.on_startup(config=None, db=fake_db)

        assert "active_sessions" in mod._instrument_cache
        full_name, instrument = mod._instrument_cache["active_sessions"]
        assert full_name == "butler_finance_active_sessions"
        assert instrument is mock_updown

    async def test_on_startup_restores_histogram(self, monkeypatch):
        defn = {"name": "latency_ms", "type": "histogram", "help": "Request latency"}
        mock_load, mock_meter = self._patch_module(monkeypatch, [defn])
        mock_hist = MagicMock()
        mock_meter.create_histogram.return_value = mock_hist

        mod = MetricsModule()
        fake_db = self._make_fake_db(schema="finance")
        await mod.on_startup(config=None, db=fake_db)

        assert "latency_ms" in mod._instrument_cache
        full_name, instrument = mod._instrument_cache["latency_ms"]
        assert full_name == "butler_finance_latency_ms"
        assert instrument is mock_hist

    async def test_on_startup_restores_multiple_definitions(self, monkeypatch):
        definitions = [
            {"name": "req_count", "type": "counter", "help": "Requests"},
            {"name": "active", "type": "gauge", "help": "Active"},
            {"name": "latency", "type": "histogram", "help": "Latency"},
        ]
        self._patch_module(monkeypatch, definitions)

        mod = MetricsModule()
        fake_db = self._make_fake_db(schema="fin")
        await mod.on_startup(config=None, db=fake_db)

        assert len(mod._instrument_cache) == 3
        assert "req_count" in mod._instrument_cache
        assert "active" in mod._instrument_cache
        assert "latency" in mod._instrument_cache

    async def test_on_startup_skips_invalid_name(self, monkeypatch):
        """Definitions with invalid names are skipped (no exception raised).

        Covers non-string, None, empty string, and invalid string names to
        ensure _restore_instrument_cache never raises TypeError from re.match.
        """
        definitions = [
            {"name": "2bad_name", "type": "counter", "help": "Invalid string name"},
            {"name": "good_name", "type": "counter", "help": "Should be kept"},
            {"name": 123, "type": "counter", "help": "Non-string name"},
            {"name": None, "type": "counter", "help": "None name"},
            {"name": "", "type": "counter", "help": "Empty string name"},
        ]
        self._patch_module(monkeypatch, definitions)

        mod = MetricsModule()
        fake_db = self._make_fake_db()
        await mod.on_startup(config=None, db=fake_db)

        assert "good_name" in mod._instrument_cache
        assert len(mod._instrument_cache) == 1

    async def test_on_startup_skips_unknown_metric_type(self, monkeypatch):
        """Definitions with unknown metric types are skipped gracefully."""
        definitions = [
            {"name": "my_metric", "type": "unknown_type", "help": "Should be skipped"},
        ]
        self._patch_module(monkeypatch, definitions)

        mod = MetricsModule()
        fake_db = self._make_fake_db()
        await mod.on_startup(config=None, db=fake_db)

        assert "my_metric" not in mod._instrument_cache

    async def test_on_startup_with_no_pool_skips_cache(self, monkeypatch):
        """When db.pool is None, instrument cache restoration is skipped."""
        mock_load, _ = self._patch_module(monkeypatch, [])
        mod = MetricsModule()
        fake_db = MagicMock()
        fake_db.schema = "finance"
        fake_db.pool = None
        await mod.on_startup(config=None, db=fake_db)

        assert mod._instrument_cache == {}
        mock_load.assert_not_awaited()

    async def test_on_shutdown_clears_instrument_cache(self, monkeypatch):
        """on_shutdown clears all new fields."""
        defn = {"name": "req_count", "type": "counter", "help": "Requests"}
        self._patch_module(monkeypatch, [defn])

        mod = MetricsModule()
        fake_db = self._make_fake_db(schema="finance")
        await mod.on_startup(config=None, db=fake_db)

        # Verify cache was populated
        assert len(mod._instrument_cache) == 1
        assert mod._butler_name == "finance"

        await mod.on_shutdown()

        assert mod._config is None
        assert mod._db is None
        assert mod._butler_name is None
        assert mod._pool is None
        assert mod._instrument_cache == {}

    async def test_on_startup_none_schema_sets_none_butler_name(self, monkeypatch):
        """When db.schema is None, _butler_name is None."""
        self._patch_module(monkeypatch, [])
        mod = MetricsModule()
        fake_db = MagicMock()
        fake_db.schema = None
        fake_db.pool = MagicMock()
        await mod.on_startup(config=None, db=fake_db)
        assert mod._butler_name is None


# ---------------------------------------------------------------------------
# butlers-lxiq.3: metrics_define MCP tool
# ---------------------------------------------------------------------------


class TestMetricsDefine:
    """Tests for the metrics_define MCP tool."""

    def _make_fake_db(self, *, schema: str = "finance", pool: Any = None):
        """Build a mock db object with .schema and .pool."""
        fake_db = MagicMock()
        fake_db.schema = schema
        fake_db.pool = pool or MagicMock()
        return fake_db

    def _patch_storage_and_meter(self, monkeypatch, *, current_count: int = 0):
        """Patch count_definitions, save_definition, and get_meter."""
        import butlers.modules.metrics as metrics_mod

        mock_count = AsyncMock(return_value=current_count)
        mock_save = AsyncMock()
        monkeypatch.setattr(metrics_mod, "count_definitions", mock_count)
        monkeypatch.setattr(metrics_mod, "save_definition", mock_save)

        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_updown = MagicMock()
        mock_histogram = MagicMock()
        mock_meter.create_counter.return_value = mock_counter
        mock_meter.create_up_down_counter.return_value = mock_updown
        mock_meter.create_histogram.return_value = mock_histogram
        monkeypatch.setattr(metrics_mod, "get_meter", lambda: mock_meter)

        return mock_count, mock_save, mock_meter, mock_counter, mock_updown, mock_histogram

    async def _build_module_with_tools(
        self, monkeypatch, *, schema: str = "finance", current_count: int = 0
    ):
        """Set up MetricsModule with register_tools called; return (mod, captured_tools)."""
        mock_count, mock_save, mock_meter, mock_counter, mock_updown, mock_histogram = (
            self._patch_storage_and_meter(monkeypatch, current_count=current_count)
        )
        mod = MetricsModule()
        fake_db = self._make_fake_db(schema=schema)

        # Capture registered tools in a dict by name.
        tools: dict[str, Any] = {}

        class CaptureMCP:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await mod.register_tools(mcp=CaptureMCP(), config=None, db=fake_db)
        return (
            mod,
            tools,
            mock_count,
            mock_save,
            mock_meter,
            mock_counter,
            mock_updown,
            mock_histogram,
        )

    async def test_define_counter_creates_instrument(self, monkeypatch):
        """metrics_define with type='counter' creates a counter instrument."""
        (
            mod,
            tools,
            mock_count,
            mock_save,
            mock_meter,
            mock_counter,
            _,
            _,
        ) = await self._build_module_with_tools(monkeypatch, schema="finance")

        result = await tools["metrics_define"](
            name="api_calls", metric_type="counter", help="Total API calls"
        )

        assert result["ok"] is True
        assert result["name"] == "api_calls"
        assert result["type"] == "counter"
        assert result["full_name"] == "butler_finance_api_calls"
        assert result["cached"] is False
        mock_meter.create_counter.assert_called_once_with(
            name="butler_finance_api_calls", description="Total API calls"
        )
        mock_save.assert_awaited_once()

    async def test_define_gauge_creates_updown_counter(self, monkeypatch):
        """metrics_define with type='gauge' creates an UpDownCounter instrument."""
        mod, tools, _, _, mock_meter, _, mock_updown, _ = await self._build_module_with_tools(
            monkeypatch, schema="finance"
        )

        result = await tools["metrics_define"](
            name="active_sessions", metric_type="gauge", help="Active sessions"
        )

        assert result["ok"] is True
        assert result["type"] == "gauge"
        assert result["full_name"] == "butler_finance_active_sessions"
        mock_meter.create_up_down_counter.assert_called_once_with(
            name="butler_finance_active_sessions", description="Active sessions"
        )

    async def test_define_histogram_creates_histogram(self, monkeypatch):
        """metrics_define with type='histogram' creates a histogram instrument."""
        mod, tools, _, _, mock_meter, _, _, mock_histogram = await self._build_module_with_tools(
            monkeypatch, schema="finance"
        )

        result = await tools["metrics_define"](
            name="latency_ms", metric_type="histogram", help="Request latency"
        )

        assert result["ok"] is True
        assert result["type"] == "histogram"
        assert result["full_name"] == "butler_finance_latency_ms"
        mock_meter.create_histogram.assert_called_once_with(
            name="butler_finance_latency_ms", description="Request latency"
        )

    async def test_define_populates_instrument_cache(self, monkeypatch):
        """metrics_define updates _instrument_cache after successful definition."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(
            monkeypatch, schema="finance"
        )

        assert "api_calls" not in mod._instrument_cache
        await tools["metrics_define"](
            name="api_calls", metric_type="counter", help="Total API calls"
        )
        assert "api_calls" in mod._instrument_cache
        full_name, _ = mod._instrument_cache["api_calls"]
        assert full_name == "butler_finance_api_calls"

    async def test_define_persists_to_state_store(self, monkeypatch):
        """metrics_define calls save_definition with the correct args."""
        mod, tools, _, mock_save, _, _, _, _ = await self._build_module_with_tools(
            monkeypatch, schema="finance"
        )

        await tools["metrics_define"](
            name="api_calls",
            metric_type="counter",
            help="Total API calls",
            labels=["status", "method"],
        )

        mock_save.assert_awaited_once()
        call_args = mock_save.call_args
        # save_definition(pool, name, defn)
        assert call_args[0][1] == "api_calls"
        saved_defn = call_args[0][2]
        assert saved_defn["name"] == "api_calls"
        assert saved_defn["type"] == "counter"
        assert saved_defn["help"] == "Total API calls"
        assert saved_defn["labels"] == ["status", "method"]
        assert "registered_at" in saved_defn

    async def test_define_idempotent_returns_cached(self, monkeypatch):
        """Re-defining an existing metric returns cached entry without state store write."""
        mod, tools, _, mock_save, _, mock_counter, _, _ = await self._build_module_with_tools(
            monkeypatch, schema="finance"
        )

        # First define.
        await tools["metrics_define"](
            name="api_calls", metric_type="counter", help="Total API calls"
        )
        assert mock_save.await_count == 1

        # Second define (idempotent).
        result = await tools["metrics_define"](
            name="api_calls", metric_type="counter", help="Total API calls"
        )

        assert result["ok"] is True
        assert result["cached"] is True
        # save_definition should not be called again.
        assert mock_save.await_count == 1

    async def test_define_rejects_invalid_name(self, monkeypatch):
        """metrics_define returns an error dict for invalid metric names."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)

        for bad_name in ["2bad", "ApiCalls", "api-calls", "", "_bad", "api calls"]:
            result = await tools["metrics_define"](
                name=bad_name, metric_type="counter", help="Help"
            )
            assert "error" in result, f"Expected error for name={bad_name!r}"

    async def test_define_rejects_invalid_metric_type(self, monkeypatch):
        """metrics_define returns an error dict for unknown metric_type."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)

        result = await tools["metrics_define"](name="api_calls", metric_type="summary", help="Help")
        assert "error" in result
        assert "summary" in result["error"]

    async def test_define_enforces_cap_at_1000(self, monkeypatch):
        """metrics_define returns an error when the 1,000-metric cap is reached."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(
            monkeypatch, current_count=1000
        )

        result = await tools["metrics_define"](
            name="new_metric", metric_type="counter", help="Help"
        )
        assert "error" in result
        assert "1000" in result["error"] or "cap" in result["error"].lower()

    async def test_define_no_pool_returns_error(self, monkeypatch):
        """metrics_define returns an error when pool is not available."""
        import butlers.modules.metrics as metrics_mod

        mock_count = AsyncMock(return_value=0)
        mock_save = AsyncMock()
        monkeypatch.setattr(metrics_mod, "count_definitions", mock_count)
        monkeypatch.setattr(metrics_mod, "save_definition", mock_save)
        monkeypatch.setattr(metrics_mod, "get_meter", lambda: MagicMock())

        mod = MetricsModule()
        fake_db = MagicMock()
        fake_db.schema = "finance"
        fake_db.pool = None  # No pool

        tools: dict[str, Any] = {}

        class CaptureMCP:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await mod.register_tools(mcp=CaptureMCP(), config=None, db=fake_db)

        result = await tools["metrics_define"](name="api_calls", metric_type="counter", help="Help")
        assert "error" in result
        assert "pool" in result["error"].lower()

    async def test_define_cardinality_advisory_in_docstring(self, monkeypatch):
        """The metrics_define tool description must include a cardinality advisory."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)
        docstring = tools["metrics_define"].__doc__ or ""
        assert "cardinality" in docstring.lower(), (
            "metrics_define docstring must contain a cardinality advisory"
        )

    async def test_define_default_labels_empty(self, monkeypatch):
        """When labels is omitted, the saved definition has an empty labels list."""
        mod, tools, _, mock_save, _, _, _, _ = await self._build_module_with_tools(
            monkeypatch, schema="finance"
        )

        await tools["metrics_define"](name="api_calls", metric_type="counter", help="Help")

        call_args = mock_save.call_args
        saved_defn = call_args[0][2]
        assert saved_defn["labels"] == []


# ---------------------------------------------------------------------------
# butlers-lxiq.3: metrics_emit MCP tool
# ---------------------------------------------------------------------------


class TestMetricsEmit:
    """Tests for the metrics_emit MCP tool."""

    def _make_fake_db(self, *, schema: str = "finance", pool: Any = None):
        fake_db = MagicMock()
        fake_db.schema = schema
        fake_db.pool = pool or MagicMock()
        return fake_db

    def _patch_storage_and_meter(self, monkeypatch, *, current_count: int = 0):
        import butlers.modules.metrics as metrics_mod

        mock_count = AsyncMock(return_value=current_count)
        mock_save = AsyncMock()
        monkeypatch.setattr(metrics_mod, "count_definitions", mock_count)
        monkeypatch.setattr(metrics_mod, "save_definition", mock_save)

        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_updown = MagicMock()
        mock_histogram = MagicMock()
        mock_meter.create_counter.return_value = mock_counter
        mock_meter.create_up_down_counter.return_value = mock_updown
        mock_meter.create_histogram.return_value = mock_histogram
        monkeypatch.setattr(metrics_mod, "get_meter", lambda: mock_meter)

        return mock_count, mock_save, mock_meter, mock_counter, mock_updown, mock_histogram

    async def _build_module_with_tools(
        self, monkeypatch, *, schema: str = "finance", current_count: int = 0
    ):
        mock_count, mock_save, mock_meter, mock_counter, mock_updown, mock_histogram = (
            self._patch_storage_and_meter(monkeypatch, current_count=current_count)
        )
        mod = MetricsModule()
        fake_db = self._make_fake_db(schema=schema)

        tools: dict[str, Any] = {}

        class CaptureMCP:
            def tool(self):
                def decorator(fn):
                    tools[fn.__name__] = fn
                    return fn

                return decorator

        await mod.register_tools(mcp=CaptureMCP(), config=None, db=fake_db)
        return (
            mod,
            tools,
            mock_count,
            mock_save,
            mock_meter,
            mock_counter,
            mock_updown,
            mock_histogram,
        )

    async def _define_metric(
        self, tools, *, name: str, metric_type: str, labels: list[str] | None = None
    ):
        """Helper: define a metric via the tools dict."""
        return await tools["metrics_define"](
            name=name,
            metric_type=metric_type,
            help=f"Test {metric_type}",
            labels=labels,
        )

    async def test_emit_undefined_metric_returns_error(self, monkeypatch):
        """metrics_emit rejects metrics that haven't been defined."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)

        result = await tools["metrics_emit"](name="unknown_metric", value=1.0)
        assert "error" in result
        assert "unknown_metric" in result["error"]

    async def test_emit_counter_calls_add(self, monkeypatch):
        """metrics_emit for a counter calls instrument.add(value)."""
        mod, tools, _, _, _, mock_counter, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="api_calls", metric_type="counter")

        result = await tools["metrics_emit"](name="api_calls", value=5.0)

        assert result == {"ok": True}
        mock_counter.add.assert_called_once_with(5.0, attributes=None)

    async def test_emit_gauge_calls_add(self, monkeypatch):
        """metrics_emit for a gauge (UpDownCounter) calls instrument.add(value)."""
        mod, tools, _, _, _, _, mock_updown, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="active_sessions", metric_type="gauge")

        result = await tools["metrics_emit"](name="active_sessions", value=-3.0)

        assert result == {"ok": True}
        mock_updown.add.assert_called_once_with(-3.0, attributes=None)

    async def test_emit_histogram_calls_record(self, monkeypatch):
        """metrics_emit for a histogram calls instrument.record(value)."""
        mod, tools, _, _, _, _, _, mock_histogram = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="latency_ms", metric_type="histogram")

        result = await tools["metrics_emit"](name="latency_ms", value=42.5)

        assert result == {"ok": True}
        mock_histogram.record.assert_called_once_with(42.5, attributes=None)

    async def test_emit_counter_negative_value_returns_error(self, monkeypatch):
        """metrics_emit rejects negative values for counter metrics."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="api_calls", metric_type="counter")

        result = await tools["metrics_emit"](name="api_calls", value=-1.0)
        assert "error" in result
        assert (
            "-1.0" in result["error"]
            or "negative" in result["error"].lower()
            or ">= 0" in result["error"]
        )

    async def test_emit_histogram_negative_value_returns_error(self, monkeypatch):
        """metrics_emit rejects negative values for histogram metrics."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="latency_ms", metric_type="histogram")

        result = await tools["metrics_emit"](name="latency_ms", value=-0.1)
        assert "error" in result

    async def test_emit_gauge_accepts_negative_value(self, monkeypatch):
        """metrics_emit accepts negative values for gauge (UpDownCounter) metrics."""
        mod, tools, _, _, _, _, mock_updown, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="temp", metric_type="gauge")

        result = await tools["metrics_emit"](name="temp", value=-50.0)
        assert result == {"ok": True}
        mock_updown.add.assert_called_once()

    async def test_emit_counter_zero_value_accepted(self, monkeypatch):
        """metrics_emit accepts zero value for counters."""
        mod, tools, _, _, _, mock_counter, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="api_calls", metric_type="counter")

        result = await tools["metrics_emit"](name="api_calls", value=0.0)
        assert result == {"ok": True}
        mock_counter.add.assert_called_once_with(0.0, attributes=None)

    async def test_emit_with_matching_labels(self, monkeypatch):
        """metrics_emit passes labels as attributes when they match declared labels."""
        mod, tools, _, _, _, mock_counter, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(
            tools, name="req_count", metric_type="counter", labels=["status", "method"]
        )

        result = await tools["metrics_emit"](
            name="req_count",
            value=1.0,
            labels={"status": "200", "method": "GET"},
        )
        assert result == {"ok": True}
        mock_counter.add.assert_called_once_with(1.0, attributes={"status": "200", "method": "GET"})

    async def test_emit_missing_label_returns_error(self, monkeypatch):
        """metrics_emit rejects observations with missing label keys."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(
            tools, name="req_count", metric_type="counter", labels=["status", "method"]
        )

        result = await tools["metrics_emit"](
            name="req_count",
            value=1.0,
            labels={"status": "200"},  # 'method' is missing
        )
        assert "error" in result
        assert "missing" in result["error"].lower() or "method" in result["error"]

    async def test_emit_extra_label_returns_error(self, monkeypatch):
        """metrics_emit rejects observations with extra (undeclared) label keys."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="req_count", metric_type="counter", labels=["status"])

        result = await tools["metrics_emit"](
            name="req_count",
            value=1.0,
            labels={"status": "200", "extra_key": "oops"},
        )
        assert "error" in result
        assert "extra" in result["error"].lower() or "extra_key" in result["error"]

    async def test_emit_no_labels_when_none_declared(self, monkeypatch):
        """metrics_emit succeeds with empty/null labels when metric has no declared labels."""
        mod, tools, _, _, _, mock_counter, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="api_calls", metric_type="counter", labels=[])

        result = await tools["metrics_emit"](name="api_calls", value=1.0, labels=None)
        assert result == {"ok": True}
        mock_counter.add.assert_called_once_with(1.0, attributes=None)

    async def test_emit_with_labels_when_none_declared_returns_error(self, monkeypatch):
        """metrics_emit rejects labels when metric was declared with no labels."""
        mod, tools, _, _, _, _, _, _ = await self._build_module_with_tools(monkeypatch)
        await self._define_metric(tools, name="api_calls", metric_type="counter", labels=[])

        result = await tools["metrics_emit"](
            name="api_calls",
            value=1.0,
            labels={"extra": "val"},
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Storage layer — integration tests with a real state store
# ---------------------------------------------------------------------------

_STATE_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY,
        value JSONB NOT NULL DEFAULT '{}',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        version INTEGER NOT NULL DEFAULT 1
    )
"""


@pytest.mark.integration
class TestStorageIntegration:
    """Integration tests for storage functions against a real Postgres state store.

    Requires the ``provisioned_postgres_pool`` fixture (Docker).
    """

    async def test_save_and_load_round_trip(self, provisioned_postgres_pool):
        """save_definition + load_all_definitions round-trip."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute(_STATE_TABLE_DDL)
            defn = {
                "name": "email_count",
                "type": "counter",
                "help": "Emails processed",
                "labels": ["status"],
            }
            from butlers.modules.metrics.storage import (
                load_all_definitions,
                save_definition,
            )

            await save_definition(pool, "email_count", defn)
            results = await load_all_definitions(pool)
            assert len(results) == 1
            assert results[0] == defn

    async def test_save_multiple_and_count(self, provisioned_postgres_pool):
        """count_definitions returns correct count after multiple saves."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute(_STATE_TABLE_DDL)
            from butlers.modules.metrics.storage import (
                count_definitions,
                save_definition,
            )

            assert await count_definitions(pool) == 0

            await save_definition(pool, "metric_a", {"type": "counter"})
            await save_definition(pool, "metric_b", {"type": "gauge"})
            await save_definition(pool, "metric_c", {"type": "histogram"})

            assert await count_definitions(pool) == 3

    async def test_save_overwrites_existing(self, provisioned_postgres_pool):
        """Saving a definition with the same name overwrites the old one."""
        async with provisioned_postgres_pool() as pool:
            await pool.execute(_STATE_TABLE_DDL)
            from butlers.modules.metrics.storage import (
                load_all_definitions,
                save_definition,
            )

            await save_definition(pool, "my_metric", {"type": "counter", "help": "v1"})
            await save_definition(pool, "my_metric", {"type": "counter", "help": "v2"})

            results = await load_all_definitions(pool)
            assert len(results) == 1
            assert results[0]["help"] == "v2"

    async def test_load_all_returns_empty_list_when_no_definitions(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as pool:
            await pool.execute(_STATE_TABLE_DDL)
            from butlers.modules.metrics.storage import load_all_definitions

            results = await load_all_definitions(pool)
            assert results == []

    async def test_count_returns_zero_when_no_definitions(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as pool:
            await pool.execute(_STATE_TABLE_DDL)
            from butlers.modules.metrics.storage import count_definitions

            assert await count_definitions(pool) == 0
