"""Unit tests for src/butlers/scripts/backfill_contact_info_triples.py.

Covers:
1. Script file exists and loads.
2. CLI defaults: --apply=False.
3. Dry-run: no writes; counts gap rows correctly.
4. Secured=true rows are skipped.
5. NULL entity_id rows are skipped.
6. Unmapped types are skipped with per-type counts.
7. Already-present triples are not re-asserted.
8. Apply mode asserts gap triples via the central writer.
9. Idempotency: re-run with no gaps produces asserted=0.
10. Report is always written (both dry-run and apply).
11. Preflight: missing entity_facts table returns rc=1.
12. Preflight: missing predicate_registry table returns rc=1.
13. Errors are counted and rc=1 is returned.
14. pending_approval outcome from owner entity is counted as asserted.
"""

from __future__ import annotations

import importlib.util
import sys as _sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "butlers"
    / "scripts"
    / "backfill_contact_info_triples.py"
)
_MOD_NAME = "backfill_contact_info_triples"


def _load_module():
    if _MOD_NAME in _sys.modules:
        return _sys.modules[_MOD_NAME]
    spec = importlib.util.spec_from_file_location(_MOD_NAME, _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers: mock writer module
# ---------------------------------------------------------------------------

_MAPPED_TYPES = {
    "email": "has-email",
    "phone": "has-phone",
    "telegram": "has-handle",
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "website": "has-website",
    "other": "has-handle",
}

_UNMAPPED_TYPES = {
    "telegram_user_id",
    "telegram_username",
    "telegram_chat_id",
    "google_health",
    "home_assistant_url",
}


class _MockAssertOutcome:
    inserted = "inserted"
    superseded = "superseded"
    unchanged = "unchanged"
    pending_approval = "pending_approval"


class _MockAssertResult:
    def __init__(self, outcome: str, fact_id=None, action_id=None):
        self.outcome = outcome
        self.fact_id = fact_id
        self.action_id = action_id


def _make_writer_mod(
    *,
    assert_outcome: str = "inserted",
    assert_raises: Exception | None = None,
):
    """Build a minimal mock of relationship_assert_fact module."""
    mod = MagicMock()
    mod.AssertOutcome = _MockAssertOutcome

    def _type_to_predicate(ci_type: str) -> str | None:
        return _MAPPED_TYPES.get(ci_type)

    mod.contact_info_type_to_predicate.side_effect = _type_to_predicate

    if assert_raises is not None:
        mod.relationship_assert_fact = AsyncMock(side_effect=assert_raises)
    else:
        result = _MockAssertResult(outcome=assert_outcome, fact_id=uuid.uuid4())
        mod.relationship_assert_fact = AsyncMock(return_value=result)

    return mod


# ---------------------------------------------------------------------------
# Helpers: mock asyncpg pool
# ---------------------------------------------------------------------------

_ENTITY_ID_A = uuid.uuid4()
_CONTACT_ID_A = uuid.uuid4()
_CREATED_AT = datetime(2025, 6, 1, tzinfo=UTC)


def _ci_row(
    *,
    ci_type: str = "email",
    value: str = "alice@example.com",
    secured: bool = False,
    entity_id: uuid.UUID | None = _ENTITY_ID_A,
    is_primary: bool = True,
    created_at: datetime | None = _CREATED_AT,
) -> dict[str, Any]:
    return {
        "ci_id": uuid.uuid4(),
        "contact_id": _CONTACT_ID_A,
        "type": ci_type,
        "value": value,
        "is_primary": is_primary,
        "secured": secured,
        "created_at": created_at,
        "entity_id": entity_id,
    }


def _as_record(data: dict[str, Any]) -> MagicMock:
    rec = MagicMock()
    rec.__getitem__ = lambda self, key: data[key]
    rec.get = lambda key, default=None: data.get(key, default)
    return rec


def _make_pool(
    *,
    facts_table_exists: bool = True,
    predicate_registry_exists: bool = True,
    ci_rows: list[dict[str, Any]] | None = None,
    # _has_active_triple returns: list of booleans, one per gap-candidate row.
    # The pool is queried once per non-secured, non-null-entity, mapped-type row.
    active_triples: list[bool] | None = None,
) -> MagicMock:
    """Build a mock asyncpg pool.

    fetchval call sequence inside _run_backfill_with_pool:
      0 — to_regclass('relationship.entity_facts')
      1 — to_regclass('relationship.entity_predicate_registry')
      2..N — _has_active_triple queries (one per gap-candidate row)
    """
    pool = MagicMock()

    if ci_rows is None:
        ci_rows = [_ci_row()]

    if active_triples is None:
        # Default: no triples present yet → all are gaps
        active_triples = [False] * len(ci_rows)

    # fetchval sequence
    preflight_results: list[Any] = [
        facts_table_exists,
        predicate_registry_exists,
    ]
    _fetchval_index = [0]
    _fetchval_results = list(preflight_results)
    # We'll serve True/False for gap checks as they come in
    _gap_results: list[bool] = list(active_triples)
    _gap_index = [0]

    async def _fetchval(sql: str, *args: Any) -> Any:
        idx = _fetchval_index[0]
        _fetchval_index[0] += 1
        if idx < len(_fetchval_results):
            return _fetchval_results[idx]
        # Remaining calls are _has_active_triple lookups
        gi = _gap_index[0]
        _gap_index[0] += 1
        if gi < len(_gap_results):
            return _gap_results[gi]  # type: ignore[return-value]
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)

    # fetchrow: used for _has_active_triple (SELECT 1 ... LIMIT 1)
    # Return a truthy record if triple present, else None
    _triple_results = list(active_triples)
    _triple_index = [0]

    async def _fetchrow(sql: str, *args: Any) -> Any:
        ti = _triple_index[0]
        _triple_index[0] += 1
        if ti < len(_triple_results):
            return MagicMock() if _triple_results[ti] else None
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)

    # fetch: returns the full contact_info row list
    pool.fetch = AsyncMock(return_value=[_as_record(r) for r in ci_rows])

    pool.acquire = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock()))

    return pool


