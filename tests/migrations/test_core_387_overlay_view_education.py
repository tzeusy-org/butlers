"""core_387 widens calendar.v_overlay_contributions with the education schema.

Static checks pin the contributor set against the read model's list. The integration test runs the
real upgrade and downgrade on a migrated database: an education overlay key surfaces with the
hardcoded ``education`` literal after upgrade, and downgrade restores core_140's four-schema view.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.api.read_models.calendar_workspace import OVERLAY_CONTRIBUTORS
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    load_migration,
    migration_db_name,
)

_CORE = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "core"
_MIGRATION_PATH = _CORE / "core_387_overlay_view_education.py"


@pytest.mark.unit
def test_the_view_contributors_are_the_read_models_contributors():
    mod = load_migration(_MIGRATION_PATH, "core_387")
    assert tuple(sorted(mod._SPECIALIST_SCHEMAS)) == tuple(sorted(OVERLAY_CONTRIBUTORS))
    assert set(mod._SPECIALIST_SCHEMAS) - set(mod._PREVIOUS_SCHEMAS) == {"education"}


@pytest.mark.unit
def test_it_chains_onto_a_core_revision_and_keeps_the_guardrails():
    mod = load_migration(_MIGRATION_PATH, "core_387")
    # Chained onto main's core head when armed; only the family is pinned here.
    assert mod.down_revision.startswith("core_") and mod.down_revision != mod.revision
    source = _MIGRATION_PATH.read_text()
    assert (
        "SELECT '{s}' AS butler, key, value FROM {s}.state WHERE key LIKE '{_KEY_PREFIX}'" in source
    )
    assert mod._KEY_PREFIX == "calendar/overlay/%"
    assert "REVOKE" in source and "GRANT" in source


_ALL = ("education", "finance", "health", "relationship", "travel")


@pytest.mark.integration
def test_education_overlay_keys_surface_after_upgrade_and_leave_on_downgrade(postgres_container):
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    core = _build_alembic_config(db_url, chains=["core"])
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        command.upgrade(core, "core@core_383")
        with engine.connect() as conn:
            for schema in _ALL:
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
                conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {schema}.state "
                        "(key TEXT PRIMARY KEY, value JSONB NOT NULL)"
                    )
                )
            for schema in ("education", "finance"):
                conn.execute(
                    text(f"INSERT INTO {schema}.state (key, value) VALUES (:k, '{{}}'::jsonb)"),
                    {"k": "calendar/overlay/2030-03-04"},
                )
            conn.execute(
                text("INSERT INTO education.state (key, value) VALUES ('other/key', '{}'::jsonb)")
            )

        command.upgrade(core, "core@core_387")
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT butler, key FROM calendar.v_overlay_contributions ORDER BY butler")
            ).fetchall()
            assert rows == [
                ("education", "calendar/overlay/2030-03-04"),
                ("finance", "calendar/overlay/2030-03-04"),
            ]

        command.downgrade(core, "core_383")
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT butler, key FROM calendar.v_overlay_contributions ORDER BY butler")
            ).fetchall()
            assert rows == [("finance", "calendar/overlay/2030-03-04")]
    finally:
        engine.dispose()


@pytest.mark.integration
def test_the_view_keeps_education_until_the_last_schema_downgrades(postgres_container):
    """Core revisions replay per butler schema: one schema stepping back must not drop the view term."""
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    schemas = ("butler_a", "butler_b")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")

    def cfg(schema: str):
        return _build_alembic_config(db_url, chains=["core"], target_schema=schema)

    def view_butlers() -> list[str]:
        with engine.connect() as conn:
            return [
                row[0]
                for row in conn.execute(
                    text("SELECT butler FROM calendar.v_overlay_contributions ORDER BY butler")
                )
            ]

    try:
        with engine.connect() as conn:
            for schema in (*schemas, *_ALL):
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            for schema in _ALL:
                conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {schema}.state "
                        "(key TEXT PRIMARY KEY, value JSONB NOT NULL)"
                    )
                )
                conn.execute(
                    text(f"INSERT INTO {schema}.state (key, value) VALUES (:k, '{{}}'::jsonb)"),
                    {"k": "calendar/overlay/2030-03-04"},
                )
        for schema in schemas:
            command.upgrade(cfg(schema), "core@core_383")
        for schema in schemas:
            command.upgrade(cfg(schema), "core@core_387")
        assert view_butlers() == list(_ALL)

        # One schema steps back; the other still runs core_387, so education stays.
        command.downgrade(cfg("butler_a"), "core@core_383")
        assert view_butlers() == list(_ALL)

        # The last schema leaves: the view returns to the four earlier contributors.
        command.downgrade(cfg("butler_b"), "core@core_383")
        assert view_butlers() == ["finance", "health", "relationship", "travel"]
    finally:
        engine.dispose()
