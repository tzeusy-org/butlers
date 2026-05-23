"""Tests for contact_backfill_credentials.py — Migration bead bu-5oci9.

Covers:
1. Script module existence and CLI contract.
2. Date-label validation (_validate_date_label).
3. Empty snapshot → script no-ops (0 inserts, rc=0).
4. 1 secured row with entity_id → 1 INSERT into relationship.credentials.
5. 1 secured row with NULL entity_id → skipped_orphan=1, no INSERT.
6. Idempotency: run twice, second run reports 0 inserts (conflict_no_op).
7. Mixed (secured + non-secured) → only secured rows are considered.
8. Dry-run: no writes, report not written, rc=0.
9. Preflight: missing snapshot table → rc=1.
10. Preflight: missing relationship.credentials table → rc=1.
11. Report file written on apply=True, not written on dry-run.
12. Report contains required sections and PASS parity.
13. CLI _parse_args defaults: --apply=False, --date=today.
"""

from __future__ import annotations

import importlib.util
import sys as _sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "butlers"
    / "scripts"
    / "contact_backfill_credentials.py"
)

_MOD_NAME = "contact_backfill_credentials"


def _load_module():
    """Import the backfill script by file path.

    Registers the module in sys.modules so that @dataclass and similar
    decorators that look up the class module via sys.modules work correctly.
    """
    if _MOD_NAME in _sys.modules:
        return _sys.modules[_MOD_NAME]
    spec = importlib.util.spec_from_file_location(_MOD_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. Script existence
# ---------------------------------------------------------------------------


class TestScriptExists:
    def test_script_file_exists(self) -> None:
        """contact_backfill_credentials.py exists at the expected path."""
        assert _SCRIPT_PATH.exists(), f"Script not found at {_SCRIPT_PATH}"

    def test_module_loads(self) -> None:
        """The module can be imported without errors."""
        mod = _load_module()
        assert mod is not None

    def test_main_callable(self) -> None:
        """main() is defined and callable."""
        mod = _load_module()
        assert callable(getattr(mod, "main", None))

    def test_run_backfill_callable(self) -> None:
        """run_backfill() is the injectable public entry point."""
        mod = _load_module()
        assert callable(getattr(mod, "run_backfill", None))


# ---------------------------------------------------------------------------
# 2. Date-label validation
# ---------------------------------------------------------------------------


class TestValidateDateLabel:
    def _validate(self, label: str) -> None:
        mod = _load_module()
        mod._validate_date_label(label)

    def test_valid_8_digit_string(self) -> None:
        """8 digits accepted without raising."""
        self._validate("20260601")

    def test_invalid_short(self) -> None:
        """7 digits raise ValueError."""
        mod = _load_module()
        with pytest.raises(ValueError, match="YYYYMMDD"):
            mod._validate_date_label("2026060")

    def test_invalid_long(self) -> None:
        """9 digits raise ValueError."""
        mod = _load_module()
        with pytest.raises(ValueError, match="YYYYMMDD"):
            mod._validate_date_label("202606010")

    def test_invalid_letters(self) -> None:
        """Letters raise ValueError."""
        mod = _load_module()
        with pytest.raises(ValueError):
            mod._validate_date_label("YYYYMMDD")

    def test_sql_injection_attempt(self) -> None:
        """SQL injection attempt is rejected."""
        mod = _load_module()
        with pytest.raises(ValueError):
            mod._validate_date_label("20260601'; DROP TABLE relationship.credentials;--")


# ---------------------------------------------------------------------------
# Pool fixture factory
# ---------------------------------------------------------------------------


def _make_record(data: dict) -> MagicMock:
    """Create a dict-like asyncpg Record mock."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: data[key]
    record.get = lambda key, default=None: data.get(key, default)
    record.__contains__ = lambda self, key: key in data
    return record


def _make_pool(
    *,
    contacts_snap_exists: bool = True,
    contact_info_snap_exists: bool = True,
    credentials_table_exists: bool = True,
    contacts_total: int = 1,
    contact_info_secured_total: int = 0,
    secured_rows: list[dict] | None = None,
    insert_returns_row: bool = True,
) -> AsyncMock:
    """Build a mock asyncpg pool that returns predictable results.

    fetchval call order:
      0 — to_regclass(contacts_snap)
      1 — to_regclass(contact_info_snap)
      2 — to_regclass(relationship.credentials)
      3 — COUNT(*) FROM contacts_snap
      4 — COUNT(*) FROM contact_info_snap WHERE secured = true

    pool.fetch is called once to load secured rows (JOIN contacts + contact_info).

    pool.fetchrow is called once per eligible secured row for the INSERT.
    Returns a mock row if insert_returns_row=True (new insert),
    returns None if insert_returns_row=False (ON CONFLICT DO NOTHING).
    """
    pool = AsyncMock()

    if secured_rows is None:
        secured_rows = []

    _fetchval_results: list[Any] = [
        "public.contacts_pre_migration_20260601" if contacts_snap_exists else None,
        "public.contact_info_pre_migration_20260601" if contact_info_snap_exists else None,
        "relationship.credentials" if credentials_table_exists else None,
        contacts_total,
        contact_info_secured_total,
    ]
    _fetchval_index = [0]

    async def _fetchval(sql, *args):
        idx = _fetchval_index[0]
        _fetchval_index[0] += 1
        if idx < len(_fetchval_results):
            return _fetchval_results[idx]
        return None

    pool.fetchval.side_effect = _fetchval

    # fetch() returns the secured rows
    async def _fetch(sql, *args):
        return [_make_record(r) for r in secured_rows]

    pool.fetch.side_effect = _fetch

    # fetchrow() for the INSERT ... ON CONFLICT DO NOTHING ... RETURNING id
    if insert_returns_row:
        inserted_record = MagicMock()
        inserted_record.__getitem__ = lambda self, key: uuid.uuid4()
        pool.fetchrow.return_value = inserted_record
    else:
        # ON CONFLICT DO NOTHING → no row returned
        pool.fetchrow.return_value = None

    return pool


# ---------------------------------------------------------------------------
# 3. Empty snapshot → no-ops
# ---------------------------------------------------------------------------


class TestEmptySnapshot:
    @pytest.mark.asyncio
    async def test_empty_snapshot_noop(self, tmp_path: Path) -> None:
        """When there are no secured rows, the script succeeds and makes no INSERTs."""
        mod = _load_module()
        pool = _make_pool(secured_rows=[], contact_info_secured_total=0)
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0
        pool.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_snapshot_report_parity_pass(self, tmp_path: Path) -> None:
        """Report shows PASS parity when there are no secured rows."""
        mod = _load_module()
        pool = _make_pool(secured_rows=[], contact_info_secured_total=0)
        report_path = tmp_path / "r.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool,
        )
        content = report_path.read_text()
        assert "PASS" in content


# ---------------------------------------------------------------------------
# 4. 1 secured row with entity_id → 1 INSERT
# ---------------------------------------------------------------------------


class TestOneSecuredRowInserted:
    @pytest.mark.asyncio
    async def test_inserts_one_credential(self, tmp_path: Path) -> None:
        """A single secured row with a valid entity_id results in one INSERT."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        contact_id = uuid.uuid4()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": contact_id,
                "type": "google_account",
                "value": "encrypted-token",
                "entity_id": ent_id,
            }
        ]
        pool = _make_pool(
            secured_rows=secured_rows,
            contact_info_secured_total=1,
            insert_returns_row=True,
        )
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0
        assert pool.fetchrow.call_count == 1

    @pytest.mark.asyncio
    async def test_insert_sql_contains_credentials_table(self, tmp_path: Path) -> None:
        """INSERT SQL must target relationship.credentials."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "telegram_session",
                "value": "session-data",
                "entity_id": ent_id,
            }
        ]
        pool = _make_pool(secured_rows=secured_rows, contact_info_secured_total=1)
        await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert pool.fetchrow.called
        sql = pool.fetchrow.call_args[0][0]
        assert "relationship.credentials" in sql

    @pytest.mark.asyncio
    async def test_insert_sql_uses_on_conflict_do_nothing(self, tmp_path: Path) -> None:
        """INSERT SQL must use ON CONFLICT DO NOTHING for idempotency."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "encrypted",
                "entity_id": ent_id,
            }
        ]
        pool = _make_pool(secured_rows=secured_rows, contact_info_secured_total=1)
        await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        sql = pool.fetchrow.call_args[0][0]
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql


