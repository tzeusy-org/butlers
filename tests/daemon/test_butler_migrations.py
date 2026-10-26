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
from contextlib import ExitStack, asynccontextmanager
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
    get_module_migration_chains,
    has_butler_chain,
    run_migrations,
)
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _build_alembic_config
# ---------------------------------------------------------------------------


class _MigrationOwnerDriver:
    """Driver boundary for the actual catalog-owned ordinary-DDL context."""

    def __init__(self):
        self.info = {"butlers.source_database": "butlers"}
        self.role = "maintenance_selected"
        self.closed = False
        self.commits = 0

    def exec_driver_sql(self, sql):
        from types import SimpleNamespace

        if "JOIN pg_catalog.pg_roles actor" in sql:
            return SimpleNamespace(one=lambda: ("test_selected", "migration_selected", True))
        if sql == 'SET ROLE "migration_selected"':
            self.role = "migration_selected"
        elif sql == "RESET ROLE":
            self.role = "maintenance_selected"
        else:
            raise AssertionError("Unexpected owner driver operation")

    def close(self):
        self.closed = True

    def commit(self):
        self.commits += 1


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
    held = _MigrationOwnerDriver()
    admission_order = []
    with (
        patch(
            "butlers.migrations.get_all_chains", return_value=["core", "approvals", "switchboard"]
        ),
        patch("butlers.migrations._build_alembic_config", return_value=mock_cfg),
        patch("butlers.migrations.command.upgrade") as mock_upgrade,
        patch("butlers.db_authority.interfaces.stage_dashboard_interfaces"),
        patch(
            "butlers.migrations.check_bootstrap_connection",
            side_effect=lambda actual: (
                admission_order.append("bootstrap") if actual is held else None
            ),
            create=True,
        ),
        patch(
            "butlers.migrations._bootstrap_extensions",
            side_effect=lambda _url, **kwargs: admission_order.append("extensions"),
        ),
    ):
        asyncio.run(
            run_migrations(
                "postgresql://unused", chain="all", schema="switchboard", connection=held
            )
        )

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
            "butlers.migrations.check_bootstrap_connection",
            side_effect=BootstrapPrerequisiteError("missing or untrusted"),
        ) as admit,
        patch("butlers.migrations._bootstrap_extensions") as extensions,
        patch("butlers.migrations._build_alembic_config") as build,
        patch("butlers.migrations.command.upgrade") as upgrade,
    ):
        with pytest.raises(BootstrapPrerequisiteError):
            asyncio.run(run_migrations("postgresql://unused", connection=held))
        admit.assert_called_once()
        extensions.assert_not_called()
        build.assert_not_called()
        upgrade.assert_not_called()
    with patch("butlers.migrations.check_bootstrap_connection") as admit:
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

    # Real Health declarations include HA's mandatory approval dependency;
    # its public module name resolves to the owning roster/home directory.
    from butlers.config import load_config
    from butlers.modules.registry import default_registry

    root = Path(__file__).resolve().parents[2]
    registry = default_registry()
    for name in ("home", "health"):
        config = load_config(root / "roster" / name)
        modules = registry.load_from_config(config.modules)
        chains = get_module_migration_chains(modules, name)
        assert {"contacts", "approvals"}.issubset(config.modules)
        if name == "home":
            assert "home_assistant" not in chains  # own butler chain already runs
        else:
            assert chains["home_assistant"] == "home"
        assert {"contacts", "approvals", "memory"}.issubset(chains.values())
    brokers = registry.load_from_config({"insight_broker": {}, "owner_conditions_broker": {}})
    assert get_module_migration_chains(brokers, "health") == {}


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

    name = "stub_mod"

    @property
    def config_schema(self) -> type[BaseModel]:
        return StubConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        self.tools_registered = True

    def migration_revisions(self) -> str | None:
        raise AssertionError("Startup and CLI must discover directories, not call this hook")

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

    async def _runtime_row(query, *args):
        if query.lstrip().startswith("SELECT *") and "runtime_config" in query:
            return {
                "butler_name": args[0],
                "core_groups": None,
                "core_groups_narrowing_reason": None,
                "catalog_read_sensitivity": "normal",
                "max_concurrent": 3,
                "max_queued": 10,
                "tool_exposure_policy": "eager_filtered",
                "blind_spot_preamble_enabled": True,
                "seeded_at": None,
                "updated_at": None,
            }
        return None

    mock_pool.fetchrow = AsyncMock(side_effect=_runtime_row)

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.execute = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _transaction():
        yield

    mock_conn.transaction = MagicMock(side_effect=_transaction)

    @asynccontextmanager
    async def _acquire():
        yield mock_conn

    mock_pool.acquire = MagicMock(side_effect=_acquire)
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
    mock_adapter_builder = MagicMock(return_value=mock_adapter)

    return {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", return_value=mock_db),
        "run_migrations": patch(
            "butlers.lifecycle.run_migrations", new_callable=AsyncMock, create=True
        ),
        "require_installed": patch(
            "butlers.lifecycle.require_installed_chain", new_callable=AsyncMock
        ),
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
        "build_runtime_adapter": patch(
            "butlers.lifecycle.build_runtime_adapter", new=mock_adapter_builder
        ),
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
    module_root = butler_dir / "module_chains"
    migrations = module_root / "stub_mod" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "001_stub.py").write_text(
        "# Directory presence, not a hook, selects this chain.\n"
    )

    with ExitStack() as stack:
        mock_mig = stack.enter_context(patches["run_migrations"])
        mock_read = stack.enter_context(patches["require_installed"])
        mock_has_chain = stack.enter_context(patches["has_butler_chain"])
        for key in (
            "db_from_env",
            "validate_credentials",
            "validate_module_credentials",
            "init_telemetry",
            "sync_schedules",
            "FastMCP",
            "Spawner",
            "build_runtime_adapter",
            "shutil_which",
            "start_mcp_server",
            "recover_route_inbox",
        ):
            stack.enter_context(patches[key])
        stack.enter_context(patch("butlers.migrations.MODULES_DIR", module_root))
        mock_has_chain.return_value = has_chain

        async def track_admission(pool, *, chain, schema):
            assert pool is patches["mock_pool"]
            call_log.append(f"admit:{chain}")
            schema_log.append(schema)

        mock_read.side_effect = track_admission
        mock_mig.side_effect = AssertionError("Serving startup cannot run migrations")

        daemon = ButlerDaemon(butler_dir, registry=registry)
        await daemon.start()

        mock_mig.assert_not_awaited()
        # Explicit survivor: the same declared chain order is now read-only
        # during serving startup and mutating only in the selected CLI actor.
        daemon_calls = [call.replace("admit:", "migrate:", 1) for call in call_log]
        daemon_schemas = list(schema_log)
        call_log.clear()
        schema_log.clear()
        from butlers.cli import _migrate_all

        held = _MigrationOwnerDriver()
        session = MagicMock()
        session.__enter__.return_value = held

        async def track_migration(db_url, chain="core", schema=None, *, connection):
            assert db_url == "postgresql://synthetic/butlers"
            assert connection is held
            call_log.append(f"migrate:{chain}")
            schema_log.append(schema)

        with (
            patch.dict(
                "os.environ", {"BUTLERS_MIGRATOR_DATABASE_URL": "postgresql://synthetic/butlers"}
            ),
            patch("butlers.migrations.migration_session", return_value=session),
            patch("butlers.migrations.finalize_authority") as finalize,
            patch("butlers.modules.registry.default_registry", return_value=registry),
            patch("butlers.migrations.run_migrations", side_effect=track_migration),
            patch("butlers.migrations.has_butler_chain", return_value=has_chain),
            patch(
                "butlers.db.db_params_from_env",
                return_value={
                    "user": "test",
                    "password": "test",
                    "host": "localhost",
                    "port": 5432,
                },
            ),
        ):
            await _migrate_all({daemon.config.name: butler_dir}, dry_run=False)
        finalize.assert_called_once()
        assert finalize.call_args.args[0] is held
        assert call_log == daemon_calls
        assert schema_log == daemon_schemas

    return call_log, schema_log


