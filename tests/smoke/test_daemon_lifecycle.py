"""Smoke tests — ButlerDaemon lifecycle (bu-dl98i.5.4).

Covers three invariants that must hold on every build:

1. start() reaches accepting state — ``_accepting_connections=True``, ``_started_at``
   set, ``daemon.db.pool`` connected.
2. shutdown() is clean — ``_accepting_connections=False``, ``stop_accepting``/``drain``
   called, background tasks cleaned up.
3. Non-fatal module startup failure is isolated — daemon still accepts; failing module
   recorded as ``'failed'``; dependents recorded as ``'cascade_failed'``.

No real LLM CLI is spawned. Invariants 1-3 share one real boot on ``booted_daemon``
(a migrated clone, only the runtime faked); the receipt check runs the readiness
phase on a DB-free host (bu-rvgzmf.2).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

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

        name = _name

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


def _make_mock_pool(*, boot_receipt: object = 1) -> tuple[Any, Any]:
    """Build a minimal asyncpg pool mock (no real DB)."""
    conn = AsyncMock()
    transaction_depth = 0

    async def enter_transaction():
        nonlocal transaction_depth
        transaction_depth += 1

    async def exit_transaction(*_exc):
        nonlocal transaction_depth
        transaction_depth -= 1
        return False

    conn.is_in_transaction = MagicMock(side_effect=lambda: transaction_depth > 0)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(side_effect=enter_transaction)
    transaction.__aexit__ = AsyncMock(side_effect=exit_transaction)
    conn.transaction = MagicMock(return_value=transaction)
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])

    pool = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.execute = AsyncMock(return_value=None)

    async def _fetchval(query: str, *_args: Any) -> object:
        if "public.register_butler_boot" in query:
            return boot_receipt
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool, conn


# ---------------------------------------------------------------------------
# Smoke tests 1-3: one real boot (bu-rvgzmf.2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_real_boot_accepts_isolates_module_failures_and_shuts_down_clean(
    booted_daemon, tmp_path: Path
) -> None:
    """A registered butler with one failing module boots, accepts, and shuts down clean.

    - start() reaches accepting state: a committed boot epoch, ``_started_at`` set,
      the pool connected.
    - A failing module is ``failed``, its dependant ``cascade_failed``, a healthy one
      ``active``, and the daemon still accepts.
    - shutdown() stops accepting and clears the scheduler, liveness and MCP server
      tasks.
    """
    from butlers.testing.daemon import write_butler_config

    config_dir = write_butler_config(
        tmp_path,
        "general",
        extra_toml="[modules.healthy_mod]\n\n[modules.failing_mod]\n\n[modules.cascading_mod]\n",
    )
    registry = ModuleRegistry()
    registry.register(_make_stub_module("healthy_mod"))
    registry.register(_make_stub_module("failing_mod", fail_on_startup=True))
    registry.register(_make_stub_module("cascading_mod", deps=["failing_mod"]))

    booted = await booted_daemon(config_dir, registry=registry, registered=True)
    daemon = booted.daemon

    assert isinstance(daemon._boot_epoch, int) and daemon._boot_epoch > 0
    assert daemon._accepting_connections is True
    assert daemon._started_at is not None
    assert await daemon.db.pool.fetchval("SELECT 1") == 1
    statuses = {name: status.status for name, status in daemon._module_statuses.items()}
    assert statuses == {
        "healthy_mod": "active",
        "failing_mod": "failed",
        "cascading_mod": "cascade_failed",
    }
    assert daemon._server_task is not None

    await booted.shutdown()

    assert daemon._accepting_connections is False
    assert daemon._scheduler_loop_task is None
    assert daemon._liveness_reporter_task is None
    assert daemon._server_task is None


# ---------------------------------------------------------------------------
# Smoke test 4: an invalid boot receipt never authorizes routes
# ---------------------------------------------------------------------------


async def test_invalid_boot_receipt_keeps_daemon_non_accepting(tmp_path: Path) -> None:
    """A missing, non-positive, or boolean receipt cannot authorize routes.

    The daemon's own readiness phase and receipt check run on a DB-free host whose
    pool answers ``public.register_butler_boot`` with each receipt.
    """
    import butlers.lifecycle as lifecycle
    from butlers.testing.tool_host import host_tools

    for index, receipt in enumerate((None, 0, True)):
        config_dir = _write_butler_toml(tmp_path / f"invalid-receipt-{index}")
        pool, _ = _make_mock_pool(boot_receipt=receipt)
        daemon = (await host_tools(config_dir, pool=pool)).daemon
        daemon._liveness_reporter_loop = AsyncMock()
        daemon._retry_boot_registration = AsyncMock()
        state = lifecycle.StartupState()
        state.pool = pool
        await lifecycle._startup_readiness(daemon, state)
        try:
            assert any(
                "public.register_butler_boot" in call.args[0]
                for call in pool.fetchval.await_args_list
            )
            assert daemon._boot_epoch is None
            assert daemon._accepting_connections is False
            assert daemon._identity_facts()["accepting_routes"] is False
            assert daemon._boot_registration_task is not None
        finally:
            for task in (daemon._boot_registration_task, daemon._liveness_reporter_task):
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
