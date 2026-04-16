"""Tests for butler-specific migration chain support — condensed.

Covers:
- _build_alembic_config: percent-encoded URLs, schema options
- has_butler_chain: existence/emptiness detection
- _discover_butler_chains / _discover_module_chains: sorted discovery
- _resolve_chain_dir: resolution priority
- get_all_chains: combined list
- Daemon: migration ordering, schema forwarding
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.daemon import ButlerDaemon
from butlers.migrations import (
    _build_alembic_config,
    _discover_butler_chains,
    _discover_module_chains,
    _resolve_chain_dir,
    get_all_chains,
    has_butler_chain,
    run_migrations,
)
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _build_alembic_config
# ---------------------------------------------------------------------------


def test_build_alembic_config_and_run_migrations() -> None:
    """Percent-encoded URLs preserved; schema options; invalid schema raises; chain='all' upgrades in order."""
    db_url = (
        "postgresql://butlers:butlers@localhost:54320/butlers"
        "?options=-csearch_path%3Dswitchboard%2Cpublic"
    )
    config = _build_alembic_config(db_url, chains=["core"])
    assert config.get_main_option("sqlalchemy.url") == db_url

    config2 = _build_alembic_config(
        "postgresql://butlers:butlers@localhost:54320/butlers",
        chains=["core"],
        target_schema="switchboard",
    )
    assert config2.get_main_option("butlers.target_schema") == "switchboard"
    assert config2.get_main_option("version_table_schema") == "switchboard"

    with pytest.raises(ValueError, match="Invalid migration schema name"):
        _build_alembic_config(
            "postgresql://butlers:butlers@localhost:54320/butlers",
            chains=["core"],
            target_schema="bad-schema",
        )

    # chain='all' upgrades each discovered chain in deterministic order
    mock_cfg = MagicMock()
    with (
        patch("butlers.migrations.get_all_chains", return_value=["core", "mailbox", "switchboard"]),
        patch("butlers.migrations._build_alembic_config", return_value=mock_cfg),
        patch("butlers.migrations.command.upgrade") as mock_upgrade,
        patch("butlers.migrations._bootstrap_extensions"),
    ):
        asyncio.run(run_migrations("postgresql://db", chain="all", schema="switchboard"))

    assert mock_upgrade.call_args_list == [
        ((mock_cfg, "core@head"),),
        ((mock_cfg, "mailbox@head"),),
        ((mock_cfg, "switchboard@head"),),
    ]


# ---------------------------------------------------------------------------
# has_butler_chain
# ---------------------------------------------------------------------------


def test_has_butler_chain(tmp_path):
    """has_butler_chain: True with .py migrations present, False when directory absent."""
    chain_dir = tmp_path / "my-butler" / "migrations"
    chain_dir.mkdir(parents=True)
    (chain_dir / "001_create_tables.py").write_text("# migration")

    with patch("butlers.migrations.ROSTER_DIR", tmp_path):
        assert has_butler_chain("my-butler") is True
        assert has_butler_chain("nonexistent-butler") is False


# ---------------------------------------------------------------------------
# _discover_butler_chains / _discover_module_chains
# ---------------------------------------------------------------------------


def test_discover_chains(tmp_path) -> None:
    """Butler and module discovery: sorted list of .py migrations; real chains include known names."""
    for name in ["zeta", "alpha"]:
        mig_dir = tmp_path / name / "migrations"
        mig_dir.mkdir(parents=True)
        (mig_dir / "001_tables.py").write_text("# migration")
    (tmp_path / "no-migrations").mkdir()

    with patch("butlers.migrations.ROSTER_DIR", tmp_path):
        assert _discover_butler_chains() == ["alpha", "zeta"]

    with patch("butlers.migrations.MODULES_DIR", tmp_path):
        assert _discover_module_chains() == ["alpha", "zeta"]

    # Real roster includes known chains
    for expected in ["general", "health", "relationship", "switchboard"]:
        assert expected in _discover_butler_chains()
    for expected in ["approvals", "mailbox", "memory"]:
        assert expected in _discover_module_chains()


# ---------------------------------------------------------------------------
# _resolve_chain_dir
# ---------------------------------------------------------------------------


def test_resolve_chain_dir(tmp_path) -> None:
    """Core → alembic/versions/; module → modules dir; butler → roster dir; missing → None."""
    alembic_dir = tmp_path / "alembic"
    modules_dir = tmp_path / "modules"
    roster_dir = tmp_path / "roster"

    (alembic_dir / "versions" / "core").mkdir(parents=True)
    (modules_dir / "mailbox" / "migrations").mkdir(parents=True)
    (roster_dir / "relationship" / "migrations").mkdir(parents=True)

    with (
        patch("butlers.migrations.ALEMBIC_DIR", alembic_dir),
        patch("butlers.migrations.MODULES_DIR", modules_dir),
        patch("butlers.migrations.ROSTER_DIR", roster_dir),
    ):
        assert _resolve_chain_dir("core") == alembic_dir / "versions" / "core"
        assert _resolve_chain_dir("mailbox") == modules_dir / "mailbox" / "migrations"
        assert _resolve_chain_dir("relationship") == roster_dir / "relationship" / "migrations"
        assert _resolve_chain_dir("does-not-exist") is None


# ---------------------------------------------------------------------------
# get_all_chains
# ---------------------------------------------------------------------------


def test_get_all_chains(tmp_path) -> None:
    """Shared chains first, then modules, then butlers; real list includes known chains."""
    alembic_dir = tmp_path / "alembic"
    modules_dir = tmp_path / "modules"
    butlers_dir = tmp_path / "butlers"

    (alembic_dir / "versions" / "core").mkdir(parents=True)
    (modules_dir / "mailbox" / "migrations").mkdir(parents=True)
    (modules_dir / "mailbox" / "migrations" / "001.py").write_text("# migration")
    (butlers_dir / "my-butler" / "migrations").mkdir(parents=True)
    (butlers_dir / "my-butler" / "migrations" / "001.py").write_text("# migration")

    with (
        patch("butlers.migrations.ALEMBIC_DIR", alembic_dir),
        patch("butlers.migrations.MODULES_DIR", modules_dir),
        patch("butlers.migrations.ROSTER_DIR", butlers_dir),
    ):
        chains = get_all_chains()
    assert chains == ["core", "mailbox", "my-butler"]

    # Real chain list
    real_chains = get_all_chains()
    for expected in ["core", "mailbox", "approvals", "memory"]:
        assert expected in real_chains


# ---------------------------------------------------------------------------
# Daemon integration: butler-specific migration ordering
# ---------------------------------------------------------------------------


class StubConfig(BaseModel):
    pass


class StubModule(Module):
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

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        return "stub_mod"

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        self.started = True

    async def on_shutdown(self) -> None:
        self.shutdown_called = True


def _make_butler_toml(
    tmp_path: Path,
    name: str,
    modules: dict | None = None,
    *,
    db_name: str | None = None,
    db_schema: str | None = None,
) -> Path:
    modules = modules or {}
    resolved_db_name = db_name or "butlers"
    resolved_schema = db_schema or name.replace("-", "_")
    toml_lines = [
        "[butler]",
        f'name = "{name}"',
        "port = 9100",
        f'description = "Test butler {name}"',
        "",
        "[butler.db]",
        f'name = "{resolved_db_name}"',
        f'schema = "{resolved_schema}"',
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
    mock_db.db_name = "butlers"

    mock_adapter = MagicMock()
    mock_adapter.binary_name = "claude"
    mock_adapter_cls = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "has_butler_chain": patch("butlers.lifecycle.has_butler_chain"),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=MagicMock()),
        "get_adapter": patch("butlers.lifecycle.get_adapter", return_value=mock_adapter_cls),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_pool": mock_pool,
    }


async def _run_daemon_with_migration_tracking(
    butler_dir: Path,
    registry: ModuleRegistry,
    *,
    has_chain: bool,
) -> tuple[list[str], list[str | None]]:
    """Start daemon and return (chain_order, schema_list) from migration calls."""
    patches = _patch_infra()
    call_log: list[str] = []
    schema_log: list[str | None] = []

    with (
        patches["db_from_env"],
        patches["run_migrations"] as mock_mig,
        patches["has_butler_chain"] as mock_has_chain,
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patches["FastMCP"],
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["recover_route_inbox"],
    ):
        mock_has_chain.return_value = has_chain

        async def track_migration(db_url, chain="core", schema=None):
            call_log.append(f"migrate:{chain}")
            schema_log.append(schema)

        mock_mig.side_effect = track_migration

        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

    return call_log, schema_log


async def test_butler_migration_ordering_and_schema_forwarding(tmp_path: Path) -> None:
    """Migration ordering: with chain runs core→butler→module; without chain skips butler.
    All chains receive the butler's configured schema."""
    registry_with_mod = ModuleRegistry()
    registry_with_mod.register(StubModule)

    # With chain + module: core→butler→module
    (tmp_path / "a").mkdir()
    butler_dir = _make_butler_toml(tmp_path / "a", "relationship", modules={"stub_mod": {}})
    call_log, _ = await _run_daemon_with_migration_tracking(
        butler_dir, registry_with_mod, has_chain=True
    )
    assert call_log == ["migrate:core", "migrate:relationship", "migrate:stub_mod"]

    # Without chain: butler step skipped
    (tmp_path / "b").mkdir()
    butler_dir2 = _make_butler_toml(tmp_path / "b", "test-butler", modules={"stub_mod": {}})
    call_log2, _ = await _run_daemon_with_migration_tracking(
        butler_dir2, registry_with_mod, has_chain=False
    )
    assert call_log2 == ["migrate:core", "migrate:stub_mod"]

    # Schema forwarded to all chains
    (tmp_path / "c").mkdir()
    butler_dir3 = _make_butler_toml(
        tmp_path / "c", "relationship", modules={"stub_mod": {}}, db_schema="relationship"
    )
    _, schema_log = await _run_daemon_with_migration_tracking(
        butler_dir3, registry_with_mod, has_chain=True
    )
    assert schema_log == ["relationship", "relationship", "relationship"]
