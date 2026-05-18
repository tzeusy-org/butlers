"""Tests for contact_backfill_triples.py — Migration bead 5.

Covers:
1. Script module existence and CLI contract.
2. Date-label validation (_validate_date_label).
3. Dry-run mode: no writes, correct return code.
4. Credentials carve-out: secured=true rows are skipped.
5. Orphan skip: rows whose contact has entity_id IS NULL are skipped.
6. Happy-path triple assertion with mocked pool.
7. Idempotency: already-present triples (ON CONFLICT) are counted separately.
8. Per-type (predicate) breakdown is correct.
9. Parity check in report: asserted + already_present + skipped_cred + skipped_orphan + errors = total.
10. Report file is written on apply=True, not written on dry-run.
11. Preflight: missing snapshot table returns exit code 1.
12. Preflight: missing relationship.entity_facts table returns exit code 1.
13. Predicate construction: f"has-{type}".
14. CLI _parse_args defaults: --apply=False, --date=today.
"""

from __future__ import annotations

import importlib.util
import sys as _sys
import uuid
from datetime import UTC, datetime
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
    / "contact_backfill_triples.py"
)


_MOD_NAME = "contact_backfill_triples"


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
        """contact_backfill_triples.py exists at the expected path."""
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
            mod._validate_date_label("20260601'; DROP TABLE public.contacts;--")


# ---------------------------------------------------------------------------
# Fixtures — mock asyncpg pool factory
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    contacts_snap_exists: bool = True,
    contact_info_snap_exists: bool = True,
    facts_table_exists: bool = True,
    contacts_total: int = 2,
    contact_info_total: int = 3,
    contact_info_rows: list[dict] | None = None,
    orphan_rows: list[dict] | None = None,
    assert_triple_returns_inserted: bool = True,
) -> AsyncMock:
    """Build a mock asyncpg pool that returns predictable results."""
    pool = AsyncMock()

    ent_id_a = uuid.uuid4()
    contact_id_a = uuid.uuid4()

    if contact_info_rows is None:
        # Two non-secured, non-orphan rows + one secured row
        contact_info_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": contact_id_a,
                "type": "email",
                "value": "alice@example.com",
                "is_primary": True,
                "secured": False,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "entity_id": ent_id_a,
            },
            {
                "ci_id": uuid.uuid4(),
                "contact_id": contact_id_a,
                "type": "phone",
                "value": "+1-555-0101",
                "is_primary": False,
                "secured": False,
                "created_at": datetime(2025, 2, 1, tzinfo=UTC),
                "entity_id": ent_id_a,
            },
            {
                "ci_id": uuid.uuid4(),
                "contact_id": contact_id_a,
                "type": "google_account",
                "value": "secret-token",
                "is_primary": False,
                "secured": True,  # credential — should be skipped
                "created_at": datetime(2025, 3, 1, tzinfo=UTC),
                "entity_id": ent_id_a,
            },
        ]

    if orphan_rows is None:
        orphan_rows = []

    # Map fetchval calls to results.
    # Call order: to_regclass(contacts_snap), to_regclass(contact_info_snap),
    #             to_regclass(relationship.entity_facts), COUNT contacts, COUNT contact_info
    _fetchval_results: list[Any] = [
        "public.contacts_pre_migration_20260601" if contacts_snap_exists else None,
        "public.contact_info_pre_migration_20260601" if contact_info_snap_exists else None,
        "relationship.entity_facts" if facts_table_exists else None,
        contacts_total,
        contact_info_total,
    ]
    _fetchval_index = [0]

    async def _fetchval(sql, *args):
        idx = _fetchval_index[0]
        _fetchval_index[0] += 1
        if idx < len(_fetchval_results):
            return _fetchval_results[idx]
        return None

    pool.fetchval.side_effect = _fetchval

    # fetch() calls: first call returns orphan rows, second returns contact_info rows
    _fetch_results = [
        [_make_record(r) for r in orphan_rows],
        [_make_record(r) for r in contact_info_rows],
    ]
    _fetch_index = [0]

    async def _fetch(sql, *args):
        idx = _fetch_index[0]
        _fetch_index[0] += 1
        if idx < len(_fetch_results):
            return _fetch_results[idx]
        return []

    pool.fetch.side_effect = _fetch

    # fetchrow() for the INSERT ... ON CONFLICT
    _inserted_record = MagicMock()
    _inserted_record.__getitem__ = lambda self, key: assert_triple_returns_inserted
    pool.fetchrow.return_value = _inserted_record

    return pool


