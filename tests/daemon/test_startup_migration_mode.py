"""Daemon startup either runs its Alembic chains or only checks they are at head (bu-dss7he.6).

``BUTLERS_STARTUP_MIGRATIONS=check`` is set by the chart with the migrator login split: the
migrations initContainer owns upgrades, and a daemon connecting as the runtime login (which
owns nothing) only verifies the head and refuses to start when the database is behind.
Unset (or ``run``) keeps today's behaviour exactly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from butlers import lifecycle, migrations
from butlers.migrations import MigrationsNotAtHeadError

pytestmark = pytest.mark.unit


class _Module:
    def __init__(self, name: str) -> None:
        self.name = name


class _Migrated(Exception):
    """Raised where the real ``run_migrations`` would start touching the database."""


def _daemon() -> SimpleNamespace:
    daemon = SimpleNamespace(
        config=SimpleNamespace(name="switchboard", db_schema="switchboard"),
        _build_db_url=lambda: "postgresql://runtime@db/butlers",
        _modules=[_Module("memory")],
        _module_statuses={},
        _module_configs={},
    )
    daemon._cascade_module_failures = MagicMock()
    return daemon


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Fake chain heads and version-table rows inside ``butlers.migrations`` only.

    The lifecycle itself is never patched (scripts/check_test_boot_patches.py): the real
    ``run_migrations`` stops at its first database step, and the real
    ``check_chain_at_head`` reads these fakes.
    """
    state: dict = {"applied": {"core_head", "switchboard_head", "memory_head"}, "reads": []}

    def migrated(db_url: str) -> None:
        raise _Migrated

    def applied(db_url: str, schema: str | None) -> set[str]:
        state["reads"].append(schema)
        return set(state["applied"])

    monkeypatch.setattr(migrations, "check_bootstrap_database", migrated)
    monkeypatch.setattr(
        migrations,
        "_chain_heads",
        lambda chain, chain_dir: ((f"{chain}_head",), frozenset({f"{chain}_old", f"{chain}_head"})),
    )
    monkeypatch.setattr(migrations, "_applied_revisions", applied)
    return state


async def _start(daemon: SimpleNamespace) -> None:
    state = lifecycle.StartupState()
    await lifecycle._startup_core_migrations(daemon, state)
    await lifecycle._startup_module_migrations(daemon, state)


@pytest.mark.parametrize("value", [None, "", "run", " run "])
async def test_default_migrates_as_before(
    monkeypatch: pytest.MonkeyPatch, catalog: dict, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(lifecycle.STARTUP_MIGRATIONS_ENV, raising=False)
    else:
        monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, value)
    with pytest.raises(_Migrated):
        await _start(_daemon())
    assert catalog["reads"] == []


async def test_check_mode_reads_every_chain_and_never_migrates(
    monkeypatch: pytest.MonkeyPatch, catalog: dict
) -> None:
    monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, "check")
    daemon = _daemon()
    await _start(daemon)
    assert catalog["reads"] == ["switchboard", "switchboard", "switchboard"]
    assert daemon._module_statuses == {}


async def test_check_mode_refuses_to_start_when_core_is_behind(
    monkeypatch: pytest.MonkeyPatch, catalog: dict
) -> None:
    monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, "check")
    catalog["applied"] = {"core_old", "switchboard_head", "memory_head"}
    with pytest.raises(MigrationsNotAtHeadError, match="core.*not at head"):
        await _start(_daemon())


async def test_check_mode_disables_only_a_module_that_is_behind(
    monkeypatch: pytest.MonkeyPatch, catalog: dict
) -> None:
    monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, "check")
    catalog["applied"] = {"core_head", "switchboard_head"}
    daemon = _daemon()
    await _start(daemon)
    status = daemon._module_statuses["memory"]
    assert (status.status, status.phase) == ("failed", "migration")
    assert "not at head" in status.error


async def test_an_unknown_mode_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, catalog: dict
) -> None:
    monkeypatch.setenv(lifecycle.STARTUP_MIGRATIONS_ENV, "skip")
    with pytest.raises(ValueError, match="BUTLERS_STARTUP_MIGRATIONS"):
        await _start(_daemon())
    assert catalog["reads"] == []


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
