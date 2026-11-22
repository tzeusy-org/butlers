"""Daemon startup either runs its Alembic chains or only checks they are at head (bu-dss7he.6).

``BUTLERS_STARTUP_MIGRATIONS=check`` is set by the chart with the migrator login split: the
migrations initContainer owns upgrades, and a daemon connecting as the runtime login (which
owns nothing) only verifies the head and refuses to start when the database is behind.
Unset (or ``run``) keeps today's behaviour exactly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers import lifecycle, migrations
from butlers.migrations import MigrationsNotAtHeadError

pytestmark = pytest.mark.unit


class _Module:
    def __init__(self, name: str) -> None:
        self.name = name


def _daemon() -> SimpleNamespace:
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="general", db_schema="general"),
        _build_db_url=lambda: "postgresql://runtime@db/butlers",
        _modules=[_Module("memory")],
        _module_statuses={},
        _module_configs={},
    )
    daemon._cascade_module_failures = MagicMock()
    return daemon


@pytest.fixture
def chains(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, MagicMock]:
    run = AsyncMock()
    check = MagicMock()
    monkeypatch.setattr(lifecycle, "run_migrations", run)
    monkeypatch.setattr(lifecycle, "check_chain_at_head", check)
    monkeypatch.setattr(lifecycle, "has_butler_chain", lambda name: True)
    monkeypatch.setattr(
        lifecycle, "get_module_migration_chains", lambda modules, name: {"memory": "memory"}
    )
    return run, check


async def _start(daemon: SimpleNamespace) -> None:
    state = lifecycle.StartupState()
    await lifecycle._startup_core_migrations(daemon, state)
    await lifecycle._startup_module_migrations(daemon, state)


@pytest.mark.parametrize("value", [None, "", "run", " run "])
async def test_default_runs_every_chain_and_never_checks(
    monkeypatch: pytest.MonkeyPatch, chains, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(lifecycle.STARTUP_MIGRATIONS_ENV, raising=False)
    else:
        monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, value)
    run, check = chains
    await _start(_daemon())
    assert [c.kwargs["chain"] for c in run.await_args_list] == ["core", "general", "memory"]
    check.assert_not_called()


async def test_check_mode_checks_every_chain_and_never_migrates(
    monkeypatch: pytest.MonkeyPatch, chains
) -> None:
    monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, "check")
    run, check = chains
    await _start(_daemon())
    run.assert_not_awaited()
    assert [(c.args[1], c.args[2]) for c in check.call_args_list] == [
        ("core", "general"),
        ("general", "general"),
        ("memory", "general"),
    ]


async def test_check_mode_refuses_to_start_when_core_is_behind(
    monkeypatch: pytest.MonkeyPatch, chains
) -> None:
    monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, "check")
    _run, check = chains
    check.side_effect = MigrationsNotAtHeadError("core", "general", ["core_001"], ["core_002"])
    with pytest.raises(MigrationsNotAtHeadError, match="core.*not at head"):
        await _start(_daemon())


async def test_check_mode_disables_only_a_module_that_is_behind(
    monkeypatch: pytest.MonkeyPatch, chains
) -> None:
    monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, "check")
    _run, check = chains

    def behind_memory(db_url: str, chain: str, schema: str | None) -> None:
        if chain == "memory":
            raise MigrationsNotAtHeadError("memory", schema, [], ["mem_010"])

    check.side_effect = behind_memory
    daemon = _daemon()
    await _start(daemon)
    status = daemon._module_statuses["memory"]
    assert (status.status, status.phase) == ("failed", "migration")
    assert "not at head" in status.error


async def test_an_unknown_mode_refuses_to_start(monkeypatch: pytest.MonkeyPatch, chains) -> None:
    monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, "skip")
    run, check = chains
    with pytest.raises(ValueError, match="BUTLERS_STARTUP_MIGRATIONS"):
        await _start(_daemon())
    run.assert_not_awaited()
    check.assert_not_called()


def _heads(monkeypatch: pytest.MonkeyPatch, applied: set[str] | None, heads=("core_002",)):
    monkeypatch.setattr(migrations, "_resolve_chain_dir", lambda chain: "dir")
    monkeypatch.setattr(
        migrations,
        "_chain_heads",
        lambda chain, chain_dir: (tuple(heads), frozenset({"core_001", "core_002"})),
    )
    monkeypatch.setattr(migrations, "_applied_revisions", lambda db_url, schema: applied)


def test_check_passes_at_head_and_ignores_other_chains_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _heads(monkeypatch, {"core_002", "general_007"})
    migrations.check_chain_at_head("postgresql://db", "core", "general")


@pytest.mark.parametrize("applied", [set(), {"core_001"}, {"core_001", "core_002"}])
def test_check_raises_a_typed_error_naming_chain_schema_and_revisions(
    monkeypatch: pytest.MonkeyPatch, applied: set[str]
) -> None:
    _heads(monkeypatch, applied)
    with pytest.raises(MigrationsNotAtHeadError) as raised:
        migrations.check_chain_at_head("postgresql://db", "core", "general")
    error = raised.value
    assert (error.chain, error.schema, error.expected) == ("core", "general", ("core_002",))
    assert error.applied == tuple(sorted(applied))
    assert "postgresql://" not in str(error)


def test_check_raises_when_the_version_table_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _heads(monkeypatch, None)
    with pytest.raises(MigrationsNotAtHeadError, match="could not be read"):
        migrations.check_chain_at_head("postgresql://db", "core", None)


def test_check_rejects_an_unknown_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrations, "_resolve_chain_dir", lambda chain: None)
    with pytest.raises(ValueError, match="Unknown migration chain"):
        migrations.check_chain_at_head("postgresql://db", "absent", None)
