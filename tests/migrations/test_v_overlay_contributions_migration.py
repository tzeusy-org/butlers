"""Tests for core_140 calendar.v_overlay_contributions cross-schema view.

Static checks verify the migration file structure, revision chain, and SQL
content.  The integration test exercises the real upgrade against a migrated
database: the view exists and is queryable from the ``calendar`` schema, unions
the four contributing specialist ``state`` tables filtered to
``calendar/overlay/%`` with a hardcoded ``butler`` literal per term, returns
zero rows before any contribution is written (empty-when-none), is not updatable
(UNION view — INSERT fails), and ``downgrade()`` drops it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_140_v_overlay_contributions.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_140", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Static structure checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_migration_file_exists():
    assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"


@pytest.mark.unit
def test_revision_chain():
    mod = _load_migration()
    assert mod.revision == "core_140"
    assert mod.down_revision == "core_139"
    assert mod.branch_labels is None


@pytest.mark.unit
def test_contributing_set_is_the_four_specialists():
    mod = _load_migration()
    assert tuple(sorted(mod._SPECIALIST_SCHEMAS)) == (
        "finance",
        "health",
        "relationship",
        "travel",
    )


@pytest.mark.unit
def test_view_and_role_identity():
    mod = _load_migration()
    assert mod._VIEW_FQN == "calendar.v_overlay_contributions"
    assert mod._CALENDAR_SCHEMA == "calendar"
    assert mod._CALENDAR_ROLE == "butler_calendar_rw"
    assert mod._KEY_PREFIX == "calendar/overlay/%"


@pytest.mark.unit
def test_upgrade_unions_key_filtered_state_with_hardcoded_literal():
    source = _MIGRATION_PATH.read_text()
    assert "CREATE OR REPLACE VIEW {_VIEW_FQN}" in source
    assert "calendar/overlay/%" in source
    # Hardcoded per-term butler literal (guardrail #2), not from payload.
    assert "SELECT '{schema}' AS butler, key, value " in source
    # Optional-schema guard contract reused from core_063.
    assert "to_regclass" in source
    assert "NULL::text AS butler, NULL::text AS key, NULL::jsonb AS value " in source
    # Reader role provisioned + granted (guardrail #5).
    assert "butler_calendar_rw" in source


@pytest.mark.unit
def test_downgrade_drops_view_and_revokes_grants():
    source = _MIGRATION_PATH.read_text()
    assert "DROP VIEW IF EXISTS {_VIEW_FQN}" in source
    assert "REVOKE SELECT ON TABLE" in source


# ---------------------------------------------------------------------------
# Integration round-trip
# ---------------------------------------------------------------------------


def _view_exists(db_url: str) -> bool:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return bool(
                conn.execute(
                    text("SELECT to_regclass('calendar.v_overlay_contributions')")
                ).scalar()
            )
    finally:
        engine.dispose()


_CONTRIBUTING = ("finance", "health", "relationship", "travel")


@pytest.mark.integration
def test_overlay_view_roundtrip_empty_and_not_updatable(postgres_container):
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)

    core = _build_alembic_config(db_url, chains=["core"])
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")

    try:
        # Upgrade to core head (includes core_140).  On a core-only DB the
        # specialist ``state`` tables don't exist yet, so the view is created
        # via the NULL-returning stub UNION term (absent-specialist guard).
        command.upgrade(core, "core@head")
        assert _view_exists(db_url), "overlay view should exist after upgrade"

        with engine.connect() as conn:
            # Empty-when-none: zero rows before any contribution is written
            # (and proves the stub term is queryable, not an error).
            count = conn.execute(
                text("SELECT count(*) FROM calendar.v_overlay_contributions")
            ).scalar()
            assert count == 0, "view must be empty before any overlay contribution exists"

        # Provision the specialist ``state`` tables, then rebuild the view by
        # re-running the migration step so it picks up the now-present tables
        # (real UNION terms instead of the stub).
        command.downgrade(core, "core_139")
        with engine.connect() as conn:
            for schema in _CONTRIBUTING:
                conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
                conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {schema}.state "
                        "(key TEXT PRIMARY KEY, value JSONB NOT NULL)"
                    )
                )
        command.upgrade(core, "core@head")

        with engine.connect() as conn:
            # Write overlay + non-overlay keys into two specialists.
            conn.execute(
                text(
                    "INSERT INTO finance.state (key, value) "
                    "VALUES ('calendar/overlay/2026-06-21', :v)"
                ),
                {"v": '{"butler": "finance", "has_entries": false, "entries": []}'},
            )
            conn.execute(
                text(
                    "INSERT INTO finance.state (key, value) "
                    "VALUES ('briefing/daily/2026-06-21', :v)"
                ),
                {"v": "{}"},
            )
            conn.execute(
                text(
                    "INSERT INTO travel.state (key, value) "
                    "VALUES ('calendar/overlay/2026-06-22', :v)"
                ),
                {"v": '{"butler": "travel"}'},
            )

            # Guardrail #3 (key filter) + #2 (hardcoded source literal): only
            # ``calendar/overlay/%`` keys surface, attributed by the per-term
            # literal, not the payload.
            rows = conn.execute(
                text(
                    "SELECT butler, key FROM calendar.v_overlay_contributions ORDER BY butler, key"
                )
            ).fetchall()
            assert rows == [
                ("finance", "calendar/overlay/2026-06-21"),
                ("travel", "calendar/overlay/2026-06-22"),
            ], "only overlay-prefixed keys surface, with the hardcoded butler literal"

            # Guardrail #1: UNION view is not updatable.
            with pytest.raises(Exception):  # noqa: B017 - DB raises a generic error
                conn.execute(
                    text(
                        "INSERT INTO calendar.v_overlay_contributions (butler, key, value) "
                        "VALUES ('finance', 'calendar/overlay/2026-06-23', '{}'::jsonb)"
                    )
                )
    finally:
        engine.dispose()

    # Downgrade one step: the view is dropped.
    command.downgrade(core, "core_139")
    assert not _view_exists(db_url), "overlay view should be dropped on downgrade"
