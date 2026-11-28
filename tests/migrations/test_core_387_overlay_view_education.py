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


def _core(db_url: str, schema: str):
    return _build_alembic_config(db_url, chains=["core"], target_schema=schema)


def _migrated_butlers(postgres_container, schemas: tuple[str, ...]):
    """A real migrated database with each butler schema at core_386 and an overlay key in it."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for schema in schemas:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    for schema in schemas:
        command.upgrade(_core(db_url, schema), "core@core_386")
    with engine.connect() as conn:
        for schema in schemas:
            conn.execute(
                text(f"INSERT INTO {schema}.state (key, value) VALUES (:k, '{{}}'::jsonb)"),
                {"k": "calendar/overlay/2030-03-04"},
            )
        conn.execute(
            text("INSERT INTO education.state (key, value) VALUES ('other/key', '{}'::jsonb)")
        )
    return db_url, engine


def _view_butlers(engine) -> list[str]:
    with engine.connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                text("SELECT butler FROM calendar.v_overlay_contributions ORDER BY butler")
            )
        ]


@pytest.mark.integration
def test_education_overlay_keys_surface_after_upgrade_and_leave_on_downgrade(postgres_container):
    db_url, engine = _migrated_butlers(postgres_container, ("education", "finance"))
    try:
        core = _core(db_url, "education")
        command.upgrade(core, "core@core_387")
        assert _view_butlers(engine) == ["education", "finance"]

        command.downgrade(core, "core@core_386")
        assert _view_butlers(engine) == ["finance"]
    finally:
        engine.dispose()


@pytest.mark.integration
def test_the_view_keeps_education_until_the_last_schema_downgrades(postgres_container):
    """Core revisions replay per butler schema: one schema stepping back must not drop the term."""
    db_url, engine = _migrated_butlers(postgres_container, _ALL)
    try:
        for schema in _ALL:
            command.upgrade(_core(db_url, schema), "core@core_387")
        assert _view_butlers(engine) == list(_ALL)

        # Every schema but travel steps back; travel still runs core_387, so education stays.
        for schema in _ALL[:-1]:
            command.downgrade(_core(db_url, schema), "core@core_386")
            assert _view_butlers(engine) == list(_ALL)

        # The last schema leaves: the view returns to the four earlier contributors.
        command.downgrade(_core(db_url, "travel"), "core@core_386")
        assert _view_butlers(engine) == ["finance", "health", "relationship", "travel"]
    finally:
        engine.dispose()