async def test_butler_migration_ordering_and_schema_forwarding(tmp_path: Path) -> None:
    """REQ-core-modules-010/REQ-core-daemon-008: same declared chain order.

    Serving only reads installed heads; the selected migrator performs DDL.
    Both preserve core→butler→module ordering and configured schemas.
    """
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
            return self.value

    class Connection:
        def __init__(self, bad_at=None, bad_value=False, installed=False):
            self.calls = []
            self.bad_at = bad_at
            self.bad_value = bad_value
            self.installed = installed  # a diagnostic-only old marker is never an authority
            self.info = {"legacy_install_marker": installed}

        def exec_driver_sql(self, statement):
            assert statement == "SELECT current_database(), session_user"
            return Result(("test_selected", "normal_migration"))

        def execute(self, sql, params=None):
            query = str(sql)
            self.calls.append((query, params))
            assert query == prerequisite._BOOTSTRAP_PROFILE_SQL
            facts = [True] * 5
            if self.bad_at is not None:
                facts[self.bad_at - 1] = self.bad_value
            return Result(tuple(facts))

    from tests.bootstrap_prerequisite_helpers import _historical_runner

    historical = _historical_runner(tmp_path)
    assert Path(historical["__file__"]).is_relative_to(tmp_path)
    assert set(historical["get_all_chains"]()) == set(get_all_chains())
    assert historical["get_chain_head"]("core") == __import__(
        "butlers.migrations", fromlist=["get_chain_head"]
    ).get_chain_head("core")
    # The actual immutable generators own the transient cross-schema read ACL;
    # it cannot be a stable prerequisite for reaching its ordinary repair.
    import hashlib
    from types import ModuleType

    from butlers.db_authority.apply import _PREDECESSOR_COMMIT

    root = Path(__file__).resolve().parents[2]
    # Complete inert producers bind the rollback release without requiring its
    # Git history to be present in a shallow CI checkout. Validate both before
    # compiling or executing either historical producer.
    assert _PREDECESSOR_COMMIT == "30b376895e1108977bf1b80bf54fcbdaaa3d64ad"
    pinned_sources = {}
    for filename, fixture, digest in (
        (
            "core_001_foundation.py",
            "core_001_foundation_30b.py.txt",
            "10c264462f62aef1afecb0ebb424f46ed1d553ab6b2a62e8c0717cff5e72908a",
        ),
        (
            "core_077_relationship_switchboard_read_grants.py",
            "core_077_relationship_switchboard_read_grants_30b.py.txt",
            "10251f7ae7e57a370232c56cb9145628f25276bc7241a97b35a690919ca88c28",
        ),
    ):
        source = (root / "tests" / "fixtures" / "db_authority_predecessor" / fixture).read_bytes()
        assert hashlib.sha256(source).hexdigest() == digest
        pinned_sources[filename] = source
    commands = []
    for filename, function, args in (
        (
            "core_001_foundation.py",
            "_apply_default_privileges",
            ("relationship", "butler_relationship_rw"),
        ),
        ("core_077_relationship_switchboard_read_grants.py", "upgrade", ()),
    ):
        path = "alembic/versions/core/" + filename
        # Execute the complete, hash-checked historical producer; the current
        # serving bootstrap cannot retain these broad-authority helpers.
        source = pinned_sources[filename]
        module = ModuleType("pinned_acl_" + filename.removesuffix(".py"))
        exec(compile(source, path, "exec"), module.__dict__)
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
    from butlers.db_authority.grants import compile_relation_manifest

    manifest = compile_relation_manifest(
        "test_selected", "normal_migration", "maintenance_selected", source_database="butlers"
    )
    # Concrete source-owned relations replace future-object default grants.
    profile = [
        dict(
            role=permission.role,
            schema=row.schema,
            kind={"table": "r", "view": "v", "sequence": "S"}[row.kind.value],
            privilege=verb,
        )
        for row in manifest.relations
        for permission in row.permissions
        for verb in permission.verbs
        # This released RFC0010 concrete read is an explicit exception, never
        # evidence for a future-object default or other Switchboard table.
        if not (
            row.schema == "switchboard"
            and row.name == "message_inbox"
            and permission.role == "butler_relationship_rw"
            and verb == "SELECT"
        )
    ]
    assert {
        row.name
        for row in manifest.relations
        if row.schema == "switchboard"
        for permission in row.permissions
        if permission.role == "butler_relationship_rw" and "SELECT" in permission.verbs
    } == {"message_inbox"}
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
    assert len(accepted.calls) == 1
    assert all(query.lstrip().startswith(("SELECT", "WITH")) for query, _ in accepted.calls)
    assert not any("bootstrap_configuration WHERE" in query for query, _ in accepted.calls)
    # NULL/nonboolean 'true' cannot stand in for positively inspected catalog state.
    for index in (1, 2, 3, 4, 5):
        for value in (False, None, "true", 1):
            with pytest.raises(
                prerequisite.BootstrapPrerequisiteError,
                match="missing, unsafe or untrusted|unreadable",
            ):
                prerequisite.check_bootstrap_connection(Connection(index, value))
    # A stored legacy marker never replaces these live catalog facts.
    finalized = Connection(installed=True)
    prerequisite.check_bootstrap_connection(finalized)
    assert all("pg_catalog.pg_roles" in query for query, _ in finalized.calls)
    assert all(":roles" in query for query, _ in finalized.calls)
    with pytest.raises(prerequisite.BootstrapPrerequisiteError):
        prerequisite.check_bootstrap_connection(Connection(1, False, installed=True))
    # Fixed qualified SQL replaces the retired arbitrary-query rewriter; bound
    # source-compiled identities cannot become interpolated SQL fragments.
    assert "CAST(:roles AS jsonb)" in prerequisite._BOOTSTRAP_PROFILE_SQL
    assert "FROM pg_roles" not in prerequisite._BOOTSTRAP_PROFILE_SQL
    unreadable = Connection()
    unreadable.execute = MagicMock()
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
    """REQ-database-security-011/012: actual entrypoint holds selected lock to verification."""
    import runpy
    from contextlib import contextmanager
    from types import SimpleNamespace

    from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError

    env = Path(__file__).resolve().parents[2] / "alembic" / "env.py"
    context = MagicMock()
    context.is_offline_mode.return_value = False
    context.config.attributes = {}
    context.config.get_main_option.side_effect = lambda name: {
        "sqlalchemy.url": "postgresql://unused",
        "butlers.target_schema": "probe",
        "version_table_schema": "probe",
    }.get(name)
    context.get_revision_argument.return_value = "core@head"
    connection = MagicMock()
    connection.info = {}
    connection.execute.return_value.scalar_one.return_value = None  # Fresh version table absent.
    events = []
    refuse = False

    @contextmanager
    def selected_session(url):
        assert url == "postgresql://unused"
        events.append("admit")
        if refuse:
            raise BootstrapPrerequisiteError("missing or untrusted")
        events.append("lock")
        try:
            yield connection
        finally:
            events.append("release")

    @contextmanager
    def normal_owner(conn):
        assert conn is connection
        events.append("normal")
        yield
        events.append("reset")

    script = MagicMock()
    script.get_revisions.return_value = (SimpleNamespace(revision="core_281"),)
    connection.rollback.side_effect = lambda: events.append("rollback")
    connection.exec_driver_sql.side_effect = lambda _sql: events.append("ddl")
    context.configure.side_effect = lambda **_kwargs: events.append("configure")
    context.run_migrations.side_effect = lambda: events.append("upgrade")
    with (
        patch("alembic.context", context),
        patch("alembic.script.ScriptDirectory.from_config", return_value=script),
        patch("butlers.migrations.migration_session", selected_session),
        patch("butlers.migrations.get_all_chains", return_value=["core"]),
        patch("butlers.migrations.get_chain_revision_ids", return_value={"core_281"}),
        patch("butlers.migrations.get_chain_head", return_value="core_281"),
        patch(
            "butlers.migrations.get_chain_revision_path",
            return_value=frozenset({"core_277", "core_278", "core_281"}),
        ),
        patch("butlers.migrations.ordinary_migration_identity", normal_owner),
        patch(
            "butlers.migrations._bootstrap_extensions",
            side_effect=lambda *a, **k: events.append("extension"),
        ),
        patch(
            "butlers.db_authority.interfaces.stage_dashboard_interfaces",
            side_effect=lambda c: events.append("stage"),
        ),
        patch(
            "butlers.migrations.finalize_authority",
            side_effect=lambda c, **k: events.append(("verify", c is connection, k["plans"])),
        ) as finalize,
        patch("butlers.bootstrap_prerequisite.check_bootstrap_connection") as admission,
    ):
        runpy.run_path(str(env))
        assert events[:4] == ["admit", "lock", "stage", "extension"]
        assert events.index("upgrade") < events.index(("verify", True, (("core", "probe", None),)))
        assert events[-1] == "release"
        finalize.assert_called_once_with(connection, plans=(("core", "probe", None),))
        admission.assert_called_once_with(connection)
        events.clear()
        refuse = True
        with pytest.raises(BootstrapPrerequisiteError):
            runpy.run_path(str(env))
        assert events == ["admit"]
        assert "ddl" not in events
        context.is_offline_mode.return_value = True
        admission.reset_mock()
        events.clear()
        runpy.run_path(str(env))
        admission.assert_not_called()
        assert events == ["configure", "upgrade"]


