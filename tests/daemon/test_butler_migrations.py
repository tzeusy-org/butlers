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
    admission_order = []
    with (
        patch(
            "butlers.migrations.get_all_chains", return_value=["core", "approvals", "switchboard"]
        ),
        patch("butlers.migrations._build_alembic_config", return_value=mock_cfg),
        patch("butlers.migrations.command.upgrade") as mock_upgrade,
        patch(
            "butlers.migrations.check_bootstrap_database",
            side_effect=lambda _url: admission_order.append("bootstrap"),
            create=True,
        ),
        patch(
            "butlers.migrations._bootstrap_extensions",
            side_effect=lambda _url: admission_order.append("extensions"),
        ),
    ):
        asyncio.run(run_migrations("postgresql://db", chain="all", schema="switchboard"))

    # REQ-database-security-011: online admission precedes the first runner DDL.
    assert admission_order == ["bootstrap", "extensions"]
    assert mock_upgrade.call_args_list == [
        ((mock_cfg, "core@head"),),
        ((mock_cfg, "approvals@head"),),
        ((mock_cfg, "switchboard@head"),),
    ]

    from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError

    with (
        patch(
            "butlers.migrations.check_bootstrap_database",
            side_effect=BootstrapPrerequisiteError("missing or untrusted"),
        ) as admit,
        patch("butlers.migrations._bootstrap_extensions") as extensions,
        patch("butlers.migrations._build_alembic_config") as build,
        patch("butlers.migrations.command.upgrade") as upgrade,
    ):
        with pytest.raises(BootstrapPrerequisiteError):
            asyncio.run(run_migrations("postgresql://unused"))
        admit.assert_called_once()
        extensions.assert_not_called()
        build.assert_not_called()
        upgrade.assert_not_called()
    with patch("butlers.migrations.check_bootstrap_database") as admit:
        with pytest.raises(ValueError, match="Invalid migration schema"):
            asyncio.run(run_migrations("postgresql://unused", schema="invalid-schema"))
        with pytest.raises(ValueError, match="Unknown migration chain"):
            asyncio.run(run_migrations("postgresql://unused", chain="absent_chain"))
        admit.assert_not_called()


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
    for expected in ["approvals", "contacts", "memory"]:
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
    (modules_dir / "approvals" / "migrations").mkdir(parents=True)
    (roster_dir / "relationship" / "migrations").mkdir(parents=True)

    with (
        patch("butlers.migrations.ALEMBIC_DIR", alembic_dir),
        patch("butlers.migrations.MODULES_DIR", modules_dir),
        patch("butlers.migrations.ROSTER_DIR", roster_dir),
    ):
        assert _resolve_chain_dir("core") == alembic_dir / "versions" / "core"
        assert _resolve_chain_dir("approvals") == modules_dir / "approvals" / "migrations"
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
    (modules_dir / "approvals" / "migrations").mkdir(parents=True)
    (modules_dir / "approvals" / "migrations" / "001.py").write_text("# migration")
    (butlers_dir / "my-butler" / "migrations").mkdir(parents=True)
    (butlers_dir / "my-butler" / "migrations" / "001.py").write_text("# migration")

    with (
        patch("butlers.migrations.ALEMBIC_DIR", alembic_dir),
        patch("butlers.migrations.MODULES_DIR", modules_dir),
        patch("butlers.migrations.ROSTER_DIR", butlers_dir),
    ):
        chains = get_all_chains()
    assert chains == ["core", "approvals", "my-butler"]

    # Real chain list
    real_chains = get_all_chains()
    for expected in ["core", "approvals", "memory"]:
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