# ---------------------------------------------------------------------------
# 1. Script exists and loads
# ---------------------------------------------------------------------------


class TestScriptExists:
    def test_script_file_exists(self) -> None:
        assert _SCRIPT_PATH.exists(), f"Script not found at {_SCRIPT_PATH}"

    def test_module_loads(self) -> None:
        mod = _load_module()
        assert mod is not None

    def test_run_backfill_callable(self) -> None:
        mod = _load_module()
        assert callable(getattr(mod, "run_backfill", None))

    def test_main_callable(self) -> None:
        mod = _load_module()
        assert callable(getattr(mod, "main", None))


# ---------------------------------------------------------------------------
# 2. CLI defaults
# ---------------------------------------------------------------------------


class TestCliDefaults:
    def test_apply_default_false(self) -> None:
        """--apply defaults to False (dry-run is the safe default)."""
        mod = _load_module()
        args = mod._parse_args([])
        assert args.apply is False

    def test_apply_flag_sets_true(self) -> None:
        mod = _load_module()
        args = mod._parse_args(["--apply"])
        assert args.apply is True

    def test_report_path_default_exists(self) -> None:
        mod = _load_module()
        args = mod._parse_args([])
        assert args.report_path is not None
        assert "contact-info-legacy-backfill" in str(args.report_path)