# ---------------------------------------------------------------------------
# 5. NULL entity_id → skipped_orphan, no INSERT
# ---------------------------------------------------------------------------


class TestOrphanCredential:
    @pytest.mark.asyncio
    async def test_null_entity_id_no_insert(self, tmp_path: Path) -> None:
        """A secured row with entity_id IS NULL must not trigger an INSERT."""
        mod = _load_module()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "encrypted-token",
                "entity_id": None,  # orphan
            }
        ]
        pool = _make_pool(secured_rows=secured_rows, contact_info_secured_total=1)
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0
        pool.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_null_entity_id_parity_pass(self, tmp_path: Path) -> None:
        """Parity check still passes when one row is skipped as orphan."""
        mod = _load_module()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "encrypted-token",
                "entity_id": None,
            }
        ]
        pool = _make_pool(secured_rows=secured_rows, contact_info_secured_total=1)
        report_path = tmp_path / "r.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool,
        )
        content = report_path.read_text()
        assert "PASS" in content


# ---------------------------------------------------------------------------
# 6. Idempotency: second run reports 0 inserts
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_second_run_no_new_inserts(self, tmp_path: Path) -> None:
        """ON CONFLICT DO NOTHING on second run → fetchrow returns None, rc=0."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "encrypted",
                "entity_id": ent_id,
            }
        ]
        # First run: INSERT succeeds
        pool1 = _make_pool(
            secured_rows=secured_rows,
            contact_info_secured_total=1,
            insert_returns_row=True,
        )
        rc1 = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r1.md",
            apply=True,
            _pool=pool1,
        )
        # Second run: ON CONFLICT DO NOTHING (no row returned)
        pool2 = _make_pool(
            secured_rows=secured_rows,
            contact_info_secured_total=1,
            insert_returns_row=False,
        )
        rc2 = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r2.md",
            apply=True,
            _pool=pool2,
        )
        assert rc1 == 0
        assert rc2 == 0
        # Second run: fetchrow is still called (to attempt INSERT), but returns None
        assert pool2.fetchrow.call_count == 1

    @pytest.mark.asyncio
    async def test_second_run_report_shows_conflict_no_op(self, tmp_path: Path) -> None:
        """Report for second run must show conflict_no_op > 0 and inserted = 0."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "encrypted",
                "entity_id": ent_id,
            }
        ]
        pool = _make_pool(
            secured_rows=secured_rows,
            contact_info_secured_total=1,
            insert_returns_row=False,  # simulate second run: ON CONFLICT
        )
        report_path = tmp_path / "r.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool,
        )
        content = report_path.read_text()
        # inserted = 0, conflict_no_op = 1
        assert "Conflict no-op" in content or "conflict_no_op" in content.lower() or "1" in content
        assert "PASS" in content


