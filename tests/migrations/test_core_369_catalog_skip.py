"""core_369: a later schema's upgrade finds the journal installed and issues no DDL (bu-uhbhzq.1).

The core chain runs once per butler schema over the one shared ``connectors.calendar_event_revisions``.
``CREATE OR REPLACE TRIGGER`` and ``CREATE INDEX IF NOT EXISTS`` lock the table even as no-ops,
so the second and later schemas read the catalog under the advisory lock and return. No database
is needed: the migration's ``op`` is replaced by a recorder.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

_PATH = Path(__file__).resolve().parents[2] / (
    "alembic/versions/core/core_369_calendar_event_revisions.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("core_369_catalog_skip", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self, installed: bool = True, *, acl_exact: bool | None = None) -> None:
        self.executed: list[str] = []
        self.catalog_reads: list[str] = []
        self._structure = installed
        self._acl = installed if acl_exact is None else acl_exact
        self.module = None

    def execute(self, statement: str) -> None:
        self.executed.append(str(statement))

    def get_bind(self):
        recorder = self

        class _Bind:
            def exec_driver_sql(self, statement: str):
                recorder.catalog_reads.append(statement)
                answer = (
                    recorder._structure
                    if statement == recorder.module._STRUCTURE_INSTALLED
                    else recorder._acl
                )
                return SimpleNamespace(scalar=lambda: answer)

        return _Bind()


def _patched(monkeypatch, fake: _Op):
    module = _load()
    fake.module = module
    monkeypatch.setattr(module, "op", fake)
    return module


def test_an_installed_journal_issues_no_ddl_after_the_lock(monkeypatch) -> None:
    fake = _Op(installed=True)
    module = _patched(monkeypatch, fake)
    module.upgrade()

    assert fake.executed == [module._LOCK]  # the advisory lock and nothing else
    assert fake.catalog_reads == [module._STRUCTURE_INSTALLED, module._ACL_EXACT]


def test_drifted_grants_on_an_installed_structure_run_only_revoke_then_grant(monkeypatch) -> None:
    """A fresh schema replays core_007's blanket grants: repair the ACL, never replay CREATE."""
    fake = _Op(installed=True, acl_exact=False)
    module = _patched(monkeypatch, fake)
    module.upgrade()

    ddl = "\n".join(fake.executed[1:])
    assert fake.executed[0] == module._LOCK
    for forbidden in ("CREATE", "ALTER", "DROP"):
        assert forbidden not in ddl
    assert ddl.index("REVOKE ALL ON TABLE") < ddl.index("GRANT SELECT, INSERT ON TABLE")


def test_a_missing_journal_is_created(monkeypatch) -> None:
    fake = _Op(installed=False)
    module = _patched(monkeypatch, fake)
    module.upgrade()

    ddl = "\n".join(fake.executed)
    assert fake.executed[0] == module._LOCK
    for fragment in (
        "CREATE" + " TABLE IF NOT EXISTS connectors.calendar_event_revisions",
        "CREATE INDEX IF NOT EXISTS ix_calendar_event_revisions_starts",
        "CREATE OR REPLACE TRIGGER calendar_event_revisions_append_only",
        "CREATE OR REPLACE TRIGGER calendar_event_revisions_no_truncate",
        "GRANT SELECT, INSERT ON TABLE",
        "GRANT SELECT ON TABLE",
    ):
        assert fragment in ddl


def test_the_catalog_check_reads_every_installed_object_and_takes_no_table_lock() -> None:
    module = _load()
    sql = module._STRUCTURE_INSTALLED + module._ACL_EXACT
    for fragment in (
        "calendar_event_revisions_append_only",
        "calendar_event_revisions_no_truncate",
        "ix_calendar_event_revisions_starts",
        "ix_calendar_event_revisions_observed",
        "aclexplode",
        "tgenabled = 'O'",
        "strpos(pg_get_constraintdef(oid), 'baseline') > 0",
        "strpos(pg_get_constraintdef(oid), 'resync') > 0",
    ):
        assert fragment in sql
    # Catalog-only: it never names the table in a FROM clause that would lock it.
    assert "FROM connectors.calendar_event_revisions" not in sql


def test_the_last_downgrade_locks_the_table_before_it_looks_for_rows(monkeypatch) -> None:
    """An uncommitted insert is invisible to EXISTS; the lock must wait it out first."""
    fake = _Op(installed=True)
    module = _patched(monkeypatch, fake)
    monkeypatch.setattr(module, "_installation_count", lambda: 1)
    module.downgrade()

    check = next(s for s in fake.executed if "downgrade refused" in s)
    lock = check.index("LOCK TABLE connectors.calendar_event_revisions IN ACCESS EXCLUSIVE MODE")
    assert lock < check.index("EXISTS (SELECT 1 FROM connectors.calendar_event_revisions)")
    drop = next(i for i, s in enumerate(fake.executed) if s.startswith("DROP TABLE"))
    assert fake.executed.index(check) < drop


def test_an_earlier_schema_downgrade_takes_no_table_lock(monkeypatch) -> None:
    fake = _Op(installed=True)
    module = _patched(monkeypatch, fake)
    monkeypatch.setattr(module, "_installation_count", lambda: 2)
    module.downgrade()
    assert fake.executed == [module._LOCK]