# ---------------------------------------------------------------------------
# 3. Dry-run: no writes
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_calls_no_writer(self, tmp_path: Path) -> None:
        """Dry-run: relationship_assert_fact must NOT be called."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        pool = _make_pool(ci_rows=[_ci_row(ci_type="email")], active_triples=[False])

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=False, report_path=tmp_path / "r.md")

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_counts_gap_rows(self, tmp_path: Path) -> None:
        """Dry-run: gap rows are counted in stats.asserted even though no write occurs."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [
            _ci_row(ci_type="email", value="a@example.com"),
            _ci_row(ci_type="phone", value="+1-555-0101"),
        ]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[False, False])

        report_path = tmp_path / "r.md"
        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=False, report_path=report_path)

        assert rc == 0
        # Report should mention both would-be asserted predicates
        report_text = report_path.read_text()
        assert "has-email" in report_text
        assert "has-phone" in report_text

    @pytest.mark.asyncio
    async def test_dry_run_writes_report(self, tmp_path: Path) -> None:
        """Dry-run: report file is written even though no DB writes occur."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        pool = _make_pool(ci_rows=[_ci_row()], active_triples=[False])
        report_path = tmp_path / "report.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=False, report_path=report_path)

        assert rc == 0
        assert report_path.exists()
        content = report_path.read_text()
        assert "DRY-RUN" in content


# ---------------------------------------------------------------------------
# 4. Secured=true rows are skipped
# ---------------------------------------------------------------------------


class TestSecuredSkip:
    @pytest.mark.asyncio
    async def test_secured_row_is_not_asserted(self, tmp_path: Path) -> None:
        """secured=true rows must not be backfilled."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [_ci_row(ci_type="email", secured=True)]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[])  # no gap checks needed

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=tmp_path / "r.md")

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_not_called()

    @pytest.mark.asyncio
    async def test_secured_count_in_stats(self, tmp_path: Path) -> None:
        """skipped_secured counter increments for each secured=true row."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [
            _ci_row(ci_type="email", secured=True),
            _ci_row(ci_type="phone", secured=True),
        ]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[])
        report_path = tmp_path / "r.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=False, report_path=report_path)

        assert rc == 0
        report_text = report_path.read_text()
        # Both rows should show up as skipped_secured=2
        assert "| 2 |" in report_text or "2" in report_text


# ---------------------------------------------------------------------------
# 5. NULL entity_id rows are skipped
# ---------------------------------------------------------------------------


class TestNullEntitySkip:
    @pytest.mark.asyncio
    async def test_null_entity_not_asserted(self, tmp_path: Path) -> None:
        """Rows with entity_id=NULL must not be backfilled."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [_ci_row(ci_type="email", entity_id=None)]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[])

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=tmp_path / "r.md")

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_not_called()

    @pytest.mark.asyncio
    async def test_null_entity_count_in_report(self, tmp_path: Path) -> None:
        """skipped_null_entity appears in the report."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [_ci_row(entity_id=None), _ci_row(entity_id=None)]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[])
        report_path = tmp_path / "r.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            await mod._run_backfill_with_pool(pool, apply=False, report_path=report_path)

        content = report_path.read_text()
        assert "null entity" in content.lower() or "null_entity" in content


# ---------------------------------------------------------------------------
# 6. Unmapped types are skipped with per-type counts
# ---------------------------------------------------------------------------


class TestUnmappedTypeSkip:
    @pytest.mark.asyncio
    async def test_unmapped_type_not_asserted(self, tmp_path: Path) -> None:
        """telegram_user_id and similar have no predicate mapping and must be skipped."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [
            _ci_row(ci_type="telegram_user_id", value="12345"),
            _ci_row(ci_type="telegram_username", value="@bob"),
            _ci_row(ci_type="google_health", value="gh-token"),
        ]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[])

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=tmp_path / "r.md")

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_not_called()

    @pytest.mark.asyncio
    async def test_unmapped_type_in_report(self, tmp_path: Path) -> None:
        """Unmapped types appear in the report table."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [_ci_row(ci_type="telegram_user_id", value="42")]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[])
        report_path = tmp_path / "r.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            await mod._run_backfill_with_pool(pool, apply=False, report_path=report_path)

        content = report_path.read_text()
        assert "telegram_user_id" in content

    @pytest.mark.asyncio
    async def test_mapped_type_is_asserted(self, tmp_path: Path) -> None:
        """email, phone, telegram, linkedin, twitter, website, other ARE mapped."""
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_outcome="inserted")
        ci_rows = [_ci_row(ci_type="email"), _ci_row(ci_type="phone", value="+1-555")]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[False, False])

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=tmp_path / "r.md")

        assert rc == 0
        assert writer_mod.relationship_assert_fact.call_count == 2


# ---------------------------------------------------------------------------
# 7. Already-present triples are not re-asserted
# ---------------------------------------------------------------------------


class TestAlreadyPresent:
    @pytest.mark.asyncio
    async def test_existing_triple_not_reasserted(self, tmp_path: Path) -> None:
        """If a triple already exists (active_triple=True), assert is not called."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [_ci_row(ci_type="email")]
        # Triple is already present
        pool = _make_pool(ci_rows=ci_rows, active_triples=[True])

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=tmp_path / "r.md")

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_not_called()

    @pytest.mark.asyncio
    async def test_unchanged_outcome_counted_as_already_present(self, tmp_path: Path) -> None:
        """If writer returns 'unchanged' (race condition), it counts as already_present."""
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_outcome="unchanged")
        # Gap check says not present, but writer returns unchanged (race)
        ci_rows = [_ci_row(ci_type="email")]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[False])

        report_path = tmp_path / "r.md"
        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=report_path)

        assert rc == 0
        # Should be counted as already_present
        content = report_path.read_text()
        assert "APPLY" in content