async def test_explicit_migrator_connection_is_handed_to_every_chain(monkeypatch):
    """An admitted CLI session cannot be replaced by a per-chain connection."""
    from unittest.mock import Mock

    import butlers.migrations as migrations

    connection = _MigrationOwnerDriver()
    configs = []
    staged = Mock()
    monkeypatch.setattr("butlers.db_authority.interfaces.stage_dashboard_interfaces", staged)
    monkeypatch.setattr(migrations, "check_bootstrap_connection", Mock(), raising=False)
    monkeypatch.setattr(migrations, "_bootstrap_extensions", Mock())
    monkeypatch.setattr(migrations, "_resolve_target_chains", lambda chain: ["core", "memory"])
    monkeypatch.setattr(migrations, "_resolve_chain_dir", lambda chain: Path("present"))
    monkeypatch.setattr(
        migrations,
        "_upgrade_chain",
        lambda config, chain, schema: configs.append((config, chain, schema)),
    )
    await migrations.run_migrations(
        "postgresql://synthetic", chain="all", schema="chronicler", connection=connection
    )
    staged.assert_called_once_with(connection)
    assert [row[1] for row in configs] == ["core", "memory"]
    assert all(row[0].attributes["connection"] is connection for row in configs)
    assert all(row[2] == "chronicler" for row in configs)
    assert connection.closed is False
    assert connection.commits == 0


