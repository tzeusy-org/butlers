"""Unit tests for the guarded drop migrations (bu-brbil, bu-zquce.2).

Migrations under test:
  - sw_014       roster/switchboard/migrations/014_drop_dead_feature_tables.py
  - sw_017       roster/switchboard/migrations/017_drop_triage_rules.py
  - finance_007  roster/finance/migrations/007_drop_import_batches.py
  - contacts_002 src/butlers/modules/contacts/migrations/002_drop_contacts_source_accounts.py
  - mem_005      src/butlers/modules/memory/migrations/005_drop_embedding_versions.py

Pure unit tests (no DB). Verify revision chains, that upgrade() drops every
target table (in FK order, guarded), that in-use tables are NOT dropped, and
that downgrade() recreates every dropped table.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SW_014 = _REPO_ROOT / "roster" / "switchboard" / "migrations" / "014_drop_dead_feature_tables.py"
_SW_017 = _REPO_ROOT / "roster" / "switchboard" / "migrations" / "017_drop_triage_rules.py"
_FINANCE_007 = _REPO_ROOT / "roster" / "finance" / "migrations" / "007_drop_import_batches.py"
_CONTACTS_002 = (
    _REPO_ROOT
    / "src"
    / "butlers"
    / "modules"
    / "contacts"
    / "migrations"
    / "002_drop_contacts_source_accounts.py"
)
_MEM_005 = (
    _REPO_ROOT
    / "src"
    / "butlers"
    / "modules"
    / "memory"
    / "migrations"
    / "005_drop_embedding_versions.py"
)


def _load(path: Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None, f"Cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _collect_sql(path: Path, mod_name: str, fn_name: str) -> list[str]:
    """Run upgrade()/downgrade() with op.execute mocked; return SQL strings."""
    mod = _load(path, mod_name)
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        getattr(mod, fn_name)()
    return sqls


def _has_drop(sqls: list[str], table: str) -> bool:
    needle = table.lower()
    return any("drop table if exists" in s.lower() and needle in s.lower() for s in sqls)


def _has_create(sqls: list[str], table: str) -> bool:
    needle = table.lower()
    return any("create table if not exists" in s.lower() and needle in s.lower() for s in sqls)


# (path, mod_name, revision, down_revision)
_MIGRATIONS = [
    (_SW_014, "sw_014", "sw_014", "sw_013"),
    (_SW_017, "sw_017", "sw_017", "sw_016"),
    (_FINANCE_007, "finance_007", "finance_007", "finance_006"),
    (_CONTACTS_002, "contacts_002", "contacts_002", "contacts_001"),
    (_MEM_005, "mem_005", "mem_005", "mem_004"),
]


@pytest.mark.parametrize("path,mod_name,revision,down_revision", _MIGRATIONS)
def test_revision_chain(path, mod_name, revision, down_revision) -> None:
    """Each drop migration declares its revision/down_revision, no branch/depends."""
    mod = _load(path, mod_name)
    assert mod.revision == revision
    assert mod.down_revision == down_revision
    assert mod.branch_labels is None
    assert mod.depends_on is None
    assert callable(getattr(mod, "upgrade", None))
    assert callable(getattr(mod, "downgrade", None))


# (path, mod_name, table) — every table that upgrade() must DROP
_DROP_TARGETS = [
    (_SW_014, "sw_014", "connector_source_filters"),
    (_SW_014, "sw_014", "source_filters"),
    (_SW_014, "sw_014", "email_metadata_refs"),
    (_SW_014, "sw_014", "fanout_execution_log"),
    (_SW_017, "sw_017", "triage_rules"),
    (_FINANCE_007, "finance_007", "import_batches"),
    (_CONTACTS_002, "contacts_002", "contacts_source_accounts"),
    (_MEM_005, "mem_005", "embedding_versions"),
]


@pytest.mark.parametrize("path,mod_name,table", _DROP_TARGETS)
def test_upgrade_drops_target_table(path, mod_name, table) -> None:
    """upgrade() emits a guarded DROP TABLE IF EXISTS for each dead table."""
    sqls = _collect_sql(path, mod_name, "upgrade")
    assert _has_drop(sqls, table), f"upgrade() must DROP TABLE IF EXISTS {table}"


# (path, mod_name, table) — downgrade() must recreate each dropped table
_RECREATE_TARGETS = [
    (_SW_014, "sw_014", "fanout_execution_log"),
    (_SW_014, "sw_014", "email_metadata_refs"),
    (_SW_014, "sw_014", "source_filters"),
    (_SW_014, "sw_014", "connector_source_filters"),
    (_SW_017, "sw_017", "triage_rules"),
    (_FINANCE_007, "finance_007", "import_batches"),
    (_CONTACTS_002, "contacts_002", "contacts_source_accounts"),
    (_MEM_005, "mem_005", "embedding_versions"),
]


@pytest.mark.parametrize("path,mod_name,table", _RECREATE_TARGETS)
def test_downgrade_recreates_table(path, mod_name, table) -> None:
    """downgrade() emits CREATE TABLE IF NOT EXISTS for each dropped table."""
    sqls = _collect_sql(path, mod_name, "downgrade")
    assert _has_create(sqls, table), f"downgrade() must CREATE TABLE IF NOT EXISTS {table}"


# (path, mod_name, table) — in-use tables that must NOT be dropped (scope guard)
_PROTECTED = [
    (_SW_014, "sw_014", "routing_rules"),
    (_SW_017, "sw_017", "ingestion_rules"),
    (_FINANCE_007, "finance_007", "transactions"),
    (_CONTACTS_002, "contacts_002", "contacts_sync_state"),
    (_CONTACTS_002, "contacts_002", "contacts_source_links"),
    (_MEM_005, "mem_005", "memories"),
]


@pytest.mark.parametrize("path,mod_name,table", _PROTECTED)
def test_upgrade_does_not_drop_in_use_table(path, mod_name, table) -> None:
    """upgrade() must NOT drop any still-live table (scope guard)."""
    sqls = _collect_sql(path, mod_name, "upgrade")
    assert len(sqls) >= 1, "upgrade() must emit at least one SQL statement"
    for sql in sqls:
        lower = sql.lower()
        assert not ("drop table" in lower and table.lower() in lower), (
            f"upgrade() must NOT drop {table} (still referenced at runtime)"
        )


def test_sw014_drops_connector_before_source_filters() -> None:
    """connector_source_filters must be dropped before source_filters (FK dependency)."""
    sqls = _collect_sql(_SW_014, "sw_014", "upgrade")
    positions: dict[str, int] = {}
    for i, sql in enumerate(sqls):
        lower = sql.lower()
        if "connector_source_filters" in lower and "drop" in lower:
            positions["connector"] = i
        elif "source_filters" in lower and "drop" in lower and "connector" not in lower:
            positions["source"] = i
    assert "connector" in positions and "source" in positions
    assert positions["connector"] < positions["source"]


def test_finance007_drops_guarded_fk_before_table() -> None:
    """ALTER TABLE DROP CONSTRAINT (IF EXISTS) must precede DROP TABLE import_batches."""
    sqls = _collect_sql(_FINANCE_007, "finance_007", "upgrade")
    fk_pos = next(
        (
            i
            for i, sql in enumerate(sqls)
            if "fk_txn_import_batch" in sql.lower() and "drop constraint" in sql.lower()
        ),
        None,
    )
    tbl_pos = next(
        (
            i
            for i, sql in enumerate(sqls)
            if "import_batches" in sql.lower() and "drop table" in sql.lower()
        ),
        None,
    )
    assert fk_pos is not None and tbl_pos is not None
    assert fk_pos < tbl_pos, "FK must be dropped before the table"
    fk_sql = next(sql for sql in sqls if "fk_txn_import_batch" in sql.lower())
    assert "if exists" in fk_sql.lower(), "DROP CONSTRAINT must use IF EXISTS for idempotency"


def test_finance007_downgrade_restores_fk() -> None:
    sqls = _collect_sql(_FINANCE_007, "finance_007", "downgrade")
    combined = " ".join(sqls).lower()
    assert "fk_txn_import_batch" in combined and "add constraint" in combined


def test_mem005_only_drops_table_no_alter() -> None:
    """mem_005 must drop the standalone table only — never ALTER (embedding_model_version
    column lives on another table, added by mem_004)."""
    sqls = _collect_sql(_MEM_005, "mem_005", "upgrade")
    combined = " ".join(sqls).lower()
    assert "drop table if exists embedding_versions" in combined
    assert "alter table" not in combined
