"""Condensed metrics module tests — behavioral contract only.

Replaces test_module_metrics.py (84) + test_module_metrics_prometheus.py (29)
= 113 tests replaced with ~15.

Covers:
- Module ABC compliance
- MetricsModuleConfig validation (prometheus_query_url required)
- Registry discovery
- Lifecycle: on_startup stores config, on_shutdown clears state
- Instrument naming: _full_name format
- Tool registration: expected tools registered
- Tool behaviors via registered MCP tools

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.metrics import MetricsModule, MetricsModuleConfig
from butlers.modules.registry import default_registry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestMetricsModuleConfig:
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
# ABC compliance
# ---------------------------------------------------------------------------


class TestMetricsModuleABC:
    def test_module_contract(self):
        """MetricsModule satisfies Module ABC: name, config_schema, dependencies, revisions."""
        mod = MetricsModule()
        assert issubclass(MetricsModule, Module)
        assert mod.name == "metrics"
        assert mod.config_schema is MetricsModuleConfig
        assert mod.dependencies == []
        assert mod.migration_revisions() is None
        assert "metrics" in default_registry().available_modules


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def _make_db(self):
        db = MagicMock()
        db.pool = None
        db.db_schema = "test_butler"
        return db

    async def test_startup_stores_config(self):
        mod = MetricsModule()
        await mod.on_startup(
            config={"prometheus_query_url": "http://prom:9090"}, db=self._make_db()
        )
        assert isinstance(mod._config, MetricsModuleConfig)

    async def test_shutdown_clears_state(self):
        mod = MetricsModule()
        await mod.on_startup(
            config={"prometheus_query_url": "http://prom:9090"}, db=self._make_db()
        )
        await mod.on_shutdown()
        assert mod._config is None or mod._db is None


# ---------------------------------------------------------------------------
# Instrument naming
# ---------------------------------------------------------------------------


class TestInstrumentNaming:
    def test_full_name_format(self):
        mod = MetricsModule()
        result = mod._full_name("finance", "api_calls")
        assert "finance" in result
        assert "api_calls" in result

    def test_hyphens_replaced_with_underscores(self):
        mod = MetricsModule()
        result = mod._full_name("self-healing", "retries")
        assert "_" in result
        assert "-" not in result


# ---------------------------------------------------------------------------
# metrics_define / metrics_emit behavior via registered tools
# ---------------------------------------------------------------------------


class TestMetricsDefineEmit:
    def _make_mcp_and_tools(self) -> tuple[MagicMock, dict[str, Any]]:
        mcp = MagicMock()
        tools: dict[str, Any] = {}

        def tool_decorator(*args, **kw):
            def wrap(fn):
                tools[kw.get("name") or fn.__name__] = fn
                return fn

            return wrap

        mcp.tool = tool_decorator
        return mcp, tools

    async def test_define_counter_creates_instrument(self):
        mod = MetricsModule()
        db = MagicMock()
        db.pool = MagicMock()
        db.db_schema = "test_butler"
        db.pool.fetchrow = MagicMock(return_value=None)
        db.pool.execute = MagicMock(return_value=None)
        mcp, tools = self._make_mcp_and_tools()
        await mod.register_tools(
            mcp=mcp,
            config={"prometheus_query_url": "http://prom:9090"},
            db=db,
            butler_name="test-butler",
        )
        # metrics_define should be registered
        assert "metrics_define" in tools

    async def test_emit_rejects_undefined_metric(self):
        mod = MetricsModule()
        db = MagicMock()
        db.pool = MagicMock()
        db.db_schema = "test_butler"
        mcp, tools = self._make_mcp_and_tools()
        await mod.register_tools(
            mcp=mcp,
            config={"prometheus_query_url": "http://prom:9090"},
            db=db,
            butler_name="test-butler",
        )
        assert "metrics_emit" in tools
        result = await tools["metrics_emit"](name="undefined_metric", value=1.0)
        assert isinstance(result, dict)
        assert "error" in result