async def test_serving_startup_verifies_installed_head_without_running_migration(monkeypatch):
    from types import SimpleNamespace

    from butlers import lifecycle

    conn = AsyncMock()
    conn.fetchval.return_value = True

    @asynccontextmanager
    async def acquire():
        yield conn

    pool = SimpleNamespace(acquire=acquire)
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="general", db_schema="general"),
        _build_db_url=lambda: "postgresql://unused",
    )
    state = lifecycle.StartupState(pool=pool)
    migrate = AsyncMock(side_effect=AssertionError("Serving must not migrate"))
    monkeypatch.setattr(lifecycle, "run_migrations", migrate, raising=False)
    monkeypatch.setattr(lifecycle, "has_butler_chain", lambda name: False)
    await lifecycle._startup_core_migrations(daemon, state)
    assert conn.fetchval.await_args.args[1] == __import__(
        "butlers.migrations", fromlist=["get_chain_head"]
    ).get_chain_head("core")
    migrate.assert_not_awaited()
    conn.fetchval.return_value = False
    with pytest.raises(RuntimeError):
        await lifecycle._startup_core_migrations(daemon, state)


def test_disposable_migration_producer_holds_one_session_through_finalizer(monkeypatch):
    from contextlib import contextmanager

    import butlers.migrations as migrations
    import butlers.testing.migration as producer

    events = []
    connection = object()
    url = "postgresql://migration_selected:synthetic@localhost/test_selected"
    maintenance_url = "postgresql://bootstrap_selected:synthetic@localhost/test_selected"
    monkeypatch.setattr(producer, "create_migration_db", lambda container, name: url)
    monkeypatch.setattr(
        producer, "migration_bootstrap_db_url", lambda container, name: maintenance_url
    )

    @contextmanager
    def selected_session(selected_url, *, source_database=None):
        assert selected_url == maintenance_url
        assert source_database == "butlers"
        events.append("lock")
        try:
            yield connection
        finally:
            events.append("release")

    async def upgrade(selected_url, chain, schema, *, connection):
        assert selected_url == url
        assert connection is selected_connection
        events.append((chain, schema))

    def finalize(actual, *, source_database=None, plans=None):
        assert actual is connection
        assert source_database == "butlers"
        assert plans == (("core", None, None), ("memory", "chronicler_mem", None))
        events.append("finalize")

    selected_connection = connection
    monkeypatch.setattr(migrations, "migration_session", selected_session)
    monkeypatch.setattr(migrations, "run_migrations", upgrade)
    monkeypatch.setattr(migrations, "finalize_authority", finalize)
    assert (
        producer.create_migrated_test_db(
            object(), "test_selected", ["core", "memory"], {"memory": "chronicler_mem"}
        )
        == url
    )
    assert events == ["lock", ("core", None), ("memory", "chronicler_mem"), "finalize", "release"]


