"""sw_042: the Decision Desk prompt and intent tables round-trip cleanly.

bu-ckkpz.3. Real PostgreSQL. Behavioural invariants (one live intent per bead,
reason iff failed) are covered against the migrated schema in
``tests/core/test_decision_desk.py``; this file owns upgrade and downgrade.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_TABLES = ("decision_prompts", "decision_intents")


def _tables(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'switchboard' AND table_name = ANY(:names)"
            ),
            {"names": list(_TABLES)},
        )
        return {row[0] for row in rows}


def test_sw_042_upgrade_downgrade_round_trip(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, ["switchboard"], target_schema="switchboard")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        command.upgrade(config, "switchboard@sw_042")
        assert _tables(engine) == set(_TABLES)
        with engine.connect() as conn:
            index = conn.execute(
                text(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = 'switchboard' "
                    "AND indexname = 'uq_decision_intents_live_bead'"
                )
            ).scalar_one()
        assert "UNIQUE" in index
        assert "pending" in index and "applied" in index and "failed" not in index

        command.downgrade(config, "switchboard@sw_041")
        assert _tables(engine) == set()

        command.upgrade(config, "switchboard@sw_042")
        assert _tables(engine) == set(_TABLES)
    finally:
        engine.dispose()
