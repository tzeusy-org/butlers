"""Tests for the MetricsModule skeleton and storage layer (butlers-lxiq.1).

Covers acceptance criteria:
1. MetricsModule can be instantiated without error
2. migration_revisions() returns None
3. 'metrics' appears in ModuleRegistry.default_registry().available_modules
4. MetricsModuleConfig raises ValidationError when prometheus_query_url is missing
5. storage.py functions operate correctly against the state store
"""

from __future__ import annotations

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

    async def test_on_startup_stores_config_and_db(self):
        mod = MetricsModule()
        fake_db = MagicMock()
        cfg = MetricsModuleConfig(prometheus_query_url="http://prom:9090")
        await mod.on_startup(config=cfg, db=fake_db)
        assert mod._config is cfg
        assert mod._db is fake_db

    async def test_on_startup_coerces_dict_config(self):
        mod = MetricsModule()
        fake_db = MagicMock()
        await mod.on_startup(config={"prometheus_query_url": "http://prom:9090"}, db=fake_db)
        assert isinstance(mod._config, MetricsModuleConfig)
        assert mod._config.prometheus_query_url == "http://prom:9090"

    async def test_on_startup_accepts_none_config(self):
        """None config is accepted (module not fully configured but doesn't crash)."""
        mod = MetricsModule()
        fake_db = MagicMock()
        await mod.on_startup(config=None, db=fake_db)
        assert mod._config is None
        assert mod._db is fake_db

    async def test_on_shutdown_clears_state(self):
        mod = MetricsModule()
        fake_db = MagicMock()
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


@pytest.mark.integration
class TestStorageIntegration:
    """Integration tests for storage functions against a real Postgres state store.

    Requires the ``provisioned_postgres_pool`` fixture (Docker).
    """

    async def test_save_and_load_round_trip(self, provisioned_postgres_pool):
        """save_definition + load_all_definitions round-trip."""
        async with provisioned_postgres_pool() as pool:
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
            from butlers.modules.metrics.storage import load_all_definitions

            results = await load_all_definitions(pool)
            assert results == []

    async def test_count_returns_zero_when_no_definitions(self, provisioned_postgres_pool):
        async with provisioned_postgres_pool() as pool:
            from butlers.modules.metrics.storage import count_definitions

            assert await count_definitions(pool) == 0
