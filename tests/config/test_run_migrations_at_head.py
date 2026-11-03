"""``run_migrations`` skips Alembic when the chain is already at head."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, text

import butlers.migrations as migrations
from butlers.migrations import get_chain_head, run_migrations

pytestmark = pytest.mark.integration


@pytest.fixture
def upgrades(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record each Alembic upgrade ``run_migrations`` starts, then run it."""
    calls: list[str] = []
    real_upgrade = migrations.command.upgrade

    def _upgrade(config, revision, *args, **kwargs):
        calls.append(revision)
        return real_upgrade(config, revision, *args, **kwargs)

    monkeypatch.setattr(migrations.command, "upgrade", _upgrade)
    return calls


def _applied(db_url: str, schema: str | None) -> set[str]:
    table = f'"{schema}".alembic_version' if schema else "alembic_version"
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return {row[0] for row in conn.execute(text(f"SELECT version_num FROM {table}"))}
    finally:
        engine.dispose()


def _one_behind_head(chain: str) -> str:
    head = migrations._chain_script_directory(chain).get_revision(get_chain_head(chain))
    assert isinstance(head.down_revision, str)
    return head.down_revision


@pytest.mark.parametrize("schema", ["general", None])
def test_at_head_no_upgrade_runs(fresh_db, upgrades, schema, monkeypatch) -> None:
    url = fresh_db("core", schemas={"core": schema} if schema else None)
    admitted: list[str] = []
    real_admission = migrations.check_bootstrap_database

    def _admission(db_url: str) -> None:
        admitted.append(db_url)
        real_admission(db_url)

    monkeypatch.setattr(migrations, "check_bootstrap_database", _admission)
    upgrades.clear()  # template builds in this process also upgrade
    asyncio.run(run_migrations(url, chain="core", schema=schema))
    assert upgrades == []
    # Bootstrap admission (REQ-database-security-011) still runs at head.
    assert admitted == [url]
    assert get_chain_head("core") in _applied(url, schema)


@pytest.mark.parametrize("schema", ["general", None])
def test_one_revision_behind_still_upgrades(fresh_db, upgrades, schema) -> None:
    behind = _one_behind_head("core")
    url = fresh_db("core", schemas={"core": schema} if schema else None, revisions={"core": behind})
    assert behind in _applied(url, schema)
    upgrades.clear()  # template builds in this process also upgrade
    asyncio.run(run_migrations(url, chain="core", schema=schema))
    assert upgrades == ["core@head"]
    assert get_chain_head("core") in _applied(url, schema)


def test_unmigrated_database_still_upgrades(fresh_db, upgrades) -> None:
    # The core template exists; the switchboard chain has never run on this clone.
    url = fresh_db("core", schemas={"core": "switchboard"})
    upgrades.clear()  # template builds in this process also upgrade
    asyncio.run(run_migrations(url, chain="switchboard", schema="switchboard"))
    assert upgrades == ["switchboard@head"]
    assert get_chain_head("switchboard") in _applied(url, "switchboard")


def test_at_head_check_sees_a_new_revision_file(fresh_db, upgrades, monkeypatch) -> None:
    """A cached head must not outlive the chain's files changing on disk."""
    url = fresh_db("core", schemas={"core": "general"})
    upgrades.clear()  # template builds in this process also upgrade
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    assert upgrades == []
    monkeypatch.setattr(migrations, "_chain_files_signature", lambda chain_dir: ("changed",))
    real_heads = migrations._chain_heads_uncached

    def _moved_head(chain: str):
        _heads, revisions = real_heads(chain)
        return ("core_new_head",), revisions | {"core_new_head"}

    monkeypatch.setattr(migrations, "_chain_heads_uncached", _moved_head)
    # The database no longer matches the (new) head, so the real upgrade runs; on the
    # real files it is a no-op.
    asyncio.run(run_migrations(url, chain="core", schema="general"))
    assert upgrades == ["core@head"]
