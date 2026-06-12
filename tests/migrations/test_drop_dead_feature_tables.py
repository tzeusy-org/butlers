"""Unit tests for the 4 guarded drop migrations (bu-brbil).

Migrations under test:
  - sw_014    roster/switchboard/migrations/014_drop_dead_feature_tables.py
  - finance_007  roster/finance/migrations/007_drop_import_batches.py
  - contacts_002 src/butlers/modules/contacts/migrations/002_drop_contacts_source_accounts.py
  - mem_005   src/butlers/modules/memory/migrations/005_drop_embedding_versions.py

All tests are pure unit tests (no DB required).  They verify:
  1. File exists at expected path.
  2. revision / down_revision / branch_labels / depends_on identifiers.
  3. upgrade() emits DROP TABLE IF EXISTS for every target table.
  4. Tables NOT in scope are absent from upgrade() SQL.
  5. downgrade() emits CREATE TABLE IF NOT EXISTS for every dropped table.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SW_014 = _REPO_ROOT / "roster" / "switchboard" / "migrations" / "014_drop_dead_feature_tables.py"
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


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def _load(path: Path, mod_name: str):
    """Load a migration module by file path."""
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec is not None and spec.loader is not None, f"Cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _collect_upgrade_sql(path: Path, mod_name: str) -> list[str]:
    """Run upgrade() with op.execute mocked; return SQL strings."""
    mod = _load(path, mod_name)
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return sqls


def _collect_downgrade_sql(path: Path, mod_name: str) -> list[str]:
    """Run downgrade() with op.execute mocked; return SQL strings."""
    mod = _load(path, mod_name)
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.downgrade()
    return sqls


def _has_drop(sqls: list[str], table: str) -> bool:
    """Return True if any SQL string contains both DROP TABLE IF EXISTS and the table name."""
    needle = table.lower()
    for sql in sqls:
        lower = sql.lower()
        if "drop table if exists" in lower and needle in lower:
            return True
    return False


def _has_create(sqls: list[str], table: str) -> bool:
    """Return True if any SQL string contains both CREATE TABLE IF NOT EXISTS and the table name."""
    needle = table.lower()
    for sql in sqls:
        lower = sql.lower()
        if "create table if not exists" in lower and needle in lower:
            return True
    return False


# ---------------------------------------------------------------------------
# sw_014 — switchboard drop (4 tables)
# ---------------------------------------------------------------------------


class TestSw014FileAndChain:
    def test_file_exists(self) -> None:
        assert _SW_014.exists(), f"Migration file not found: {_SW_014}"

    def test_revision(self) -> None:
        assert _load(_SW_014, "sw_014").revision == "sw_014"

    def test_down_revision(self) -> None:
        assert _load(_SW_014, "sw_014").down_revision == "sw_013"

    def test_branch_labels_none(self) -> None:
        assert _load(_SW_014, "sw_014").branch_labels is None

    def test_depends_on_none(self) -> None:
        assert _load(_SW_014, "sw_014").depends_on is None

    def test_upgrade_callable(self) -> None:
        assert callable(getattr(_load(_SW_014, "sw_014"), "upgrade", None))

    def test_downgrade_callable(self) -> None:
        assert callable(getattr(_load(_SW_014, "sw_014"), "downgrade", None))


class TestSw014UpgradeSQL:
    def test_drops_connector_source_filters(self) -> None:
        sqls = _collect_upgrade_sql(_SW_014, "sw_014")
        assert _has_drop(sqls, "connector_source_filters"), (
            "upgrade() must DROP TABLE IF EXISTS connector_source_filters"
        )

    def test_drops_source_filters(self) -> None:
        sqls = _collect_upgrade_sql(_SW_014, "sw_014")
        assert _has_drop(sqls, "source_filters"), (
            "upgrade() must DROP TABLE IF EXISTS source_filters"
        )

    def test_drops_email_metadata_refs(self) -> None:
        sqls = _collect_upgrade_sql(_SW_014, "sw_014")
        assert _has_drop(sqls, "email_metadata_refs"), (
            "upgrade() must DROP TABLE IF EXISTS email_metadata_refs"
        )

    def test_drops_fanout_execution_log(self) -> None:
        sqls = _collect_upgrade_sql(_SW_014, "sw_014")
        assert _has_drop(sqls, "fanout_execution_log"), (
            "upgrade() must DROP TABLE IF EXISTS fanout_execution_log"
        )

    def test_connector_dropped_before_source_filters(self) -> None:
        """connector_source_filters must be dropped before source_filters (FK dependency)."""
        sqls = _collect_upgrade_sql(_SW_014, "sw_014")
        positions = {}
        for i, sql in enumerate(sqls):
            lower = sql.lower()
            if "connector_source_filters" in lower and "drop" in lower:
                positions["connector"] = i
            elif "source_filters" in lower and "drop" in lower and "connector" not in lower:
                positions["source"] = i
        assert "connector" in positions and "source" in positions, (
            "Both connector_source_filters and source_filters drops must be present"
        )
        assert positions["connector"] < positions["source"], (
            "connector_source_filters must be dropped before source_filters"
        )

    def test_does_not_drop_routing_table(self) -> None:
        """routing_rules must NOT appear in upgrade SQL (not in scope)."""
        sqls = _collect_upgrade_sql(_SW_014, "sw_014")
        assert len(sqls) >= 1, "upgrade() must emit at least one SQL statement"
        combined = " ".join(sqls).lower()
        assert "routing_rules" not in combined, (
            "routing_rules is not a dead table and must not be dropped"
        )


class TestSw014DowngradeSQL:
    def test_recreates_fanout_execution_log(self) -> None:
        sqls = _collect_downgrade_sql(_SW_014, "sw_014")
        assert _has_create(sqls, "fanout_execution_log"), (
            "downgrade() must CREATE TABLE IF NOT EXISTS fanout_execution_log"
        )

    def test_recreates_email_metadata_refs(self) -> None:
        sqls = _collect_downgrade_sql(_SW_014, "sw_014")
        assert _has_create(sqls, "email_metadata_refs"), (
            "downgrade() must CREATE TABLE IF NOT EXISTS email_metadata_refs"
        )

    def test_recreates_source_filters(self) -> None:
        sqls = _collect_downgrade_sql(_SW_014, "sw_014")
        assert _has_create(sqls, "source_filters"), (
            "downgrade() must CREATE TABLE IF NOT EXISTS source_filters"
        )

    def test_recreates_connector_source_filters(self) -> None:
        sqls = _collect_downgrade_sql(_SW_014, "sw_014")
        assert _has_create(sqls, "connector_source_filters"), (
            "downgrade() must CREATE TABLE IF NOT EXISTS connector_source_filters"
        )


# ---------------------------------------------------------------------------
# finance_007 — import_batches drop
# ---------------------------------------------------------------------------


class TestFinance007FileAndChain:
    def test_file_exists(self) -> None:
        assert _FINANCE_007.exists(), f"Migration file not found: {_FINANCE_007}"

    def test_revision(self) -> None:
        assert _load(_FINANCE_007, "finance_007").revision == "finance_007"

    def test_down_revision(self) -> None:
        assert _load(_FINANCE_007, "finance_007").down_revision == "finance_006"

    def test_branch_labels_none(self) -> None:
        assert _load(_FINANCE_007, "finance_007").branch_labels is None

    def test_depends_on_none(self) -> None:
        assert _load(_FINANCE_007, "finance_007").depends_on is None

    def test_upgrade_callable(self) -> None:
        assert callable(getattr(_load(_FINANCE_007, "finance_007"), "upgrade", None))

    def test_downgrade_callable(self) -> None:
        assert callable(getattr(_load(_FINANCE_007, "finance_007"), "downgrade", None))


class TestFinance007UpgradeSQL:
    def test_drops_fk_before_table(self) -> None:
        """ALTER TABLE DROP CONSTRAINT must precede DROP TABLE import_batches."""
        sqls = _collect_upgrade_sql(_FINANCE_007, "finance_007")
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
        assert fk_pos is not None, "upgrade() must DROP CONSTRAINT fk_txn_import_batch"
        assert tbl_pos is not None, "upgrade() must DROP TABLE import_batches"
        assert fk_pos < tbl_pos, "FK must be dropped before the table"

    def test_drops_import_batches(self) -> None:
        sqls = _collect_upgrade_sql(_FINANCE_007, "finance_007")
        assert _has_drop(sqls, "import_batches"), (
            "upgrade() must DROP TABLE IF EXISTS import_batches"
        )

    def test_fk_drop_is_guarded(self) -> None:
        sqls = _collect_upgrade_sql(_FINANCE_007, "finance_007")
        fk_sql = next(
            (sql for sql in sqls if "fk_txn_import_batch" in sql.lower()),
            None,
        )
        assert fk_sql is not None
        assert "if exists" in fk_sql.lower(), "DROP CONSTRAINT must use IF EXISTS for idempotency"

    def test_does_not_drop_transactions(self) -> None:
        sqls = _collect_upgrade_sql(_FINANCE_007, "finance_007")
        assert len(sqls) >= 1, "upgrade() must emit at least one SQL statement"
        for sql in sqls:
            lower = sql.lower()
            assert not ("drop table" in lower and "transactions" in lower), (
                "upgrade() must NOT drop the transactions table"
            )


class TestFinance007DowngradeSQL:
    def test_recreates_import_batches(self) -> None:
        sqls = _collect_downgrade_sql(_FINANCE_007, "finance_007")
        assert _has_create(sqls, "import_batches"), (
            "downgrade() must CREATE TABLE IF NOT EXISTS import_batches"
        )

    def test_restores_fk(self) -> None:
        sqls = _collect_downgrade_sql(_FINANCE_007, "finance_007")
        combined = " ".join(sqls).lower()
        assert "fk_txn_import_batch" in combined and "add constraint" in combined, (
            "downgrade() must restore the fk_txn_import_batch constraint"
        )


# ---------------------------------------------------------------------------
# contacts_002 — contacts_source_accounts drop
# ---------------------------------------------------------------------------


class TestContacts002FileAndChain:
    def test_file_exists(self) -> None:
        assert _CONTACTS_002.exists(), f"Migration file not found: {_CONTACTS_002}"

    def test_revision(self) -> None:
        assert _load(_CONTACTS_002, "contacts_002").revision == "contacts_002"

    def test_down_revision(self) -> None:
        assert _load(_CONTACTS_002, "contacts_002").down_revision == "contacts_001"

    def test_branch_labels_none(self) -> None:
        assert _load(_CONTACTS_002, "contacts_002").branch_labels is None

    def test_depends_on_none(self) -> None:
        assert _load(_CONTACTS_002, "contacts_002").depends_on is None

    def test_upgrade_callable(self) -> None:
        assert callable(getattr(_load(_CONTACTS_002, "contacts_002"), "upgrade", None))

    def test_downgrade_callable(self) -> None:
        assert callable(getattr(_load(_CONTACTS_002, "contacts_002"), "downgrade", None))


class TestContacts002UpgradeSQL:
    def test_drops_contacts_source_accounts(self) -> None:
        sqls = _collect_upgrade_sql(_CONTACTS_002, "contacts_002")
        assert _has_drop(sqls, "contacts_source_accounts"), (
            "upgrade() must DROP TABLE IF EXISTS contacts_source_accounts"
        )

    def test_does_not_drop_contacts_sync_state(self) -> None:
        """contacts_sync_state is still live — must NOT be touched."""
        sqls = _collect_upgrade_sql(_CONTACTS_002, "contacts_002")
        assert len(sqls) >= 1, "upgrade() must emit at least one SQL statement"
        for sql in sqls:
            lower = sql.lower()
            assert not ("drop" in lower and "contacts_sync_state" in lower), (
                "upgrade() must NOT drop contacts_sync_state (still referenced at runtime)"
            )

    def test_does_not_drop_contacts_source_links(self) -> None:
        """contacts_source_links is still live — must NOT be touched."""
        sqls = _collect_upgrade_sql(_CONTACTS_002, "contacts_002")
        assert len(sqls) >= 1, "upgrade() must emit at least one SQL statement"
        for sql in sqls:
            lower = sql.lower()
            assert not ("drop" in lower and "contacts_source_links" in lower), (
                "upgrade() must NOT drop contacts_source_links (still referenced at runtime)"
            )


class TestContacts002DowngradeSQL:
    def test_recreates_contacts_source_accounts(self) -> None:
        sqls = _collect_downgrade_sql(_CONTACTS_002, "contacts_002")
        assert _has_create(sqls, "contacts_source_accounts"), (
            "downgrade() must CREATE TABLE IF NOT EXISTS contacts_source_accounts"
        )


# ---------------------------------------------------------------------------
# mem_005 — embedding_versions drop
# ---------------------------------------------------------------------------


class TestMem005FileAndChain:
    def test_file_exists(self) -> None:
        assert _MEM_005.exists(), f"Migration file not found: {_MEM_005}"

    def test_revision(self) -> None:
        assert _load(_MEM_005, "mem_005").revision == "mem_005"

    def test_down_revision(self) -> None:
        assert _load(_MEM_005, "mem_005").down_revision == "mem_004"

    def test_branch_labels_none(self) -> None:
        assert _load(_MEM_005, "mem_005").branch_labels is None

    def test_depends_on_none(self) -> None:
        assert _load(_MEM_005, "mem_005").depends_on is None

    def test_upgrade_callable(self) -> None:
        assert callable(getattr(_load(_MEM_005, "mem_005"), "upgrade", None))

    def test_downgrade_callable(self) -> None:
        assert callable(getattr(_load(_MEM_005, "mem_005"), "downgrade", None))


class TestMem005UpgradeSQL:
    def test_drops_embedding_versions(self) -> None:
        sqls = _collect_upgrade_sql(_MEM_005, "mem_005")
        assert _has_drop(sqls, "embedding_versions"), (
            "upgrade() must DROP TABLE IF EXISTS embedding_versions"
        )

    def test_does_not_drop_memories(self) -> None:
        """The main memories table must not be touched."""
        sqls = _collect_upgrade_sql(_MEM_005, "mem_005")
        assert len(sqls) >= 1, "upgrade() must emit at least one SQL statement"
        for sql in sqls:
            lower = sql.lower()
            assert not ("drop table" in lower and " memories" in lower), (
                "upgrade() must NOT drop the memories table"
            )

    def test_does_not_drop_embedding_model_version_column(self) -> None:
        """embedding_model_version column (on memory entities, added by mem_004) is separate.
        It must not appear in any DROP in upgrade SQL."""
        sqls = _collect_upgrade_sql(_MEM_005, "mem_005")
        combined = " ".join(sqls).lower()
        # The column lives on another table; we only drop the standalone table.
        # Verify upgrade SQL contains exactly one table reference to embedding_versions.
        assert "drop table if exists embedding_versions" in combined, (
            "upgrade must use guarded DROP TABLE IF EXISTS embedding_versions"
        )
        assert "alter table" not in combined, (
            "upgrade() must not alter any table, only drop embedding_versions"
        )


class TestMem005DowngradeSQL:
    def test_recreates_embedding_versions(self) -> None:
        sqls = _collect_downgrade_sql(_MEM_005, "mem_005")
        assert _has_create(sqls, "embedding_versions"), (
            "downgrade() must CREATE TABLE IF NOT EXISTS embedding_versions"
        )
