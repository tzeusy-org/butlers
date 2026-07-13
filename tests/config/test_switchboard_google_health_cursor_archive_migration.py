"""Regression tests for Google Health cursor-row archival (sw_026)."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.testing.migration import create_migration_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _prepare_pre_migration_db(postgres_container) -> str:
    from butlers.migrations import _build_alembic_config, run_migrations

    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.upgrade(config, "switchboard@sw_025")
    return db_url


def test_migration_archives_cursor_rows_but_not_account_heartbeat(postgres_container) -> None:
    from butlers.migrations import _build_alembic_config

    db_url = _prepare_pre_migration_db(postgres_container)
    account_identity = "google_health:user:account@example.invalid"
    cursor_identity = f"{account_identity}:11111111-2222-3333-4444-555555555555:activity"

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO connector_registry (connector_type, endpoint_identity) "
                    "VALUES ('google_health', :account), ('google_health', :cursor)"
                ),
                {"account": account_identity, "cursor": cursor_identity},
            )

        config = _build_alembic_config(db_url, chains=["switchboard"])
        command.upgrade(config, "switchboard@sw_026")

        with engine.connect() as conn:
            rows = dict(
                conn.execute(
                    text(
                        "SELECT endpoint_identity, archived_at FROM connector_registry "
                        "WHERE connector_type = 'google_health'"
                    )
                ).all()
            )
    finally:
        engine.dispose()

    assert rows[cursor_identity] is not None
    assert rows[account_identity] is None