def test_disposable_bootstrap_hands_actual_source_manifest_to_production_sql(monkeypatch):
    """The production preparation receives a selected, source-derived manifest."""
    import json

    from butlers.testing import migration as producer

    calls = []
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.execute.side_effect = lambda sql, *args: calls.append((str(sql), args))
    raw = MagicMock()
    raw.cursor.return_value = cursor
    engine = MagicMock()
    engine.raw_connection.return_value = raw
    monkeypatch.setattr(producer, "create_engine", lambda *args, **kwargs: engine)
    producer._bootstrap_migration_prerequisites(
        "postgresql://bootstrap:synthetic@localhost/test_selected", "migration_selected"
    )
    assert "butlers.source_manifest" in calls[0][0]
    manifest = json.loads(calls[0][1][0][0])
    assert manifest["database"] == "test_selected"
    assert manifest["migration_role"] == "migration_selected"
    assert "butler_chronicler_rw" in manifest["roles"]
    assert "chronicler_mem" in manifest["schemas"]
    assert calls[1][0] == producer.init_db_sql_for_dbapi()
    raw.close.assert_called_once()
    engine.dispose.assert_called_once()


def test_controlled_maintenance_uses_normal_owner_for_actual_chain_and_restores(monkeypatch):
    """Actual chain runner keeps the selected session and normal CREATE owner."""
    from types import SimpleNamespace

    import butlers.migrations as migrations

    events = []

    class Connection:
        info = {"butlers.source_database": "butlers"}
        role = "maintenance_selected"
        valid = True
        owner = "migration_selected"

        def exec_driver_sql(self, sql):
            if sql == "SELECT current_database(), session_user":
                return SimpleNamespace(one=lambda: ("test_selected", "maintenance_selected"))
            if "JOIN pg_catalog.pg_roles actor" in sql:
                return SimpleNamespace(one=lambda: ("test_selected", self.owner, self.valid))
            if sql.startswith("SET ROLE "):
                self.role = sql.split('"')[1]
            elif sql == "RESET ROLE":
                self.role = "maintenance_selected"
            else:
                raise AssertionError("Unexpected driver operation")
            events.append((sql, self.role))

        def execute(self, statement, parameters):
            assert "owners_isolated" in str(statement)
            return SimpleNamespace(one=lambda: (True,) * 5)

    connection = Connection()
    config = SimpleNamespace(attributes={})
    monkeypatch.setattr(migrations, "_resolve_target_chains", lambda chain: ["memory"])
    monkeypatch.setattr(migrations, "_resolve_chain_dir", lambda chain: Path("present"))
    monkeypatch.setattr(migrations, "_build_alembic_config", lambda *a, **kw: config)

    def actual_chain(cfg, chain, schema):
        assert cfg.attributes["connection"] is connection
        assert connection.role == "migration_selected"
        events.append(("ordinary_create", connection.role))

    monkeypatch.setattr(migrations, "_upgrade_chain", actual_chain)
    asyncio.run(
        migrations.run_migrations(
            "postgresql://unused", chain="memory", schema="chronicler_mem", connection=connection
        )
    )
    assert events == [
        ('SET ROLE "migration_selected"', "migration_selected"),
        ("ordinary_create", "migration_selected"),
        ("RESET ROLE", "maintenance_selected"),
    ]
    assert connection.role == "maintenance_selected"
    for invalid_owner, valid in (("migration_selected", False), ("butler_general_rw", True)):
        connection.owner, connection.valid = invalid_owner, valid
        events.clear()
        with pytest.raises(RuntimeError):
            asyncio.run(
                migrations.run_migrations(
                    "postgresql://unused", chain="memory", connection=connection
                )
            )
        assert events == []


def test_actual_historical_connector_and_calendar_producers_do_not_replay_broad_authority():
    """Run the real revision producers; the driver is the only replaced boundary."""
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    for filename, view, prefix in (
        ("core_001_foundation.py", "foundation", None),
        ("core_007_connectors.py", None, None),
        ("core_140_v_overlay_contributions.py", "v_overlay_contributions", "calendar/overlay/%"),
        ("core_142_v_prep_contributions.py", "v_prep_contributions", "calendar/prep/%"),
    ):
        path = root / "alembic" / "versions" / "core" / filename
        spec = importlib.util.spec_from_file_location("current_producer_" + path.stem, path)
        producer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(producer)
        statements = []
        with patch.object(producer.op, "execute", side_effect=statements.append):
            if view and view != "foundation":
                with patch.object(producer, "_state_table_exists", return_value=True):
                    producer.upgrade()
            else:
                producer.upgrade()
        sql = "\n".join(statements)
        assert "ON ALL TABLES" not in sql
        assert "ALTER DEFAULT PRIVILEGES" not in sql
        assert "GRANT USAGE, CREATE" not in sql
        assert "CREATE ROLE" not in sql
        if view == "foundation":
            for name in (
                "state",
                "sessions",
                "scheduled_tasks",
                "route_inbox",
                "butler_secrets",
                "session_process_logs",
                "corrections",
            ):
                assert f"CREATE TABLE IF NOT EXISTS {name}" in sql
            assert "CREATE TABLE IF NOT EXISTS public.ingestion_events" in sql
            statements.clear()
            with patch.object(producer.op, "execute", side_effect=statements.append):
                producer.downgrade()
            drops = "\n".join(statements)
            assert "DROP TABLE IF EXISTS public.ingestion_events" in drops
            assert "REVOKE" not in drops
        elif view:
            assert f"CREATE OR REPLACE VIEW calendar.{view}" in sql
            for schema in producer._SPECIALIST_SCHEMAS:
                assert f"FROM {schema}.state WHERE key LIKE '{prefix}'" in sql
                assert f"GRANT SELECT ON TABLE {schema}.state" not in sql
            assert f'GRANT SELECT ON TABLE calendar.{view} TO "dashboard_api"' in sql
            statements.clear()
            with (
                patch.object(producer.op, "execute", side_effect=statements.append),
                patch.object(producer, "_state_table_exists", return_value=False),
            ):
                producer.upgrade()
            assert "WHERE FALSE" in "\n".join(statements)
        else:
            assert "CREATE TABLE IF NOT EXISTS connectors.filtered_events" in sql
            assert "PARTITION OF connectors.filtered_events" in sql
            assert "full_payload        JSONB NOT NULL" in sql
            assert "SELECT connectors.connectors_filtered_events_ensure_partition(now())" in sql