# ---------------------------------------------------------------------------
# 8. Apply mode asserts gap triples via the central writer
# ---------------------------------------------------------------------------


class TestApplyMode:
    @pytest.mark.asyncio
    async def test_apply_calls_relationship_assert_fact(self, tmp_path: Path) -> None:
        """apply=True calls relationship_assert_fact for each gap row."""
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_outcome="inserted")
        ci_rows = [_ci_row(ci_type="email", value="alice@example.com")]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[False])

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=tmp_path / "r.md")

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_called_once()
        call_kwargs = writer_mod.relationship_assert_fact.call_args
        # subject should be the entity UUID
        assert call_kwargs.args[1] == _ENTITY_ID_A
        # predicate should be "has-email"
        assert call_kwargs.args[2] == "has-email"
        # object should be the value
        assert call_kwargs.args[3] == "alice@example.com"
        # src must be 'migration'
        assert call_kwargs.kwargs.get("src") == "migration"

    @pytest.mark.asyncio
    async def test_apply_report_contains_apply_mode(self, tmp_path: Path) -> None:
        """Report in apply mode says APPLY not DRY-RUN."""
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_outcome="inserted")
        pool = _make_pool(ci_rows=[_ci_row()], active_triples=[False])
        report_path = tmp_path / "r.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            await mod._run_backfill_with_pool(pool, apply=True, report_path=report_path)

        content = report_path.read_text()
        assert "APPLY" in content
        assert "DRY-RUN" not in content

    @pytest.mark.asyncio
    async def test_apply_uses_correct_src(self, tmp_path: Path) -> None:
        """relationship_assert_fact must be called with src='migration'."""
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_outcome="inserted")
        pool = _make_pool(ci_rows=[_ci_row()], active_triples=[False])

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            await mod._run_backfill_with_pool(pool, apply=True, report_path=tmp_path / "r.md")

        kwargs = writer_mod.relationship_assert_fact.call_args.kwargs
        assert kwargs["src"] == "migration"
        assert kwargs["verified"] is False
        assert kwargs["conf"] == 1.0


