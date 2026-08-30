"""Static contract tests for Chronicler's calendar read-surface grants."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

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
