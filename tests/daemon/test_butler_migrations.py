"""Tests for butler-specific migration chain support.

Verifies that the daemon applies butler-name-specific Alembic migrations
after core migrations and before module migrations, when such a chain exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.daemon import ButlerDaemon
from butlers.migrations import (
    _discover_butler_chains,
    _discover_module_chains,
    _resolve_chain_dir,
    get_all_chains,
    has_butler_chain,
)
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# has_butler_chain unit tests
# ---------------------------------------------------------------------------


class TestHasButlerChain:
    """Unit tests for the has_butler_chain helper."""

    def test_existing_chain_with_migrations(self, tmp_path: Path) -> None:
        """Returns True when directory exists and contains .py migration files."""
        chain_dir = tmp_path / "my-butler" / "migrations"
        chain_dir.mkdir(parents=True)
        (chain_dir / "__init__.py").touch()
        (chain_dir / "001_create_tables.py").write_text("# migration")

        with patch("butlers.migrations.ROSTER_DIR", tmp_path):
            assert has_butler_chain("my-butler") is True

    def test_no_chain_directory(self, tmp_path: Path) -> None:
        """Returns False when no directory exists for the butler name."""
        tmp_path.mkdir(exist_ok=True)

        with patch("butlers.migrations.ROSTER_DIR", tmp_path):
            assert has_butler_chain("nonexistent-butler") is False

    def test_empty_chain_directory(self, tmp_path: Path) -> None:
        """Returns False when directory exists but contains only __init__.py."""
        chain_dir = tmp_path / "empty-butler" / "migrations"
        chain_dir.mkdir(parents=True)
        (chain_dir / "__init__.py").touch()

        with patch("butlers.migrations.ROSTER_DIR", tmp_path):
            assert has_butler_chain("empty-butler") is False

    def test_chain_directory_with_non_py_files(self, tmp_path: Path) -> None:
        """Returns False when directory only contains non-.py files."""
        chain_dir = tmp_path / "txt-butler" / "migrations"
        chain_dir.mkdir(parents=True)
        (chain_dir / "__init__.py").touch()
        (chain_dir / "README.md").write_text("docs")

        with patch("butlers.migrations.ROSTER_DIR", tmp_path):
            assert has_butler_chain("txt-butler") is False

    def test_real_relationship_chain(self) -> None:
        """The 'relationship' chain should be detected from roster/relationship/migrations/."""
        assert has_butler_chain("relationship") is True

    def test_real_nonexistent_chain(self) -> None:
        """A made-up butler name should not have a chain."""
        assert has_butler_chain("does-not-exist-butler-xyz") is False


# ---------------------------------------------------------------------------
# Discovery and resolution unit tests
# ---------------------------------------------------------------------------


class TestDiscoverButlerChains:
    """Unit tests for _discover_butler_chains."""

    def test_discovers_butlers_with_migrations(self, tmp_path: Path) -> None:
        """Returns sorted list of butler names that have migrations."""
        for name in ["zeta", "alpha"]:
            mig_dir = tmp_path / name / "migrations"
            mig_dir.mkdir(parents=True)
            (mig_dir / "__init__.py").touch()
            (mig_dir / "001_tables.py").write_text("# migration")

        with patch("butlers.migrations.ROSTER_DIR", tmp_path):
            chains = _discover_butler_chains()

        assert chains == ["alpha", "zeta"]

    def test_skips_butlers_without_migrations_dir(self, tmp_path: Path) -> None:
        """Butler dirs without a migrations/ subfolder are skipped."""
        (tmp_path / "no-migrations").mkdir()
        mig_dir = tmp_path / "has-migrations" / "migrations"
        mig_dir.mkdir(parents=True)
        (mig_dir / "001_tables.py").write_text("# migration")

        with patch("butlers.migrations.ROSTER_DIR", tmp_path):
            chains = _discover_butler_chains()

        assert chains == ["has-migrations"]

    def test_skips_empty_migrations_dir(self, tmp_path: Path) -> None:
        """Butler dirs with an empty migrations/ folder are skipped."""
        mig_dir = tmp_path / "empty" / "migrations"
        mig_dir.mkdir(parents=True)
        (mig_dir / "__init__.py").touch()

        with patch("butlers.migrations.ROSTER_DIR", tmp_path):
            chains = _discover_butler_chains()

        assert chains == []

    def test_returns_empty_when_roster_dir_missing(self, tmp_path: Path) -> None:
        """Returns empty list when ROSTER_DIR does not exist."""
        missing = tmp_path / "nonexistent"

        with patch("butlers.migrations.ROSTER_DIR", missing):
            chains = _discover_butler_chains()

        assert chains == []

    def test_real_discovery_finds_known_butlers(self) -> None:
        """The real roster/ directory should contain the known butler chains."""
        chains = _discover_butler_chains()
        for expected in ["general", "health", "relationship", "switchboard"]:
            assert expected in chains, f"Expected {expected} in discovered chains"


class TestDiscoverModuleChains:
    """Unit tests for _discover_module_chains."""

    def test_discovers_modules_with_migrations(self, tmp_path: Path) -> None:
        """Returns sorted list of module names that have migrations."""
        for name in ["zeta", "alpha"]:
            mig_dir = tmp_path / name / "migrations"
            mig_dir.mkdir(parents=True)
            (mig_dir / "__init__.py").touch()
            (mig_dir / "001_tables.py").write_text("# migration")

        with patch("butlers.migrations.MODULES_DIR", tmp_path):
            chains = _discover_module_chains()

        assert chains == ["alpha", "zeta"]

    def test_skips_modules_without_migrations_dir(self, tmp_path: Path) -> None:
        """Module dirs without a migrations/ subfolder are skipped."""
        (tmp_path / "no-migrations").mkdir()
        mig_dir = tmp_path / "has-migrations" / "migrations"
        mig_dir.mkdir(parents=True)
        (mig_dir / "001_tables.py").write_text("# migration")

        with patch("butlers.migrations.MODULES_DIR", tmp_path):
            chains = _discover_module_chains()

        assert chains == ["has-migrations"]

    def test_skips_empty_migrations_dir(self, tmp_path: Path) -> None:
        """Module dirs with an empty migrations/ folder are skipped."""
        mig_dir = tmp_path / "empty" / "migrations"
        mig_dir.mkdir(parents=True)
        (mig_dir / "__init__.py").touch()

        with patch("butlers.migrations.MODULES_DIR", tmp_path):
            chains = _discover_module_chains()

        assert chains == []

    def test_returns_empty_when_modules_dir_missing(self, tmp_path: Path) -> None:
        """Returns empty list when MODULES_DIR does not exist."""
        missing = tmp_path / "nonexistent"

        with patch("butlers.migrations.MODULES_DIR", missing):
            chains = _discover_module_chains()

        assert chains == []

    def test_real_discovery_finds_known_modules(self) -> None:
        """The real modules/ directory should contain the known module chains."""
        chains = _discover_module_chains()
        for expected in ["approvals", "mailbox", "memory"]:
            assert expected in chains, f"Expected {expected} in discovered module chains"


class TestResolveChainDir:
    """Unit tests for _resolve_chain_dir."""

    def test_shared_chain_resolves_to_alembic_versions(self, tmp_path: Path) -> None:
        """Core chain resolves to alembic/versions/core/."""
        core_dir = tmp_path / "versions" / "core"
        core_dir.mkdir(parents=True)

        with (
            patch("butlers.migrations.ALEMBIC_DIR", tmp_path),
            patch("butlers.migrations.MODULES_DIR", tmp_path / "empty_modules"),
            patch("butlers.migrations.ROSTER_DIR", tmp_path / "empty_roster"),
        ):
            result = _resolve_chain_dir("core")

        assert result == core_dir

    def test_module_chain_resolves_to_module_dir(self, tmp_path: Path) -> None:
        """Module chains resolve to src/butlers/modules/<name>/migrations/."""
        mig_dir = tmp_path / "mailbox" / "migrations"
        mig_dir.mkdir(parents=True)

        with (
            patch("butlers.migrations.ALEMBIC_DIR", tmp_path / "empty_alembic"),
            patch("butlers.migrations.MODULES_DIR", tmp_path),
            patch("butlers.migrations.ROSTER_DIR", tmp_path / "empty_roster"),
        ):
            result = _resolve_chain_dir("mailbox")

        assert result == mig_dir

    def test_butler_chain_resolves_to_butlers_dir(self, tmp_path: Path) -> None:
        """Butler-specific chains resolve to roster/<name>/migrations/."""
        mig_dir = tmp_path / "relationship" / "migrations"
        mig_dir.mkdir(parents=True)

        with (
            patch("butlers.migrations.ALEMBIC_DIR", tmp_path / "empty_alembic"),
            patch("butlers.migrations.MODULES_DIR", tmp_path / "empty_modules"),
            patch("butlers.migrations.ROSTER_DIR", tmp_path),
        ):
            result = _resolve_chain_dir("relationship")

        assert result == mig_dir

    def test_nonexistent_chain_returns_none(self, tmp_path: Path) -> None:
        """Non-existent chains return None."""
        with (
            patch("butlers.migrations.ALEMBIC_DIR", tmp_path),
            patch("butlers.migrations.MODULES_DIR", tmp_path),
            patch("butlers.migrations.ROSTER_DIR", tmp_path),
        ):
            result = _resolve_chain_dir("does-not-exist")

        assert result is None


class TestGetAllChains:
    """Unit tests for get_all_chains."""

    def test_includes_shared_module_and_butler_chains(self, tmp_path: Path) -> None:
        """Returns shared chains first, then module chains, then butler chains."""
        alembic_dir = tmp_path / "alembic"
        modules_dir = tmp_path / "modules"
        butlers_dir = tmp_path / "butlers"

        # Create shared chain dirs (core only)
        (alembic_dir / "versions" / "core").mkdir(parents=True)

        # Create a module chain
        mod_mig_dir = modules_dir / "mailbox" / "migrations"
        mod_mig_dir.mkdir(parents=True)
        (mod_mig_dir / "001.py").write_text("# migration")

        # Create a butler chain
        mig_dir = butlers_dir / "my-butler" / "migrations"
        mig_dir.mkdir(parents=True)
        (mig_dir / "001.py").write_text("# migration")

        with (
            patch("butlers.migrations.ALEMBIC_DIR", alembic_dir),
            patch("butlers.migrations.MODULES_DIR", modules_dir),
            patch("butlers.migrations.ROSTER_DIR", butlers_dir),
        ):
            chains = get_all_chains()

        assert chains == ["core", "mailbox", "my-butler"]

    def test_real_chains_include_core(self) -> None:
        """The real chain list should always include core."""
        chains = get_all_chains()
        assert "core" in chains

    def test_real_chains_include_module_chains(self) -> None:
        """The real chain list should include mailbox and approvals as module chains."""
        chains = get_all_chains()
        assert "mailbox" in chains
        assert "approvals" in chains
        assert "memory" in chains


# ---------------------------------------------------------------------------
# Daemon integration tests for butler-specific migration ordering
# ---------------------------------------------------------------------------


class StubConfig(BaseModel):
    """Config schema for stub module."""


class StubModule(Module):
    """Stub module for testing migration ordering."""

    def __init__(self) -> None:
        self.started = False
        self.shutdown_called = False
        self.tools_registered = False

    @property
    def name(self) -> str:
        return "stub_mod"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        return "stub_mod"

    async def on_startup(self, config: Any, db: Any) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


def _make_butler_toml(tmp_path: Path, name: str, modules: dict | None = None) -> Path:
    """Write a butler.toml with a given butler name."""
    modules = modules or {}
    toml_lines = [
        "[butler]",
        f'name = "{name}"',
        "port = 9100",
        f'description = "Test butler {name}"',
        "",
        "[butler.db]",
        f'name = "butler_{name.replace("-", "_")}"',
    ]
    for mod_name, mod_cfg in modules.items():
        toml_lines.append(f"\n[modules.{mod_name}]")
        for k, v in mod_cfg.items():
            if isinstance(v, str):
                toml_lines.append(f'{k} = "{v}"')
            else:
                toml_lines.append(f"{k} = {v}")
    (tmp_path / "butler.toml").write_text("\n".join(toml_lines))
    return tmp_path


def _patch_infra():
    """Return patches for all daemon infrastructure dependencies."""
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

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "has_butler_chain": patch("butlers.daemon.has_butler_chain"),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=MagicMock()),
        "get_adapter": patch("butlers.daemon.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_pool": mock_pool,
    }


class TestButlerSpecificMigrationInDaemon:
    """Verify the daemon runs butler-specific migrations at the right time."""

    async def test_butler_with_chain_runs_butler_migrations(self, tmp_path: Path) -> None:
        """When has_butler_chain returns True, butler-specific migrations run
        after core and before module migrations."""
        butler_dir = _make_butler_toml(tmp_path, "relationship", modules={"stub_mod": {}})
        registry = ModuleRegistry()
        registry.register(StubModule)
        patches = _patch_infra()

        call_log: list[str] = []

        with (
            patches["db_from_env"],
            patches["run_migrations"] as mock_mig,
            patches["has_butler_chain"] as mock_has_chain,
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["recover_route_inbox"],
        ):
            mock_has_chain.return_value = True

            async def track_migration(db_url, chain="core"):
                call_log.append(f"migrate:{chain}")

            mock_mig.side_effect = track_migration

            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        # Verify ordering: core -> butler-specific -> module
        assert call_log == ["migrate:core", "migrate:relationship", "migrate:stub_mod"]

    async def test_butler_without_chain_skips_butler_migrations(self, tmp_path: Path) -> None:
        """When has_butler_chain returns False, no butler-specific migration runs."""
        butler_dir = _make_butler_toml(tmp_path, "test-butler", modules={"stub_mod": {}})
        registry = ModuleRegistry()
        registry.register(StubModule)
        patches = _patch_infra()

        call_log: list[str] = []

        with (
            patches["db_from_env"],
            patches["run_migrations"] as mock_mig,
            patches["has_butler_chain"] as mock_has_chain,
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["recover_route_inbox"],
        ):
            mock_has_chain.return_value = False

            async def track_migration(db_url, chain="core"):
                call_log.append(f"migrate:{chain}")

            mock_mig.side_effect = track_migration

            daemon = ButlerDaemon(butler_dir, registry=registry)
            await daemon.start()

        # Only core and module, no butler-specific
        assert call_log == ["migrate:core", "migrate:stub_mod"]

    async def test_butler_chain_checked_with_butler_name(self, tmp_path: Path) -> None:
        """has_butler_chain should be called with the butler's name from config."""
        butler_dir = _make_butler_toml(tmp_path, "my-special-butler")
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["has_butler_chain"] as mock_has_chain,
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["recover_route_inbox"],
        ):
            mock_has_chain.return_value = False

            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        mock_has_chain.assert_called_once_with("my-special-butler")

    async def test_no_modules_butler_with_chain(self, tmp_path: Path) -> None:
        """Butler with chain but no modules still runs butler-specific migrations."""
        butler_dir = _make_butler_toml(tmp_path, "relationship")
        patches = _patch_infra()

        call_log: list[str] = []

        with (
            patches["db_from_env"],
            patches["run_migrations"] as mock_mig,
            patches["has_butler_chain"] as mock_has_chain,
            patches["validate_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["recover_route_inbox"],
        ):
            mock_has_chain.return_value = True

            async def track_migration(db_url, chain="core"):
                call_log.append(f"migrate:{chain}")

            mock_mig.side_effect = track_migration

            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        assert call_log == ["migrate:core", "migrate:relationship"]
