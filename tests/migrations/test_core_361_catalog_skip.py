"""core_361: a later schema's upgrade finds the journal installed and issues no DDL (bu-uhbhzq.4).

The core chain runs once per butler schema over the one shared ``connectors.evidence_journal``.
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
    "alembic/versions/core/core_361_connector_evidence_journal.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("core_361_catalog_skip", _PATH)
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


def test_an_installed_journal_issues_no_ddl_after_the_lock(monkeypatch) -> None:
    module = _load()
    fake = _Op(installed=True)
    monkeypatch.setattr(module, "op", fake)
    module.upgrade()

    assert fake.executed == [module._LOCK]  # the advisory lock and nothing else
    assert fake.catalog_reads == [module._ALREADY_INSTALLED]


def test_a_missing_journal_is_created(monkeypatch) -> None:
    module = _load()
    fake = _Op(installed=False)
    monkeypatch.setattr(module, "op", fake)
    module.upgrade()

    ddl = "\n".join(fake.executed)
    assert fake.executed[0] == module._LOCK
    for fragment in (
        "CREATE TABLE IF NOT EXISTS connectors.evidence_journal",
        "CREATE INDEX IF NOT EXISTS ix_evidence_journal_source_observed",
        "CREATE OR REPLACE TRIGGER evidence_journal_append_only",
        "CREATE OR REPLACE TRIGGER evidence_journal_no_truncate",
        "GRANT SELECT, INSERT ON TABLE",
    ):
        assert fragment in ddl


def test_the_catalog_check_reads_every_installed_object_and_takes_no_table_lock() -> None:
    sql = _load()._ALREADY_INSTALLED
    for fragment in (
        "evidence_journal_append_only",
        "evidence_journal_no_truncate",
        "ix_evidence_journal_source_observed",
        "ix_evidence_journal_idempotency_key",
        "aclexplode",
    ):
        assert fragment in sql
    # Catalog-only: it never names the table in a FROM clause that would lock it.
    assert "FROM connectors.evidence_journal" not in sql


def test_the_last_downgrade_locks_the_table_before_it_looks_for_rows(monkeypatch) -> None:
    """An uncommitted insert is invisible to EXISTS; the lock must wait it out first."""
    module = _load()
    fake = _Op(installed=True)
    monkeypatch.setattr(module, "op", fake)
    monkeypatch.setattr(module, "_installation_count", lambda: 1)
    module.downgrade()

    check = next(s for s in fake.executed if "downgrade refused" in s)
    lock = check.index("LOCK TABLE connectors.evidence_journal IN ACCESS EXCLUSIVE MODE")
    assert lock < check.index("EXISTS (SELECT 1 FROM connectors.evidence_journal)")
    drop = next(i for i, s in enumerate(fake.executed) if s.startswith("DROP TABLE"))
    assert fake.executed.index(check) < drop


def test_an_earlier_schema_downgrade_takes_no_table_lock(monkeypatch) -> None:
    module = _load()
    fake = _Op(installed=True)
    monkeypatch.setattr(module, "op", fake)
    monkeypatch.setattr(module, "_installation_count", lambda: 2)
    module.downgrade()
    assert fake.executed == [module._LOCK]