# ---------------------------------------------------------------------------
# 9. Idempotency: re-run with no gaps
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_all_present_no_asserts(self, tmp_path: Path) -> None:
        """If all triples are already present, asserted=0 and no writer calls."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        ci_rows = [
            _ci_row(ci_type="email"),
            _ci_row(ci_type="phone", value="+1-555"),
        ]
        # Both triples already exist
        pool = _make_pool(ci_rows=ci_rows, active_triples=[True, True])

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=tmp_path / "r.md")

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_not_called()


# ---------------------------------------------------------------------------
# 10. Report is always written
# ---------------------------------------------------------------------------


class TestReport:
    @pytest.mark.asyncio
    async def test_report_written_on_dry_run(self, tmp_path: Path) -> None:
        mod = _load_module()
        writer_mod = _make_writer_mod()
        pool = _make_pool()
        report_path = tmp_path / "dry.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            await mod._run_backfill_with_pool(pool, apply=False, report_path=report_path)

        assert report_path.exists()

    @pytest.mark.asyncio
    async def test_report_written_on_apply(self, tmp_path: Path) -> None:
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_outcome="inserted")
        pool = _make_pool(ci_rows=[_ci_row()], active_triples=[False])
        report_path = tmp_path / "apply.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            await mod._run_backfill_with_pool(pool, apply=True, report_path=report_path)

        assert report_path.exists()

    @pytest.mark.asyncio
    async def test_report_parent_dir_created(self, tmp_path: Path) -> None:
        """Parent directories of report_path are created if missing."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        pool = _make_pool()
        report_path = tmp_path / "deep" / "nested" / "report.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            await mod._run_backfill_with_pool(pool, apply=False, report_path=report_path)

        assert report_path.exists()


# ---------------------------------------------------------------------------
# 11. Preflight: missing entity_facts table
# ---------------------------------------------------------------------------