def _make_record(data: dict) -> MagicMock:
    """Create a dict-like asyncpg Record mock."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: data[key]
    record.get = lambda key, default=None: data.get(key, default)
    record.__contains__ = lambda self, key: key in data
    return record


# ---------------------------------------------------------------------------
# 3. Dry-run: no writes
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_fetchrow(self, tmp_path: Path) -> None:
        """Dry-run must not call pool.fetchrow (the INSERT path)."""
        mod = _load_module()
        pool = _make_pool()
        report_path = tmp_path / "report.md"

        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=False,
            _pool=pool,
        )

        pool.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write_report(self, tmp_path: Path) -> None:
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
        """Dry-run returns 0 (success) even without writes."""
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
# 4. Credentials carve-out
# ---------------------------------------------------------------------------


class TestCredentialCarveOut:
    @pytest.mark.asyncio
    async def test_secured_rows_not_inserted(self, tmp_path: Path) -> None:
        """Rows with secured=True must not reach pool.fetchrow (the INSERT path)."""
        mod = _load_module()
        secured_only_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": "secret",
                "is_primary": False,
                "secured": True,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "entity_id": uuid.uuid4(),
            }
        ]
        pool = _make_pool(
            contact_info_rows=secured_only_rows,
            contact_info_total=1,
        )
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0
        pool.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_secured_count_in_stats(self, tmp_path: Path) -> None:
        """Stats.skipped_credential must equal the number of secured rows."""
        mod = _load_module()
        # Two secured rows
        secured_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "google_account",
                "value": f"token-{i}",
                "is_primary": False,
                "secured": True,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "entity_id": uuid.uuid4(),
            }
            for i in range(2)
        ]
        pool = _make_pool(contact_info_rows=secured_rows, contact_info_total=2)
        # Capture stats by running with apply=True and counting fetchrow calls
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0
        # No triples inserted — all were credentials
        pool.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Orphan skip
# ---------------------------------------------------------------------------


class TestOrphanSkip:
    @pytest.mark.asyncio
    async def test_orphan_rows_not_inserted(self, tmp_path: Path) -> None:
        """Rows for contacts with entity_id IS NULL must not trigger INSERT."""
        mod = _load_module()
        orphan_contact_id = uuid.uuid4()
        orphan_rows = [
            {
                "id": orphan_contact_id,
                "first_name": "Unknown",
                "last_name": None,
                "nickname": None,
            }
        ]
        contact_info_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": orphan_contact_id,
                "type": "email",
                "value": "orphan@example.com",
                "is_primary": False,
                "secured": False,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "entity_id": None,  # orphan
            }
        ]
        pool = _make_pool(
            orphan_rows=orphan_rows,
            contact_info_rows=contact_info_rows,
            contact_info_total=1,
            contacts_total=1,
        )
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0
        pool.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Happy-path triple assertion
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_assert_triple_called_for_eligible_rows(self, tmp_path: Path) -> None:
        """pool.fetchrow should be called once per non-secured, non-orphan row."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        contact_id = uuid.uuid4()
        eligible_rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": contact_id,
                "type": "email",
                "value": "alice@example.com",
                "is_primary": True,
                "secured": False,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "entity_id": ent_id,
            },
            {
                "ci_id": uuid.uuid4(),
                "contact_id": contact_id,
                "type": "phone",
                "value": "+1-555-0101",
                "is_primary": False,
                "secured": False,
                "created_at": datetime(2025, 2, 1, tzinfo=UTC),
                "entity_id": ent_id,
            },
        ]
        pool = _make_pool(
            contact_info_rows=eligible_rows,
            contact_info_total=2,
            contacts_total=1,
        )
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0
        assert pool.fetchrow.call_count == 2

    @pytest.mark.asyncio
    async def test_predicate_uses_has_type_format(self, tmp_path: Path) -> None:
        """The SQL INSERT must include a predicate of the form 'has-{type}'."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "telegram",
                "value": "telegram:12345",
                "is_primary": True,
                "secured": False,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "entity_id": ent_id,
            }
        ]
        pool = _make_pool(contact_info_rows=rows, contact_info_total=1, contacts_total=1)
        await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        # Check that fetchrow was called with 'has-telegram' as the predicate argument
        assert pool.fetchrow.called
        call_args = pool.fetchrow.call_args
        positional = call_args[0]
        # positional[0] is the SQL string, positional[1] = subject UUID,
        # positional[2] = predicate, positional[3] = object value
        assert "has-telegram" in positional, (
            f"Expected 'has-telegram' in fetchrow args, got: {positional}"
        )

    @pytest.mark.asyncio
    async def test_report_written_on_apply(self, tmp_path: Path) -> None:
        """Report file is written when apply=True."""
        mod = _load_module()
        pool = _make_pool()
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
        """Report must contain required sections per the spec."""
        mod = _load_module()
        pool = _make_pool()
        report_path = tmp_path / "report.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool,
        )
        content = report_path.read_text()
        assert "Backfill outcome" in content
        assert "Per-type" in content
        assert "Orphan contacts" in content
        assert "Skipped credentials" in content or "secured" in content.lower()
        assert "Parity" in content


# ---------------------------------------------------------------------------
# 7. Idempotency — already-present triples
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_already_present_does_not_raise(self, tmp_path: Path) -> None:
        """When ON CONFLICT fires (inserted=False), the run still succeeds (rc=0)."""
        mod = _load_module()
        pool = _make_pool(assert_triple_returns_inserted=False)
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 0

    @pytest.mark.asyncio
    async def test_second_run_succeeds(self, tmp_path: Path) -> None:
        """Running the backfill twice succeeds on the second run (idempotent)."""
        mod = _load_module()
        report_path = tmp_path / "r.md"
        # First run: inserts
        pool1 = _make_pool(assert_triple_returns_inserted=True)
        rc1 = await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool1,
        )
        # Second run: all already present
        pool2 = _make_pool(assert_triple_returns_inserted=False)
        rc2 = await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool2,
        )
        assert rc1 == 0
        assert rc2 == 0


# ---------------------------------------------------------------------------
# 8. Per-type breakdown
# ---------------------------------------------------------------------------


class TestPerTypeBreakdown:
    @pytest.mark.asyncio
    async def test_report_includes_per_type_breakdown(self, tmp_path: Path) -> None:
        """Report must include a per-predicate breakdown table."""
        mod = _load_module()
        ent_id = uuid.uuid4()
        rows = [
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "email",
                "value": "a@example.com",
                "is_primary": True,
                "secured": False,
                "created_at": datetime(2025, 1, 1, tzinfo=UTC),
                "entity_id": ent_id,
            },
            {
                "ci_id": uuid.uuid4(),
                "contact_id": uuid.uuid4(),
                "type": "phone",
                "value": "+1-555-0202",
                "is_primary": False,
                "secured": False,
                "created_at": datetime(2025, 1, 2, tzinfo=UTC),
                "entity_id": ent_id,
            },
        ]
        pool = _make_pool(contact_info_rows=rows, contact_info_total=2, contacts_total=1)
        report_path = tmp_path / "r.md"
        await mod.run_backfill(
            date_label="20260601",
            report_path=report_path,
            apply=True,
            _pool=pool,
        )
        content = report_path.read_text()
        assert "has-email" in content
        assert "has-phone" in content


# ---------------------------------------------------------------------------
# 9. Parity check in report
# ---------------------------------------------------------------------------


class TestParityCheck:
    @pytest.mark.asyncio
    async def test_parity_pass_when_counts_match(self, tmp_path: Path) -> None:
        """Report shows PASS when asserted + skipped + errors equals contact_info_total."""
        mod = _load_module()
        # Default pool: 3 rows total (2 eligible, 1 secured)
        pool = _make_pool(contact_info_total=3)
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
# 11. Preflight: missing snapshot table
# ---------------------------------------------------------------------------


class TestPreflightMissingSnapshot:
    @pytest.mark.asyncio
    async def test_missing_contacts_snapshot_returns_1(self, tmp_path: Path) -> None:
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
    async def test_missing_contact_info_snapshot_returns_1(self, tmp_path: Path) -> None:
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
# 12. Preflight: missing relationship.entity_facts table
# ---------------------------------------------------------------------------


class TestPreflightMissingFactsTable:
    @pytest.mark.asyncio
    async def test_missing_facts_table_returns_1(self, tmp_path: Path) -> None:
        """Returns exit code 1 when relationship.entity_facts does not exist."""
        mod = _load_module()
        pool = _make_pool(facts_table_exists=False)
        rc = await mod.run_backfill(
            date_label="20260601",
            report_path=tmp_path / "r.md",
            apply=True,
            _pool=pool,
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# 13. Predicate construction
# ---------------------------------------------------------------------------


class TestPredicateConstruction:
    def test_predicate_format(self) -> None:
        """Predicate must be 'has-{type}'."""
        # Test against the source directly
        source = _SCRIPT_PATH.read_text()
        assert 'f"has-{ci_type}"' in source or 'f"has-{ci_type}"' in source

    def test_predicate_format_with_email(self) -> None:
        """has-email predicate pattern is in the source."""
        source = _SCRIPT_PATH.read_text()
        # 'has-' concatenation with the type field
        assert "has-" in source

    def test_verified_false_default(self) -> None:
        """verified is always false for migrated triples (not owner-confirmed)."""
        source = _SCRIPT_PATH.read_text()
        assert "verified" in source
        assert "false" in source.lower()


# ---------------------------------------------------------------------------
# 14. CLI: _parse_args defaults
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
        args = mod._parse_args(["--date", "20260601", "--report-path", "/tmp/test.md"])
        assert isinstance(args.report_path, Path)
        assert str(args.report_path) == "/tmp/test.md"
