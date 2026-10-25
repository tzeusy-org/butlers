"""core_254: qa_patrols records the fleet-condition handoff mode (bu-vfobja).

Real PostgreSQL: the column is nullable with no backfill, a second schema's
core run is a no-op, and downgrade/upgrade drop and restore it.
"""

from __future__ import annotations

import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    migrate_test_database,
    migration_db_name,
    run_test_alembic_command,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_COLUMN_SQL = """
SELECT is_nullable FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'qa_patrols'
  AND column_name = 'fleet_condition_handoff'
"""


def _scalar(db_url: str, statement: str):
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            return conn.execute(text(statement)).scalar()
    finally:
        engine.dispose()


def test_core_254_adds_a_nullable_unbackfilled_column_idempotently(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, ["core"])
    run_test_alembic_command(postgres_container, command.upgrade, config, "core_253")
    assert _scalar(db_url, _COLUMN_SQL) is None
    legacy_id = _scalar(
        db_url, "INSERT INTO public.qa_patrols (status) VALUES ('clean') RETURNING id::text"
    )

    run_test_alembic_command(postgres_container, command.upgrade, config, "core_254")
    assert _scalar(db_url, _COLUMN_SQL) == "YES"
    assert (
        _scalar(
            db_url,
            f"SELECT fleet_condition_handoff IS NULL FROM public.qa_patrols "
            f"WHERE id = '{legacy_id}'",
        )
        is True
    )

    # The core chain replays per butler schema against the shared public table.
    migrate_test_database(postgres_container, db_url, plans=(("core", "general", None),))
    assert _scalar(db_url, _COLUMN_SQL) == "YES"

    run_test_alembic_command(postgres_container, command.downgrade, config, "core_253")
    assert _scalar(db_url, _COLUMN_SQL) is None
    run_test_alembic_command(postgres_container, command.upgrade, config, "core_254")
    assert _scalar(db_url, _COLUMN_SQL) == "YES"
