"""core_368 takes its advisory lock first and issues DDL only where a schema needs it (bu-5ii4gj.1).

The core chain runs once per butler schema, and the foreign key locks the shared
``public.entities`` each time, so the whole upgrade is serialized and an installed schema is a
catalog-only no-op. No database is needed: the migration's ``op`` is replaced by a recorder.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

_PATH = Path(__file__).resolve().parents[2] / (
    "alembic/versions/core/core_368_calendar_source_subject.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("core_368_catalog_skip", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self, *, table=True, column=None, constraints=()) -> None:
        self.executed: list[str] = []
        self.table, self.column, self.constraints = table, column, list(constraints)

    def execute(self, statement) -> None:
        self.executed.append(str(statement))

    def get_bind(self):
        recorder = self

        class _Bind:
            def execute(self, clause, params=None):
                sql = str(clause)
                if "to_regclass('calendar_sources') IS NOT NULL" in sql:
                    return SimpleNamespace(scalar_one=lambda: recorder.table)
                if "information_schema.columns" in sql:
                    return SimpleNamespace(fetchone=lambda: recorder.column)
                return SimpleNamespace(fetchall=lambda: [(name,) for name in recorder.constraints])

        return _Bind()


def _run(monkeypatch, **state) -> tuple[object, list[str]]:
    module = _load()
    fake = _Op(**state)
    monkeypatch.setattr(module, "op", fake)
    module.upgrade()
    # The reap's pin function is replaced every run (idempotent, no table lock); the tests below
    # are about the table DDL.
    pin = {module._PINNED_DDL, *module._PINNED_GRANTS}
    return module, [statement for statement in fake.executed if statement not in pin]


def test_the_lock_is_the_first_statement_and_covers_the_catalog_read(monkeypatch) -> None:
    module, executed = _run(monkeypatch, column=None)
    assert executed[0] == module._LOCK
    assert "pg_advisory_xact_lock" in module._LOCK


def test_a_schema_without_the_table_issues_nothing_but_the_lock(monkeypatch) -> None:
    module, executed = _run(monkeypatch, table=False)
    assert executed == [module._LOCK]


def test_an_installed_schema_runs_no_alter(monkeypatch) -> None:
    module = _load()
    _, executed = _run(monkeypatch, column=("uuid", "YES"), constraints=(module._FK, module._CHECK))
    assert executed == [module._LOCK]
    assert not any("ALTER" in statement for statement in executed)


def test_a_missing_column_gets_one_alter_with_the_column_and_both_constraints(monkeypatch) -> None:
    module, executed = _run(monkeypatch, column=None)
    [alter] = [s for s in executed if "ALTER TABLE" in s]
    assert "ADD COLUMN role_subject_entity_id UUID" in alter
    assert module._FK in alter and module._CHECK in alter


def test_a_present_column_missing_a_constraint_adds_only_that_constraint(monkeypatch) -> None:
    module = _load()
    _, executed = _run(monkeypatch, column=("uuid", "YES"), constraints=(module._FK,))
    [alter] = [s for s in executed if "ALTER TABLE" in s]
    assert "ADD COLUMN" not in alter and module._CHECK in alter and module._FK not in alter


def test_every_run_replaces_the_pin_function_for_the_enrichment_reap(monkeypatch) -> None:
    module = _load()
    fake = _Op(table=False)
    monkeypatch.setattr(module, "op", fake)
    module.upgrade()
    assert fake.executed[0] == module._LOCK
    assert module._PINNED_DDL in fake.executed
    assert "SECURITY DEFINER" in module._PINNED_DDL and "FROM PUBLIC" in module._PINNED_GRANTS[0]
    assert "TO butler_relationship_rw" in module._PINNED_GRANTS[1]