@pytest.mark.parametrize("primary_kind", (None, RuntimeError, asyncio.CancelledError))
def test_actual_migration_session_preserves_primary_and_attempts_every_cleanup(
    monkeypatch, primary_kind
):
    """A failed close cannot mask the chain error or strand the owned engine."""
    from types import SimpleNamespace

    import butlers.migrations as migrations

    events = []
    close_error = OSError("synthetic owned connection close failure")
    dispose_error = LookupError("synthetic owned engine disposal failure")
    primary = None if primary_kind is None else primary_kind("synthetic chain failure")

    class Connection:
        info = {"butlers.source_database": "butlers"}

        def exec_driver_sql(self, sql):
            if sql == "SELECT current_database(), session_user":
                return SimpleNamespace(one=lambda: ("test_selected", "maintenance_selected"))
            if "JOIN pg_catalog.pg_roles actor" in sql:
                return SimpleNamespace(one=lambda: ("test_selected", "migration_selected", True))
            raise AssertionError("Unexpected driver operation")

        def execute(self, sql, parameters):
            if "owners_isolated" in str(sql):
                return SimpleNamespace(one=lambda: (True,) * 5)
            if "pg_try_advisory_lock" in str(sql):
                events.append("lock")
            elif "pg_advisory_unlock" in str(sql):
                events.append("unlock")
            else:
                raise AssertionError("Unexpected driver operation")
            return SimpleNamespace(scalar_one=lambda: True)

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")
            raise close_error

    def dispose():
        events.append("dispose")
        raise dispose_error

    connection = Connection()
    engine = SimpleNamespace(connect=lambda: connection, dispose=dispose)

    def bounded_engine(url, **options):
        assert url == "postgresql://synthetic"
        assert options["poolclass"] is migrations.NullPool
        assert options["connect_args"] == {
            "connect_timeout": 5,
            "options": "-c statement_timeout=300000 -c lock_timeout=5000",
        }
        return engine

    monkeypatch.setattr(migrations, "create_engine", bounded_engine)
    with pytest.raises(BaseException) as caught:
        with migrations.migration_session("postgresql://synthetic") as held:
            assert held is connection
            events.append("chain")
            if primary is not None:
                raise primary
    assert caught.value is (close_error if primary is None else primary)
    assert events == [
        "commit",
        "lock",
        "commit",
        "chain",
        "rollback",
        "unlock",
        "commit",
        "close",
        "dispose",
    ]
    assert len(caught.value.__notes__) == (1 if primary is None else 2)


@pytest.mark.parametrize("fault", [None, "profile", "maintenance", "staging", "restore"])
def test_dashboard_stage_uses_actual_normal_owner_and_restores_session_setting(fault):
    """Execute the source installer boundary; only SQL transport is doubled."""
    from types import SimpleNamespace

    from butlers.db_authority.interfaces import DASHBOARD_PURPOSE_SQL, stage_dashboard_interfaces
    from butlers.db_authority.roles import AuthorityError

    class Driver:
        info = {}

        def __init__(self):
            self.events = []
            self.setting = "previous_selected_owner"

        def exec_driver_sql(self, sql):
            # Model the unbound DBAPI percent-decoding before SQL dispatch.
            sql = sql % {}
            if "d.datname, owner.rolname" in sql:
                return SimpleNamespace(
                    one=lambda: ("butlers", "migration_selected", fault != "profile")
                )
            if "SELECT current_user=session_user AND rolsuper" in sql:
                return SimpleNamespace(scalar_one=lambda: fault != "maintenance")
            if "SELECT current_setting" in sql:
                return SimpleNamespace(scalar_one=lambda: self.setting)
            assert sql == DASHBOARD_PURPOSE_SQL
            assert self.setting == "migration_selected"
            self.events.append("staged")
            if fault == "staging":
                raise LookupError("synthetic staging failure")

        def execute(self, statement, params):
            assert "set_config('butlers.connecting_user'" in str(statement)
            self.events.append(params["owner"])
            if fault == "restore" and params["owner"] == "previous_selected_owner":
                raise LookupError("synthetic restoration failure")
            self.setting = params["owner"]

    driver = Driver()
    if fault in ("profile", "maintenance"):
        with pytest.raises(AuthorityError):
            stage_dashboard_interfaces(driver)
        assert driver.events == []
    elif fault in ("staging", "restore"):
        with pytest.raises(LookupError):
            stage_dashboard_interfaces(driver)
        assert driver.events == ["migration_selected", "staged", "previous_selected_owner"]
    else:
        stage_dashboard_interfaces(driver)
        assert driver.events == ["migration_selected", "staged", "previous_selected_owner"]
    if fault != "restore":
        assert driver.setting == "previous_selected_owner"


