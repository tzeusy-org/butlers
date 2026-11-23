"""core_367: a later schema's upgrade finds the halted status installed and issues no DDL.

The core chain runs once per butler schema over the one shared ``public.qa_patrols``. Dropping and
re-adding the status check rescans the table even as a no-op, so later schemas read the catalog
under the advisory lock and return, and only the last schema's downgrade removes the status. No
database is needed: the migration's ``op`` is replaced by a recorder.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

_PATH = Path(__file__).resolve().parents[2] / "alembic/versions/core/core_367_qa_patrol_halted.py"


def _load():
    spec = importlib.util.spec_from_file_location("core_367_catalog_skip", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self, installed: bool) -> None:
        self.executed: list[str] = []
        self.catalog_reads: list[str] = []
        self._installed = installed

    def execute(self, statement: str) -> None:
        self.executed.append(str(statement))

    def get_bind(self):
        recorder = self

        class _Bind:
            def exec_driver_sql(self, statement: str):
                recorder.catalog_reads.append(statement)
                return SimpleNamespace(scalar=lambda: recorder._installed)

        return _Bind()


def test_an_installed_status_issues_no_ddl_after_the_lock(monkeypatch) -> None:
    module = _load()
    fake = _Op(installed=True)
    monkeypatch.setattr(module, "op", fake)
    module.upgrade()

    assert fake.executed == [module._LOCK]
    assert fake.catalog_reads == [module._ALREADY_INSTALLED]


def test_a_missing_status_widens_the_check_and_adds_the_column(monkeypatch) -> None:
    module = _load()
    fake = _Op(installed=False)
    monkeypatch.setattr(module, "op", fake)
    module.upgrade()

    assert fake.executed[0] == module._LOCK
    ddl = "\n".join(fake.executed)
    assert "ADD COLUMN IF NOT EXISTS halt_reason TEXT" in ddl
    assert "'halted'" in ddl


def test_the_catalog_check_is_catalog_only_and_has_no_driver_marker() -> None:
    sql = module_sql = _load()._ALREADY_INSTALLED
    assert "pg_constraint" in module_sql and "pg_attribute" in module_sql
    assert "FROM public.qa_patrols" not in sql
    assert "%" not in sql


def test_an_earlier_schema_downgrade_changes_nothing(monkeypatch) -> None:
    module = _load()
    fake = _Op(installed=True)
    monkeypatch.setattr(module, "op", fake)
    monkeypatch.setattr(module, "_installation_count", lambda: 2)
    module.downgrade()
    assert fake.executed == [module._LOCK]


def test_the_last_downgrade_keeps_rows_readable_then_restores_the_old_check(monkeypatch) -> None:
    module = _load()
    fake = _Op(installed=True)
    monkeypatch.setattr(module, "op", fake)
    monkeypatch.setattr(module, "_installation_count", lambda: 1)
    module.downgrade()

    relabel = next(i for i, s in enumerate(fake.executed) if "SET status = 'suppressed'" in s)
    restore = next(i for i, s in enumerate(fake.executed) if "ADD CONSTRAINT" in s)
    lock = next(
        i for i, s in enumerate(fake.executed) if s.startswith("LOCK TABLE public.qa_patrols")
    )
    assert "ACCESS EXCLUSIVE" in fake.executed[lock]
    assert lock < relabel < restore
    assert "'halted'" not in fake.executed[restore]
    assert "DROP COLUMN IF EXISTS halt_reason" in fake.executed[-1]
