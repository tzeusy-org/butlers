"""Smoke tests — ButlerDaemon lifecycle (bu-dl98i.5.4).

Covers three invariants that must hold on every build:

1. start() reaches accepting state — ``_accepting_connections=True``, ``_started_at``
   set, ``daemon.db.pool`` connected.
2. shutdown() is clean — ``_accepting_connections=False``, ``stop_accepting``/``drain``
   called, background tasks cleaned up.
3. Non-fatal module startup failure is isolated — daemon still accepts; failing module
   recorded as ``'failed'``; dependents recorded as ``'cascade_failed'``.

No real LLM CLI is spawned; all infrastructure (DB, migrations, MCP server, spawner) is
mocked so these tests run in milliseconds without Docker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from butlers.daemon import ButlerDaemon
from butlers.modules.base import Module
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _write_butler_toml(
    config_dir: Path,
    modules: dict[str, dict] | None = None,
) -> Path:
    """Write a minimal butler.toml for smoke tests."""
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "[butler]",
        'name = "smoke-butler"',
        "port = 19100",
        'description = "Smoke test butler"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "smoke_butler"',
    ]
    for mod_name in modules or {}:
        lines.append(f"\n[modules.{mod_name}]")
    (config_dir / "butler.toml").write_text("\n".join(lines))
    return config_dir


class _StubConfig(BaseModel):
    """Empty config schema accepted by all stub modules."""


def _make_stub_module(
    name: str,
    *,
    deps: list[str] | None = None,
    fail_on_startup: bool = False,
) -> type[Module]:
    """Return a fresh Module subclass with the given name and behaviour."""
    _name = name
    _deps: list[str] = deps or []
    _fail = fail_on_startup

    class _Stub(Module):
        def __init__(self) -> None:
            self.started = False
            self.shutdown_called = False

        @property
        def name(self) -> str:
            return _name

        @property
        def config_schema(self) -> type[BaseModel]:
            return _StubConfig

        @property
        def dependencies(self) -> list[str]:
            return list(_deps)

        async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
            pass

        def migration_revisions(self) -> str | None:
            return None

        async def on_startup(
            self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
        ) -> None:
            if _fail:
                raise RuntimeError(f"intentional failure in module {_name!r}")
            self.started = True

        async def on_shutdown(self) -> None:
            self.shutdown_called = True

    _Stub.__name__ = f"StubModule_{name}"
    _Stub.__qualname__ = f"StubModule_{name}"
    return _Stub


def _make_mock_pool() -> tuple[Any, Any]:
    """Build a minimal asyncpg pool mock (no real DB)."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])

    pool = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.execute = AsyncMock(return_value=None)
    pool.fetchval = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool, conn


def _make_db_mock(pool: Any) -> MagicMock:
    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=pool)
    mock_db.close = AsyncMock()
    mock_db.pool = pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"
    mock_db.schema = "smoke_butler"
    mock_db.db_schema = "smoke_butler"
    return mock_db


def _make_runtime_config_row() -> dict:
    return {
        "butler_name": "smoke-butler",
        "core_groups": None,
        "max_concurrent": 3,
        "max_queued": 10,
        "seeded_at": None,
        "updated_at": None,
    }


def _build_infra_patches(
    pool: Any,
    mock_db: Any,
    audit_db: Any,
) -> dict[str, Any]:
    """Return the patch dict used to mock all infra during daemon startup."""
    _call_count = 0

    def _db_from_env(db_name: str) -> MagicMock:
        nonlocal _call_count
        _call_count += 1
        # First call is the main butler DB; subsequent calls are audit/shared pools.
        return mock_db if _call_count == 1 else audit_db

    def _fetchrow_side(query: str, *args: Any, **kwargs: Any) -> dict | None:
        if "runtime_config" in query:
            return _make_runtime_config_row()
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow_side)

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    return {
        "db_from_env": patch("butlers.lifecycle.Database.from_env", side_effect=_db_from_env),
        "run_migrations": patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.lifecycle.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "init_telemetry": patch("butlers.lifecycle.init_telemetry"),
        "sync_schedules": patch("butlers.lifecycle.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.lifecycle.FastMCP"),
        "Spawner": patch("butlers.lifecycle.Spawner", return_value=mock_spawner),
        "get_adapter": patch(
            "butlers.lifecycle.get_adapter",
            return_value=type(
                "MockAdapter",
                (),
                {
                    "binary_name": "claude",
                    "__init__": lambda self, **kwargs: None,
                },
            ),
        ),
        "shutil_which": patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "liveness_reporter_loop": patch.object(
            ButlerDaemon, "_liveness_reporter_loop", new_callable=AsyncMock
        ),
        "scheduler_loop": patch.object(ButlerDaemon, "_scheduler_loop", new_callable=AsyncMock),
        # Exposed so callers can run assertions.
        "mock_spawner": mock_spawner,
        "mock_db": mock_db,
    }


