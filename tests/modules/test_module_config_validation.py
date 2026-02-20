"""Tests for Pydantic config validation during module loading.

Verifies that the daemon validates module config dicts against each module's
config_schema during startup, rejecting missing required fields, extra unknown
fields, and type mismatches. Also tests backward compatibility when a module
has no config_schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.daemon import ButlerDaemon, ModuleConfigError
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit
# ---------------------------------------------------------------------------
# Test module stubs
# ---------------------------------------------------------------------------


class StrictConfig(BaseModel):
    """Config schema with a required field and a defaulted field."""

    api_key: str
    timeout: int = 30


class StrictModule(Module):
    """Module with a config schema that has a required field."""

    def __init__(self) -> None:
        self._startup_config: Any = None
        self._tools_config: Any = None

    @property
    def name(self) -> str:
        return "strict_mod"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StrictConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self._tools_config = config

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        self._startup_config = config

    async def on_shutdown(self) -> None:
        pass


class AllDefaultsConfig(BaseModel):
    """Config schema where every field has a default."""

    host: str = "localhost"
    port: int = 8080


class AllDefaultsModule(Module):
    """Module whose config schema has only defaulted fields."""

    def __init__(self) -> None:
        self._startup_config: Any = None
        self._tools_config: Any = None

    @property
    def name(self) -> str:
        return "defaults_mod"

    @property
    def config_schema(self) -> type[BaseModel]:
        return AllDefaultsConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self._tools_config = config

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        self._startup_config = config

    async def on_shutdown(self) -> None:
        pass


class NoSchemaModule(Module):
    """Module that returns None for config_schema (backward compat)."""

    def __init__(self) -> None:
        self._startup_config: Any = None
        self._tools_config: Any = None

    @property
    def name(self) -> str:
        return "no_schema_mod"

    @property
    def config_schema(self) -> type[BaseModel] | None:
        return None

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self._tools_config = config

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        self._startup_config = config

    async def on_shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_butler_toml(tmp_path: Path, modules: dict[str, dict] | None = None) -> Path:
    """Write a minimal butler.toml and return the directory."""
    modules = modules or {}
    toml_lines = [
        "[butler]",
        'name = "test-butler"',
        "port = 9100",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butler_test"',
    ]
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            if isinstance(v, str):
                toml_lines.append(f'{k} = "{v}"')
            elif isinstance(v, bool):
                toml_lines.append(f"{k} = {'true' if v else 'false'}")
            else:
                toml_lines.append(f"{k} = {v}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _make_registry(*module_classes: type[Module]) -> ModuleRegistry:
    """Create a ModuleRegistry with the given module classes pre-registered."""
    registry = ModuleRegistry()
    for cls in module_classes:
        registry.register(cls)
    return registry


def _patch_infra():
    """Return a dict of patches for all infrastructure dependencies."""
    mock_pool = AsyncMock()

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butler_test"

    mock_spawner = MagicMock()

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "validate_core_credentials": patch(
            "butlers.daemon.validate_core_credentials_async",
            new_callable=AsyncMock,
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "get_adapter": patch(
            "butlers.daemon.get_adapter",
            return_value=type("MockAdapter", (), {"binary_name": "claude"}),
        ),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidConfigPasses:
    """Config that matches the schema should validate successfully."""

    async def test_valid_config_produces_pydantic_instance(self, tmp_path: Path) -> None:
        """A valid config dict should be converted to a Pydantic model instance."""
        butler_dir = _make_butler_toml(
            tmp_path, modules={"strict_mod": {"api_key": "sk-123", "timeout": 60}}
        )
        registry = _make_registry(StrictModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mod = daemon._modules[0]
        assert isinstance(mod._startup_config, StrictConfig)
        assert mod._startup_config.api_key == "sk-123"
        assert mod._startup_config.timeout == 60

    async def test_validated_instance_passed_to_register_tools(self, tmp_path: Path) -> None:
        """register_tools should receive the validated Pydantic instance."""
        butler_dir = _make_butler_toml(tmp_path, modules={"strict_mod": {"api_key": "sk-abc"}})
        registry = _make_registry(StrictModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mod = daemon._modules[0]
        assert isinstance(mod._tools_config, StrictConfig)
        assert mod._tools_config.api_key == "sk-abc"
        assert mod._tools_config.timeout == 30  # default

    async def test_defaults_applied_when_field_omitted(self, tmp_path: Path) -> None:
        """Omitting a defaulted field should use the default value."""
        butler_dir = _make_butler_toml(tmp_path, modules={"defaults_mod": {}})
        registry = _make_registry(AllDefaultsModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mod = daemon._modules[0]
        assert isinstance(mod._startup_config, AllDefaultsConfig)
        assert mod._startup_config.host == "localhost"
        assert mod._startup_config.port == 8080


class TestMissingRequiredField:
    """Missing a required field marks the module as failed (non-fatal)."""

    async def test_missing_required_field_marks_failed(self, tmp_path: Path) -> None:
        """Omitting a required field (api_key) marks module as failed."""
        butler_dir = _make_butler_toml(tmp_path, modules={"strict_mod": {"timeout": 10}})
        registry = _make_registry(StrictModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()  # Non-fatal — should not raise

        assert daemon._module_statuses["strict_mod"].status == "failed"
        assert daemon._module_statuses["strict_mod"].phase == "config"

    async def test_missing_required_field_error_mentions_field_name(self, tmp_path: Path) -> None:
        """The error message should mention the missing field name."""
        butler_dir = _make_butler_toml(tmp_path, modules={"strict_mod": {}})
        registry = _make_registry(StrictModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        assert daemon._module_statuses["strict_mod"].status == "failed"
        assert "api_key" in daemon._module_statuses["strict_mod"].error


class TestExtraFieldRejected:
    """Extra/unknown fields in the config mark the module as failed (non-fatal)."""

    async def test_extra_field_marks_failed(self, tmp_path: Path) -> None:
        """An unknown field marks the module as config-failed."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"strict_mod": {"api_key": "sk-123", "unknown_field": "bad"}},
        )
        registry = _make_registry(StrictModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        assert daemon._module_statuses["strict_mod"].status == "failed"
        assert daemon._module_statuses["strict_mod"].phase == "config"

    async def test_extra_field_error_mentions_field_name(self, tmp_path: Path) -> None:
        """The error message should mention the extra field name."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"defaults_mod": {"host": "x", "bogus": "y"}},
        )
        registry = _make_registry(AllDefaultsModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        assert daemon._module_statuses["defaults_mod"].status == "failed"
        assert "bogus" in daemon._module_statuses["defaults_mod"].error


class TestTypeMismatch:
    """Type mismatches in config values mark the module as failed (non-fatal)."""

    async def test_wrong_type_marks_failed(self, tmp_path: Path) -> None:
        """Passing a string where an int is expected marks module as config-failed."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"strict_mod": {"api_key": "sk-123", "timeout": "not_a_number"}},
        )
        registry = _make_registry(StrictModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        assert daemon._module_statuses["strict_mod"].status == "failed"
        assert daemon._module_statuses["strict_mod"].phase == "config"


class TestNoSchemaFallback:
    """Modules with no config_schema should receive the raw dict as-is."""

    async def test_no_schema_passes_raw_dict(self, tmp_path: Path) -> None:
        """When config_schema is None, the raw config dict is passed through."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"no_schema_mod": {"arbitrary": "value", "count": 42}},
        )
        registry = _make_registry(NoSchemaModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        mod = daemon._modules[0]
        assert isinstance(mod._startup_config, dict)
        assert mod._startup_config == {"arbitrary": "value", "count": 42}

    async def test_no_schema_extra_fields_allowed(self, tmp_path: Path) -> None:
        """Without a schema, any fields are accepted (no validation)."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"no_schema_mod": {"any_key": "any_value"}},
        )
        registry = _make_registry(NoSchemaModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()  # Should not raise

        mod = daemon._modules[0]
        assert isinstance(mod._tools_config, dict)
        assert mod._tools_config["any_key"] == "any_value"


class TestValidationPreventsModuleStartup:
    """Config validation failure should prevent the module's on_startup."""

    async def test_validation_failure_skips_module_startup(self, tmp_path: Path) -> None:
        """If config validation fails, the module's on_startup is not called."""
        butler_dir = _make_butler_toml(
            tmp_path,
            modules={"strict_mod": {}},  # Missing api_key
        )
        registry = _make_registry(StrictModule)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
        ):
            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        # Module should be config-failed, on_startup never called
        assert daemon._module_statuses["strict_mod"].status == "failed"
        assert daemon._module_statuses["strict_mod"].phase == "config"
        mod = daemon._modules[0]
        assert mod._startup_config is None  # on_startup was never called


class TestModuleConfigErrorIsImportable:
    """ModuleConfigError should be importable from butlers.daemon."""

    def test_import(self) -> None:
        """ModuleConfigError can be imported and is an Exception subclass."""
        assert issubclass(ModuleConfigError, Exception)