@pytest.mark.parametrize("fault", [None, "actor", "cancel", "unlock", "close"])
def test_operator_reverse_owns_real_lock_and_releases_session_before_receipt(monkeypatch, fault):
    from types import SimpleNamespace

    from butlers import migrations
    from butlers.db_authority.roles import AuthorityError

    events = []
    primary = asyncio.CancelledError("synthetic operator cancellation")

    class Connection:
        def exec_driver_sql(self, sql):
            if sql == "SELECT session_user":
                return SimpleNamespace(scalar_one=lambda: "maintenance")
            assert sql.startswith("SELECT current_user=session_user AND rolsuper")
            events.append("actual-admin-read")
            return SimpleNamespace(scalar_one=lambda: fault != "actor")

        def execute(self, statement, parameters):
            assert parameters == dict(
                family=migrations._MIGRATION_LOCK[0], operation=migrations._MIGRATION_LOCK[1]
            )
            if "pg_try_advisory_lock" in str(statement):
                events.append("lock")
                return SimpleNamespace(scalar_one=lambda: True)
            assert "pg_advisory_unlock" in str(statement)
            events.append("unlock")
            return SimpleNamespace(scalar_one=lambda: fault != "unlock")

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("physical-close")
            if fault == "close":
                raise OSError("synthetic close refusal")

    connection = Connection()

    def create_engine(url, **options):
        assert url == "postgresql://synthetic-admin"
        assert options["poolclass"] is migrations.NullPool
        assert options["connect_args"]["connect_timeout"] == 5
        assert "statement_timeout=300000" in options["connect_args"]["options"]
        return SimpleNamespace(
            connect=lambda: connection, dispose=lambda: events.append("engine-dispose")
        )

    monkeypatch.setattr(migrations, "create_engine", create_engine)
    error = None
    try:
        with migrations.operator_rollback_session("postgresql://synthetic-admin") as held:
            assert held is connection
            events.append("operator-body")
            if fault == "cancel":
                raise primary
            held.commit()
        events.append("completed-receipt")
    except BaseException as exc:
        error = exc
    if fault is None:
        assert error is None
        assert events[-3:] == ["physical-close", "engine-dispose", "completed-receipt"]
    else:
        assert "completed-receipt" not in events
        assert events[-2:] == ["physical-close", "engine-dispose"]
        if fault == "actor":
            assert isinstance(error, AuthorityError)
            assert "lock" not in events and "operator-body" not in events
        elif fault == "cancel":
            assert error is primary
        elif fault == "unlock":
            assert isinstance(error, RuntimeError)
        else:
            assert isinstance(error, OSError)
    assert events.count("physical-close") == events.count("engine-dispose") == 1
    assert events.count("unlock") == (0 if fault == "actor" else 1)


@pytest.mark.parametrize(("revision", "staged"), [("core_170", False), ("core_277", True)])
def test_bounded_core_producer_uses_actual_d13_staging_and_normal_owner(
    monkeypatch, revision, staged
):
    from types import SimpleNamespace

    import butlers.db_authority.interfaces as interfaces
    from alembic import command
    from butlers.testing import migration as producer

    events = []

    class Connection:
        info = {"butlers.source_database": "butlers"}
        role = "maintenance_selected"
        valid = True

        def exec_driver_sql(self, sql):
            # Model the unbound DBAPI percent-decoding before SQL dispatch.
            sql = sql % {}
            if "JOIN pg_catalog.pg_roles actor" in sql:
                return SimpleNamespace(
                    one=lambda: ("test_selected", "migration_selected", self.valid)
                )
            if sql == "SELECT current_setting('butlers.connecting_user', true)":
                return SimpleNamespace(scalar_one=lambda: "")
            if sql.startswith("SELECT current_user=session_user AND rolsuper"):
                return SimpleNamespace(scalar_one=lambda: self.role == "maintenance_selected")
            if sql == interfaces.DASHBOARD_PURPOSE_SQL:
                assert self.role == "maintenance_selected"
                events.append("source_d13")
            elif sql == 'SET ROLE "migration_selected"':
                self.role = "migration_selected"
                events.append("normal_owner")
            elif sql == "RESET ROLE":
                self.role = "maintenance_selected"
                events.append("restored")
            else:
                raise AssertionError("Unexpected bounded migration driver operation")

        def execute(self, statement, params):
            assert "set_config('butlers.connecting_user'" in str(statement)
            assert params in ({"owner": "migration_selected"}, {"owner": ""})
            events.append(("identity_setting", params["owner"]))

    connection = Connection()

    def actual_chain_boundary(config, target):
        assert config.attributes["connection"] is connection
        assert connection.role == "migration_selected"
        assert target == "core@" + revision
        assert config.get_main_option("butlers.target_schema") is None
        events.append("chain")

    monkeypatch.setattr(command, "upgrade", actual_chain_boundary)
    producer._upgrade_chain_to_revision(
        "postgresql://unused/test_selected",
        chain="core",
        schema=None,
        revision=revision,
        connection=connection,
    )
    assert ("source_d13" in events) is staged
    assert events[-3:] == ["normal_owner", "chain", "restored"]
    assert connection.role == "maintenance_selected"
    events.clear()
    connection.valid = False
    with pytest.raises(RuntimeError, match="maintenance/ownership prerequisite"):
        producer._upgrade_chain_to_revision(
            "postgresql://unused/test_selected",
            chain="core",
            schema=None,
            revision=revision,
            connection=connection,
        )
    assert events == []