async def _start_smoke_daemon(
    config_dir: Path,
    registry: ModuleRegistry | None = None,
) -> tuple[ButlerDaemon, Any, MagicMock]:
    """Start a ButlerDaemon with all infra mocked.

    Returns (daemon, mock_pool, mock_spawner).
    """
    pool, _ = _make_mock_pool()
    mock_db = _make_db_mock(pool)
    audit_db = _make_db_mock(pool)
    patches = _build_infra_patches(pool, mock_db, audit_db)

    with (
        patches["db_from_env"],
        patches["run_migrations"],
        patches["validate_credentials"],
        patches["validate_module_credentials"],
        patches["init_telemetry"],
        patches["sync_schedules"],
        patches["FastMCP"],
        patches["Spawner"],
        patches["get_adapter"],
        patches["shutil_which"],
        patches["start_mcp_server"],
        patches["connect_switchboard"],
        patches["recover_route_inbox"],
        patches["liveness_reporter_loop"],
        patches["scheduler_loop"],
    ):
        kwargs: dict[str, Any] = {}
        if registry is not None:
            kwargs["registry"] = registry
        daemon = ButlerDaemon(config_dir, **kwargs)
        await daemon.start()

    return daemon, pool, patches["mock_spawner"]


# ---------------------------------------------------------------------------
# Smoke test 1: start() reaches accepting state
# ---------------------------------------------------------------------------


async def test_daemon_start_reaches_accepting_state(tmp_path: Path) -> None:
    """start() sets _accepting_connections=True and _started_at.

    These two flags are the operational gate: once set, the daemon is ready to
    accept MCP connections.  Both must be set by the end of start() regardless
    of whether any modules are loaded.
    """
    config_dir = _write_butler_toml(tmp_path / "smoke-butler")
    daemon, _pool, _ = await _start_smoke_daemon(config_dir)

    assert daemon._accepting_connections is True, (
        "Daemon must set _accepting_connections=True at the end of start()"
    )
    assert daemon._started_at is not None, (
        "Daemon must record _started_at monotonic timestamp at the end of start()"
    )
    # Pool must be connected — daemon.db is the interface other components use.
    assert daemon.db is not None, "daemon.db must be set after start()"
    assert daemon.db.pool is not None, "daemon.db.pool must be connected after start()"

    await daemon.shutdown()


# ---------------------------------------------------------------------------
# Smoke test 2: shutdown() is clean
# ---------------------------------------------------------------------------


async def test_daemon_shutdown_is_clean(tmp_path: Path) -> None:
    """shutdown() resets _accepting_connections and cleanly drains sessions.

    Clean shutdown contract:
    - ``_accepting_connections`` set to False (new triggers rejected).
    - ``spawner.stop_accepting()`` called before ``drain()``.
    - Background tasks (scheduler, liveness reporter) cleaned up.
    """
    config_dir = _write_butler_toml(tmp_path / "smoke-butler")
    daemon, _, mock_spawner = await _start_smoke_daemon(config_dir)
    assert daemon._accepting_connections is True  # precondition

    await daemon.shutdown()

    assert daemon._accepting_connections is False, (
        "shutdown() must set _accepting_connections=False"
    )
    mock_spawner.stop_accepting.assert_called_once()
    mock_spawner.drain.assert_awaited_once()
    assert daemon._scheduler_loop_task is None, (
        "scheduler_loop_task must be cleaned up after shutdown"
    )
    assert daemon._liveness_reporter_task is None, (
        "liveness_reporter_task must be cleaned up after shutdown"
    )


# ---------------------------------------------------------------------------
# Smoke test 3: non-fatal module startup failure is isolated
# ---------------------------------------------------------------------------


async def test_non_fatal_module_failure_isolated(tmp_path: Path) -> None:
    """A module whose on_startup() raises does not prevent daemon from accepting.

    Daemon isolation contract:
    - ``_accepting_connections`` reaches True despite the module failure.
    - Failing module is in ``_module_statuses`` with ``status='failed'``.
    - A module that depends on the failing module gets ``status='cascade_failed'``.
    - A healthy, unrelated module is marked ``status='active'``.
    """
    config_dir = _write_butler_toml(
        tmp_path / "smoke-butler",
        modules={"healthy_mod": {}, "failing_mod": {}, "cascading_mod": {}},
    )

    HealthyMod = _make_stub_module("healthy_mod", fail_on_startup=False)
    FailingMod = _make_stub_module("failing_mod", fail_on_startup=True)
    CascadingMod = _make_stub_module("cascading_mod", deps=["failing_mod"], fail_on_startup=False)

    registry = ModuleRegistry()
    registry.register(HealthyMod)
    registry.register(FailingMod)
    registry.register(CascadingMod)

    daemon, _, _ = await _start_smoke_daemon(config_dir, registry=registry)

    # Daemon must still accept connections — module failures are non-fatal.
    assert daemon._accepting_connections is True, (
        "Module failure must be isolated — daemon must still reach accepting state"
    )

    # Failing module is recorded.
    failing_status = daemon._module_statuses.get("failing_mod")
    assert failing_status is not None, "Failing module must appear in _module_statuses"
    assert failing_status.status == "failed", (
        f"Expected 'failed' for failing module, got {failing_status.status!r}"
    )

    # Module depending on the failing module is cascade-failed.
    cascading_status = daemon._module_statuses.get("cascading_mod")
    assert cascading_status is not None, "Cascading module must appear in _module_statuses"
    assert cascading_status.status == "cascade_failed", (
        f"Expected 'cascade_failed' for dependent module, got {cascading_status.status!r}"
    )

    # Healthy, unrelated module remains active.
    healthy_status = daemon._module_statuses.get("healthy_mod")
    assert healthy_status is not None, "Healthy module must appear in _module_statuses"
    assert healthy_status.status == "active", (
        f"Healthy module must stay 'active', got {healthy_status.status!r}"
    )

    await daemon.shutdown()