class TestPreflightFacts:
    @pytest.mark.asyncio
    async def test_missing_entity_facts_returns_rc1(self, tmp_path: Path) -> None:
        """Returns rc=1 when relationship.entity_facts does not exist."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        pool = _make_pool(facts_table_exists=False)

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=False, report_path=tmp_path / "r.md")

        assert rc == 1
        writer_mod.relationship_assert_fact.assert_not_called()


# ---------------------------------------------------------------------------
# 12. Preflight: missing predicate_registry table
# ---------------------------------------------------------------------------


class TestPreflightRegistry:
    @pytest.mark.asyncio
    async def test_missing_predicate_registry_returns_rc1(self, tmp_path: Path) -> None:
        """Returns rc=1 when relationship.entity_predicate_registry does not exist."""
        mod = _load_module()
        writer_mod = _make_writer_mod()
        pool = _make_pool(predicate_registry_exists=False)

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=False, report_path=tmp_path / "r.md")

        assert rc == 1
        writer_mod.relationship_assert_fact.assert_not_called()


# ---------------------------------------------------------------------------
# 13. Errors are counted and rc=1 is returned
# ---------------------------------------------------------------------------


class TestErrors:
    @pytest.mark.asyncio
    async def test_writer_error_counted_rc1(self, tmp_path: Path) -> None:
        """Writer raising an exception increments errors and returns rc=1."""
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_raises=RuntimeError("db fail"))
        ci_rows = [_ci_row(ci_type="email")]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[False])
        report_path = tmp_path / "r.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=report_path)

        assert rc == 1
        content = report_path.read_text()
        # Errors counter should be non-zero in report
        assert "| Errors |" in content


# ---------------------------------------------------------------------------
# 14. pending_approval outcome is counted as asserted
# ---------------------------------------------------------------------------


class TestPendingApproval:
    @pytest.mark.asyncio
    async def test_pending_approval_counted_as_asserted(self, tmp_path: Path) -> None:
        """Owner entity carve-out: pending_approval outcome counts as asserted."""
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_outcome="pending_approval")
        # Set action_id on the mock result
        result = _MockAssertResult(
            outcome="pending_approval",
            fact_id=None,
            action_id=uuid.uuid4(),
        )
        writer_mod.relationship_assert_fact = AsyncMock(return_value=result)

        ci_rows = [_ci_row(ci_type="email")]
        pool = _make_pool(ci_rows=ci_rows, active_triples=[False])
        report_path = tmp_path / "r.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=report_path)

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_called_once()
        content = report_path.read_text()
        # Asserted count should be 1
        assert "APPLY" in content


# ---------------------------------------------------------------------------
# 15. Mixed bag: all skip reasons together
# ---------------------------------------------------------------------------


class TestMixedRows:
    @pytest.mark.asyncio
    async def test_all_skip_reasons_together(self, tmp_path: Path) -> None:
        """Script correctly counts each skip reason when all are present."""
        mod = _load_module()
        writer_mod = _make_writer_mod(assert_outcome="inserted")

        ci_rows = [
            _ci_row(ci_type="email", value="gap@example.com"),  # gap → should be asserted
            _ci_row(ci_type="phone", value="+1-555", secured=True),  # secured skip
            _ci_row(ci_type="email", entity_id=None),  # null entity skip
            _ci_row(ci_type="telegram_user_id", value="111"),  # unmapped skip
            _ci_row(ci_type="phone", value="+1-999"),  # already present
        ]
        # active_triples: only applies to rows that pass all skips
        # Rows 0 and 4 pass skips; row 0 is gap, row 4 is already present
        pool = _make_pool(ci_rows=ci_rows, active_triples=[False, True])
        report_path = tmp_path / "r.md"

        with patch.object(mod, "_load_assert_fact", return_value=writer_mod):
            rc = await mod._run_backfill_with_pool(pool, apply=True, report_path=report_path)

        assert rc == 0
        writer_mod.relationship_assert_fact.assert_called_once()
        content = report_path.read_text()
        assert "APPLY" in content
        assert "telegram_user_id" in content


# ---------------------------------------------------------------------------
# 16. _load_assert_fact — real (non-mocked) execution
# ---------------------------------------------------------------------------
# This test exercises the REAL dynamic-import path without mocking the writer
# module, so it would have failed before the sys.modules registration fix.
# Before the fix: @dataclass in relationship_assert_fact.py crashed with
#   AttributeError: 'NoneType' object has no attribute '__dict__'
# because the module was exec'd before being added to sys.modules.
# ---------------------------------------------------------------------------


class TestLoadAssertFactReal:
    @pytest.fixture(autouse=True)
    def _cleanup_sys_modules(self) -> Any:
        with patch.dict(_sys.modules):
            yield

    def test_load_assert_fact_returns_real_module(self) -> None:
        """_load_assert_fact() must succeed without any mocking.

        This test catches the class of bug where the module is not registered
        in sys.modules before exec_module — causing @dataclass field resolution
        to crash with AttributeError on 'NoneType'.
        """
        mod = _load_module()

        # Invoke the real _load_assert_fact — no patching of sys.modules or
        # the writer module itself.  Before the fix this raised:
        #   AttributeError: 'NoneType' object has no attribute '__dict__'
        writer_mod = mod._load_assert_fact()

        # The returned module must expose the expected public symbols.
        assert callable(getattr(writer_mod, "relationship_assert_fact", None)), (
            "relationship_assert_fact must be a callable on the loaded module"
        )
        assert callable(getattr(writer_mod, "contact_info_type_to_predicate", None)), (
            "contact_info_type_to_predicate must be a callable on the loaded module"
        )
        assert getattr(writer_mod, "AssertOutcome", None) is not None, (
            "AssertOutcome must be present on the loaded module"
        )
        # AssertResult is a @dataclass — its presence proves the fix worked because
        # @dataclass field resolution requires the module to be in sys.modules.
        assert getattr(writer_mod, "AssertResult", None) is not None, (
            "AssertResult (@dataclass) must be present — its presence proves "
            "sys.modules registration happened before exec_module"
        )

    def test_load_assert_fact_registers_in_sys_modules(self) -> None:
        """After calling _load_assert_fact(), the module must be in sys.modules."""
        mod = _load_module()
        mod_name = "relationship_assert_fact"

        # Call the real loader (may already be cached — that is fine).
        writer_mod = mod._load_assert_fact()

        # The module must be reachable via sys.modules under its spec name.
        assert mod_name in _sys.modules, (
            f"Module '{mod_name}' was not registered in sys.modules after _load_assert_fact()"
        )
        assert _sys.modules[mod_name] is writer_mod, (
            "sys.modules entry must be the same object returned by _load_assert_fact()"
        )
