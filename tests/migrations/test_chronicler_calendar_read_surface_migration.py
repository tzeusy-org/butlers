"""Static contract tests for Chronicler's calendar read-surface grants."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = pytest.mark.unit

docker_available = shutil.which("docker") is not None

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "chronicler"
    / "migrations"
    / "026_grant_calendar_read_surface.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("chronicler_026", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _executed_sql(function_name: str) -> str:
    module = _load_migration()
    operation = MagicMock()
    with patch.object(module, "op", operation):
        getattr(module, function_name)()
    return "\n".join(str(call.args[0]) for call in operation.execute.call_args_list)


def test_revision_chain_and_calendar_surface_allowlist() -> None:
    module = _load_migration()

    assert module.revision == "chronicler_026"
    assert module.down_revision == "chronicler_025"
    assert module.branch_labels is None
    assert module.depends_on is None
    assert set(module._CALENDAR_READ_SURFACE_TABLES) == {
        "calendar_event_instances",
        "calendar_events",
        "calendar_sources",
        "calendar_event_entities",
    }


def test_upgrade_grants_each_calendar_table_only_when_present() -> None:
    sql = _executed_sql("upgrade")

    assert "information_schema.tables" in sql
    assert "GRANT SELECT ON TABLE" in sql
    assert "GRANT SELECT ON ALL TABLES" not in sql
    for table in (
        "calendar_event_instances",
        "calendar_events",
        "calendar_sources",
        "calendar_event_entities",
    ):
        assert f"table_name = '{table}'" in sql
        assert f'GRANT SELECT ON TABLE "education"."{table}"' in sql


def test_downgrade_revokes_only_the_explicit_calendar_grants() -> None:
    sql = _executed_sql("downgrade")

    assert "information_schema.tables" in sql
    assert "REVOKE SELECT ON ALL TABLES" not in sql
    for table in ("calendar_events", "calendar_sources", "calendar_event_entities"):
        assert f'REVOKE SELECT ON TABLE "education"."{table}"' in sql
    assert 'REVOKE SELECT ON TABLE "education"."calendar_event_instances"' not in sql


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_chronicler_026_grants_effective_select_to_the_runtime_role(postgres_container) -> None:
    """Run the migration chain and prove the grants through the runtime role.

    The calendar tables are privilege-only grant targets in this fixture; this
    test does not query their application shape.  The important contract is
    that ``chronicler_026`` grants the restricted role access to the four
    declared tables and does not widen it to an unrelated calendar table.
    """
    db_url = create_migration_db(postgres_container, migration_db_name())
    calendar_tables = (
        "calendar_event_instances",
        "calendar_events",
        "calendar_sources",
        "calendar_event_entities",
    )

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            # schema-standin-exempt: privilege-only grant targets; column shape
            # is irrelevant to this effective-role ACL test.
            for table in (*calendar_tables, "calendar_sync_cursors"):
                conn.exec_driver_sql(f'CREATE TABLE general."{table}" (id INTEGER)')
    finally:
        engine.dispose()

    from butlers.migrations import run_migrations

    asyncio.run(run_migrations(db_url, chain="chronicler", schema="chronicler"))

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql('SET ROLE "butler_chronicler_rw"')
            assert conn.exec_driver_sql("SELECT current_user").scalar_one() == (
                "butler_chronicler_rw"
            )

            for table in calendar_tables:
                qualified_name = f"general.{table}"
                can_select = conn.execute(
                    text("SELECT has_table_privilege(current_user, :table_name, 'SELECT')"),
                    {"table_name": qualified_name},
                ).scalar_one()
                assert can_select is True, (
                    f"butler_chronicler_rw must have effective SELECT on {qualified_name}"
                )
                assert (
                    conn.exec_driver_sql(f'SELECT count(*) FROM general."{table}"').scalar_one()
                    == 0
                )

            denied = conn.execute(
                text("SELECT has_table_privilege(current_user, :table_name, 'SELECT')"),
                {"table_name": "general.calendar_sync_cursors"},
            ).scalar_one()
            assert denied is False
            with pytest.raises(ProgrammingError, match="permission denied"):
                conn.exec_driver_sql('SELECT count(*) FROM general."calendar_sync_cursors"')
    finally:
        engine.dispose()