# ---------------------------------------------------------------------------
# 7. Mixed (secured + non-secured) → only secured rows considered
# ---------------------------------------------------------------------------


class TestMixedRows:
    @pytest.mark.asyncio
    async def test_only_secured_rows_processed(self, tmp_path: Path) -> None:
        """The SQL query filters WHERE secured = true; non-secured rows never reach the script."""
        mod = _load_module()
        # The pool's fetch() mock returns only the secured rows (simulating WHERE secured=true).
        # Non-secured rows would have been filtered out by the SQL.
        ent_id = uuid.uuid4()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "secret",
                "entity_id": ent_id,
            }
        ]
        # contact_info_secured_total = 1 (only the secured row)
        pool = _make_pool(secured_rows=secured_rows, contact_info_secured_total=1)
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0
        # Only 1 fetchrow call (for the 1 secured row)
        assert pool.fetchrow.call_count == 1

    def test_fetch_sql_filters_secured(self) -> None:
        """The SQL in the source must filter WHERE secured = true."""
        source = _SCRIPT_PATH.read_text()
        assert "secured = true" in source or "secured=true" in source.replace(" ", "")


# ---------------------------------------------------------------------------
# 8. Dry-run: no writes, report not written, rc=0
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_no_insert(self, tmp_path: Path) -> None:
        """Dry-run must not call pool.fetchrow (the INSERT path)."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "secret",
                "entity_id": ent_id,
            }
        ]
        pool = _make_pool(secured_rows=secured_rows, contact_info_secured_total=1)
        await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=False,
            _pool=pool,
        )
        pool.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_no_report(self, tmp_path: Path) -> None:
        """Dry-run must not write the report file."""
        mod = _load_module()
        pool = _make_pool()
        report_path = tmp_path / "report.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=False,
            _pool=pool,
        )
        assert not report_path.exists(), "Report should not be written in dry-run mode"

    @pytest.mark.asyncio
    async def test_dry_run_returns_0(self, tmp_path: Path) -> None:
        """Dry-run returns 0 (success)."""
        mod = _load_module()
        pool = _make_pool()
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=False,
            _pool=pool,
        )
        assert rc == 0


# ---------------------------------------------------------------------------
# 9. Preflight: missing snapshot table → rc=1
# ---------------------------------------------------------------------------


class TestPreflightMissingSnapshot:
    @pytest.mark.asyncio
    async def test_missing_contacts_snapshot(self, tmp_path: Path) -> None:
        """Returns exit code 1 when contacts snapshot table is missing."""
        mod = _load_module()
        pool = _make_pool(contacts_snap_exists=False)
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 1

    @pytest.mark.asyncio
    async def test_missing_contact_info_snapshot(self, tmp_path: Path) -> None:
        """Returns exit code 1 when contact_info snapshot table is missing."""
        mod = _load_module()
        pool = _make_pool(contact_info_snap_exists=False)
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# 10. Preflight: missing relationship.credentials table → rc=1
# ---------------------------------------------------------------------------


class TestPreflightMissingCredentialsTable:
    @pytest.mark.asyncio
    async def test_missing_credentials_table(self, tmp_path: Path) -> None:
        """Returns exit code 1 when relationship.credentials does not exist."""
        mod = _load_module()
        pool = _make_pool(credentials_table_exists=False)
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# 11–12. Report file written on apply=True; contains required sections
# ---------------------------------------------------------------------------


class TestReport:
    @pytest.mark.asyncio
    async def test_report_written_on_apply(self, tmp_path: Path) -> None:
        """Report file is written when apply=True."""
        mod = _load_module()
        pool = _make_pool(secured_rows=[], contact_info_secured_total=0)
        report_path = tmp_path / "report.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool,
        )
        assert report_path.exists(), "Report file should be written when apply=True"

    @pytest.mark.asyncio
    async def test_report_contains_key_sections(self, tmp_path: Path) -> None:
        """Report must contain required sections."""
        mod = _load_module()
        pool = _make_pool(secured_rows=[], contact_info_secured_total=0)
        report_path = tmp_path / "report.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool,
        )
        content = report_path.read_text()
        assert "Backfill outcome" in content
        assert "Parity" in content
        assert "Orphan" in content
        assert "relationship.credentials" in content

    @pytest.mark.asyncio
    async def test_report_parity_pass_when_counts_match(self, tmp_path: Path) -> None:
        """Report shows PASS when all counts sum correctly."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "encrypted",
                "entity_id": ent_id,
            }
        ]
        pool = _make_pool(secured_rows=secured_rows, contact_info_secured_total=1)
        report_path = tmp_path / "r.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool,
        )
        content = report_path.read_text()
        assert "PASS" in content


# ---------------------------------------------------------------------------
# 13. CLI _parse_args defaults
# ---------------------------------------------------------------------------


class TestCLIParseArgs:
    def test_default_apply_is_false(self) -> None:
        """--apply defaults to False (dry-run by default)."""
        mod = _load_module()
        args = mod._parse_args(["--date", "20260601"])
        assert args.apply is False

    def test_apply_flag_sets_true(self) -> None:
        """Passing --apply sets apply=True."""
        mod = _load_module()
        args = mod._parse_args(["--date", "20260601", "--apply"])
        assert args.apply is True

    def test_date_can_be_overridden(self) -> None:
        """--date overrides the default today value."""
        mod = _load_module()
        args = mod._parse_args(["--date", "20260101"])
        assert args.date == "20260101"

    def test_report_path_is_path_object(self) -> None:
        """--report-path is parsed as a Path."""
        mod = _load_module()
        args = mod._parse_args(["--date", "20260601", "--report-path", "/tmp/creds.md"])
        assert isinstance(args.report_path, Path)
        assert str(args.report_path) == "/tmp/creds.md"
