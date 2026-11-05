"""Kit fixtures drop their clones at teardown (bu-rvgzmf.7).

A shard runs thousands of tests on one Postgres container; clones kept until the
session ends filled its disk and failed unrelated tests with ``DiskFull``.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, text

from butlers.testing.daemon import DaemonBooter, write_butler_config
from butlers.testing.tool_host import ToolHosts

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _exists(postgres_container, name: str) -> bool:
    engine = create_engine(postgres_container.get_connection_url())
    try:
        with engine.connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            return found.scalar() is not None
    finally:
        engine.dispose()


async def _database_exists(postgres_container, name: str) -> bool:
    return await asyncio.to_thread(_exists, postgres_container, name)


async def test_closing_the_hosts_drops_their_clones(postgres_container, tmp_path) -> None:
    hosts = ToolHosts(postgres_container)
    host = await hosts(write_butler_config(tmp_path, "general"))
    name = host.daemon.db.db_name
    assert await _database_exists(postgres_container, name)

    await hosts.close()

    assert not await _database_exists(postgres_container, name)


async def test_closing_the_booter_drops_its_clones(
    postgres_container, monkeypatch, tmp_path
) -> None:
    booter = DaemonBooter(postgres_container, monkeypatch, tmp_path / "booter")
    booted = await booter.boot(write_butler_config(tmp_path, "general"))
    name = booted.daemon.db.db_name
    assert await _database_exists(postgres_container, name)

    await booter.close()

    assert not await _database_exists(postgres_container, name)