def test_bootstrap_checker_is_read_only_fail_closed_and_distinguishes_connection_errors(
    tmp_path: Path,
) -> None:
    """REQ-database-security-011: software classification; catalog proof is real-PG elsewhere."""
    from sqlalchemy.exc import ProgrammingError

    from butlers import bootstrap_prerequisite as prerequisite

    class Result:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def one(self):
            return ("normal_migration", False)

    class Connection:
        def __init__(self, bad_at=None, bad_value=False, installed=False):
            self.calls = []
            self.bad_at = bad_at
            self.bad_value = bad_value
            self.installed = installed

        def execute(self, sql, params=None):
            query = str(sql)
            self.calls.append((query, params))
            if len(self.calls) == self.bad_at:
                return Result(self.bad_value)
            if query in prerequisite._INSTALLED.values():
                return Result(self.installed)
            return Result(True)

    from tests.bootstrap_prerequisite_helpers import _historical_runner

    historical = _historical_runner(tmp_path)
    assert Path(historical["__file__"]).is_relative_to(tmp_path)
    assert set(historical["get_all_chains"]()) == set(get_all_chains())
    assert historical["get_chain_head"]("core") == __import__(
        "butlers.migrations", fromlist=["get_chain_head"]
    ).get_chain_head("core")
    # The actual immutable generators own the transient cross-schema read ACL;
    # it cannot be a stable prerequisite for reaching its ordinary repair.
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    commands = []
    for filename, function, args in (
        (
            "core_001_foundation.py",
            "_apply_default_privileges",
            ("relationship", "butler_relationship_rw"),
        ),
        ("core_077_relationship_switchboard_read_grants.py", "upgrade", ()),
    ):
        source = root / "alembic" / "versions" / "core" / filename
        spec = importlib.util.spec_from_file_location("actual_acl_" + source.stem, source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(
            module,
            "_execute_best_effort",
            side_effect=lambda statement, **_kwargs: commands.append(statement),
        ):
            getattr(module, function)(*args)
    assert (
        'ALTER DEFAULT PRIVILEGES IN SCHEMA "switchboard" REVOKE ALL ON TABLES FROM "butler_relationship_rw"'
        in commands
    )
    assert (
        'ALTER DEFAULT PRIVILEGES IN SCHEMA "switchboard" GRANT SELECT ON TABLES TO "butler_relationship_rw"'
        in commands
    )
    assert (
        'ALTER DEFAULT PRIVILEGES IN SCHEMA "relationship" GRANT SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES ON TABLES TO "butler_relationship_rw"'
        in commands
    )
    profile = prerequisite._default_profile()
    assert {
        "role": "butler_relationship_rw",
        "schema": "switchboard",
        "kind": "r",
        "privilege": "SELECT",
    } not in profile
    assert {
        "role": "butler_relationship_rw",
        "schema": "relationship",
        "kind": "r",
        "privilege": "INSERT",
    } in profile
    assert {
        "role": "butler_general_rw",
        "schema": "general",
        "kind": "r",
        "privilege": "INSERT",
    } in profile

    accepted = Connection()
    prerequisite.check_bootstrap_connection(accepted)
    assert len(accepted.calls) == 12
    assert all(query.lstrip().startswith(("SELECT", "WITH")) for query, _ in accepted.calls)
    assert not any("bootstrap_configuration WHERE" in query for query, _ in accepted.calls)
    # NULL/nonboolean 'true' cannot stand in for positively inspected catalog state.
    for index in (2, 3, 4, 5, 6, 8, 10, 12):
        for value in (False, None, "true", 1):
            with pytest.raises(
                prerequisite.BootstrapPrerequisiteError, match="missing or untrusted|unreadable"
            ):
                prerequisite.check_bootstrap_connection(Connection(index, value))
    with pytest.raises(prerequisite.BootstrapPrerequisiteError, match="unreadable"):
        prerequisite.check_bootstrap_connection(Connection(installed=None))
    finalized = Connection(installed=True)
    prerequisite.check_bootstrap_connection(finalized)
    assert all("pg_catalog.pg_roles" in finalized.calls[index][0] for index in (7, 9, 11))
    assert all(":migration_role" in finalized.calls[index][0] for index in (7, 9, 11))
    assert prerequisite._qualified_predicate(
        "SELECT current_user, pg_roles.oid, 'pg_roles current_user' -- pg_roles\nFROM pg_roles"
    ) == (
        "SELECT CAST(:migration_role AS pg_catalog.name), pg_catalog.pg_roles.oid, "
        "'pg_roles current_user' -- pg_roles\nFROM pg_catalog.pg_roles"
    )
    unreadable = MagicMock()
    unreadable.execute.side_effect = ProgrammingError(
        "private-sql", {"password": "sensitive"}, Exception()
    )
    with pytest.raises(prerequisite.BootstrapPrerequisiteError, match="unreadable") as error:
        prerequisite.check_bootstrap_connection(unreadable)
    assert "private-sql" not in str(error.value) and "sensitive" not in str(error.value)
    engine = MagicMock()
    engine.connect.side_effect = ConnectionError("connection failed")
    with patch.object(prerequisite, "create_engine", return_value=engine):
        with pytest.raises(ConnectionError, match="connection failed"):
            prerequisite.check_bootstrap_database("postgresql://unused")
    engine.dispose.assert_called_once()


def test_direct_online_environment_admits_before_ddl_and_offline_never_attests() -> None:
    """REQ-database-security-011; REQ-database-security-012; REQ-deployment-hardening-010: both entrypoints fail closed."""
    import runpy

    from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError

    env = Path(__file__).resolve().parents[2] / "alembic" / "env.py"
    context = MagicMock()
    context.is_offline_mode.return_value = False
    context.config.get_main_option.side_effect = lambda name: {
        "sqlalchemy.url": "postgresql://unused",
        "butlers.target_schema": "probe",
        "version_table_schema": "probe",
    }.get(name)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    engine = MagicMock()
    engine.connect.return_value = connection
    events = []
    connection.rollback.side_effect = lambda: events.append("rollback")
    connection.exec_driver_sql.side_effect = lambda _sql: events.append("ddl")
    context.configure.side_effect = lambda **_kwargs: events.append("configure")
    with (
        patch("alembic.context", context),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch(
            "butlers.bootstrap_prerequisite.check_bootstrap_connection",
            side_effect=lambda _connection: events.append("admit"),
        ),
    ):
        runpy.run_path(str(env))
    assert events[:4] == ["admit", "rollback", "ddl", "ddl"]
    assert events[4] == "configure"
    events.clear()
    with (
        patch("alembic.context", context),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch(
            "butlers.bootstrap_prerequisite.check_bootstrap_connection",
            side_effect=BootstrapPrerequisiteError("missing or untrusted"),
        ),
        pytest.raises(BootstrapPrerequisiteError),
    ):
        runpy.run_path(str(env))
    assert events == []
    context.is_offline_mode.return_value = True
    with (
        patch("alembic.context", context),
        patch("sqlalchemy.create_engine", return_value=engine),
        patch("butlers.bootstrap_prerequisite.check_bootstrap_connection") as admission,
    ):
        runpy.run_path(str(env))
    admission.assert_not_called()