@pytest.mark.parametrize(("revision", "needs_dashboard"), [("core_170", False), ("core_277", True)])
def test_direct_bounded_online_entrypoint_uses_real_chain_edges(
    monkeypatch, revision, needs_dashboard
):
    """A direct selected Alembic target cannot install a future purpose interface."""
    import runpy
    from contextlib import contextmanager

    from butlers import migrations

    events = []
    connection = MagicMock()
    connection.info = {}
    config = migrations._build_alembic_config("postgresql://unused", ["core"])
    context = MagicMock()
    context.config = config
    context.is_offline_mode.return_value = False
    context.get_revision_argument.return_value = revision

    @contextmanager
    def session(url):
        assert url == "postgresql://unused"
        events.append("held")
        try:
            yield connection
        finally:
            events.append("released")

    @contextmanager
    def ordinary(actual):
        assert actual is connection
        events.append("ordinary")
        yield
        events.append("reset")

    def finalized(actual, *, plans):
        assert actual is connection
        assert plans == (("core", None, revision),)
        events.append("finalized")

    monkeypatch.setattr(migrations, "migration_session", session)
    monkeypatch.setattr(migrations, "ordinary_migration_identity", ordinary)
    monkeypatch.setattr(migrations, "finalize_authority", finalized)
    monkeypatch.setattr(migrations, "_bootstrap_extensions", lambda *a, **k: None)
    monkeypatch.setattr(
        "butlers.db_authority.interfaces.stage_dashboard_interfaces",
        lambda actual: events.append("dashboard") if actual is connection else None,
    )
    monkeypatch.setattr("butlers.bootstrap_prerequisite.check_bootstrap_connection", lambda c: None)
    context.run_migrations.side_effect = lambda: events.append("chain")
    with patch("alembic.context", context):
        runpy.run_path(str(Path(__file__).resolve().parents[2] / "alembic" / "env.py"))
    assert ("dashboard" in events) is needs_dashboard
    assert events[0] == "held" and events[-2:] == ["finalized", "released"]
    assert events.index("ordinary") < events.index("chain") < events.index("reset")


@pytest.mark.parametrize(
    ("current", "target", "refuses", "operation"),
    [
        ("core_281", "core_280", True, "downgrade"),
        ("core_281", "-1", True, "downgrade"),
        ("core_281", "base", True, "downgrade"),
        ("core_281", "core_281", False, "downgrade"),
        ("core_281", "core@head", False, "downgrade"),
        ("core_280", "core_279", False, "downgrade"),
        (("core_281", "mem_014"), "mem_013", False, "downgrade"),
        (("core_281", "mem_013"), "mem_014", False, "upgrade"),
        (("core_281", "mem_014"), "base", True, "downgrade"),
        (("core_281", "unknown_001"), "core_280", "invalid", "downgrade"),
        ("core_281", "unknown_001", "invalid", "downgrade"),
        ("core_281", "head", "invalid", "downgrade"),
    ],
)
def test_actual_alembic_reverse_refuses_before_ordinary_setup(
    monkeypatch, current, target, refuses, operation
):
    """RB03: the real command/env may read stamps, but cannot precommit setup."""
    from types import SimpleNamespace

    from alembic.util import CommandError

    from alembic import command
    from butlers.db_authority.roles import AuthorityError
    from butlers.migrations import _build_alembic_config

    effects = []

    class Driver:
        info = {}

        def execute(self, statement, parameters):
            assert "pg_catalog.to_regclass" in str(statement)
            assert parameters == {"relation": '"general".alembic_version'}
            return SimpleNamespace(scalar_one=lambda: 1)

        def exec_driver_sql(self, sql):
            assert sql == 'SELECT version_num FROM "general".alembic_version'
            revisions = [current] if isinstance(current, str) else list(current)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: revisions))

    def ordinary_setup(connection):
        effects.append("ordinary_setup")
        raise LookupError("synthetic ordinary setup reached")

    monkeypatch.setattr("butlers.bootstrap_prerequisite.check_bootstrap_connection", ordinary_setup)
    config = _build_alembic_config("postgresql://unused", ["core"], target_schema="general")
    config.attributes["connection"] = Driver()
    if refuses == "invalid":
        with pytest.raises(CommandError):
            getattr(command, operation)(config, target)
        assert effects == []
    elif refuses:
        with pytest.raises(AuthorityError, match="Explicit preserving operator reverse"):
            getattr(command, operation)(config, target)
        assert effects == []
    else:
        with pytest.raises(LookupError, match="ordinary setup reached"):
            getattr(command, operation)(config, target)
        assert effects == ["ordinary_setup"]
