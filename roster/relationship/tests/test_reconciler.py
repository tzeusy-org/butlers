"""Unit tests for the dual-write reconciler job (Amendment 14).

Issue: bu-75a3s
Parent epic: bu-ao6uh (entity-redesign)
Spec anchor: Brief §6b Amendment 14 (Reconciler job) + tasks.md §10.9.

Covers:
  (a) No rows missing → noop (rows_reconciled=0).
  (b) Rows missing → emitted via relationship_assert_fact (rows_reconciled>0).
  (c) Already-have-triple (unchanged outcome from writer) → rows_skipped.
  (d) secured=true → rows_skipped_credential.
  (e) Owner subject → carve-out path (pending_approval outcome → rows_carveout).
  (f) Writer raises → rows_error.
  (g) Unrecognised ci_type → rows_skipped_no_predicate.
  (h) Env-var interval override is respected.
  (i) entity_id IS NULL → rows_skipped_orphan (query guard).

All tests are pure unit tests (no Docker/Postgres required). The asyncpg pool
and relationship_assert_fact() are mocked via unittest.mock.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit

# The reconciler imports relationship_assert_fact inside run_contact_info_reconciler
# using a local import:
#   from butlers.tools.relationship.relationship_assert_fact import (...)
# To intercept that call, we patch it in the source module where it lives.
_WRITER_PATCH_TARGET = (
    "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"
)


# ---------------------------------------------------------------------------
# Helpers — build a mock asyncpg pool
# ---------------------------------------------------------------------------


_REGISTERED_TEST_PREDICATES = {
    "has-email",
    "has-phone",
    "has-handle",
    "has-website",
}


def _registry_rows(predicates: set[str] | None = None) -> list[dict]:
    if predicates is None:
        predicates = _REGISTERED_TEST_PREDICATES
    return [{"predicate": predicate} for predicate in sorted(predicates)]


def _make_pool(rows: list[dict], *, registered_predicates: set[str] | None = None) -> AsyncMock:
    """Return a mock asyncpg.Pool whose fetch() returns *rows*."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=[_registry_rows(registered_predicates), rows])
    pool.execute = AsyncMock(return_value="OK")
    return pool


_UNSET = object()  # sentinel to distinguish "use default uuid" from "pass None"


def _ci_row(
    *,
    ci_type: str = "email",
    ci_value: str = "alice@example.com",
    entity_id: UUID | None | object = _UNSET,
    secured: bool = False,
    is_primary: bool = False,
) -> dict:
    """Build a synthetic contact_info sweep row.

    entity_id:
        When omitted (default), a fresh random UUID is generated.
        Pass ``None`` explicitly to simulate a row where entity_id IS NULL
        (orphan contact / defensive-guard test).
    """
    resolved_entity_id = uuid4() if entity_id is _UNSET else entity_id
    return {
        "ci_id": uuid4(),
        "contact_id": uuid4(),
        "ci_type": ci_type,
        "ci_value": ci_value,
        "is_primary": is_primary,
        "secured": secured,
        "ci_created_at": None,
        "entity_id": resolved_entity_id,
    }


# ---------------------------------------------------------------------------
# Fixture: patch state_set so tests don't need a real DB for checkpoint write
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_state_set(monkeypatch):
    """Suppress the state_set() checkpoint call in all reconciler tests."""
    monkeypatch.setattr(
        "butlers.core.state.state_set",
        AsyncMock(return_value=None),
    )


# ---------------------------------------------------------------------------
# (a) No rows missing → noop
# ---------------------------------------------------------------------------


class TestNoopWhenNoMissingRows:
    async def test_empty_sweep_returns_zero_reconciled(self):
        """When the sweep returns no rows, the job is a noop."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        pool = _make_pool(rows=[])
        result = await run_contact_info_reconciler(pool)

        assert result["rows_scanned"] == 0
        assert result["rows_reconciled"] == 0
        assert result["rows_skipped"] == 0
        assert result["rows_error"] == 0

    async def test_empty_sweep_no_assert_calls(self):
        """relationship_assert_fact must not be called when there's nothing to reconcile."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        pool = _make_pool(rows=[])
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await run_contact_info_reconciler(pool)
            mock_writer.assert_not_called()


# ---------------------------------------------------------------------------
# (b) Rows missing → emitted via writer (rows_reconciled > 0)
# ---------------------------------------------------------------------------


class TestMissingRowsAreReconciled:
    async def test_single_missing_row_is_reconciled(self):
        """A contact_info row missing its triple is reconciled via the writer."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        entity_id = uuid4()
        row = _ci_row(ci_type="email", ci_value="bob@example.com", entity_id=entity_id)
        pool = _make_pool(rows=[row])

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
        ) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_scanned"] == 1
        assert result["rows_reconciled"] == 1
        assert result["rows_error"] == 0

        mock_writer.assert_called_once()
        call_args = mock_writer.call_args
        # positional: pool, entity_id, predicate, object
        assert call_args[0][1] == entity_id  # subject
        assert call_args[0][2] == "has-email"  # predicate
        assert call_args[0][3] == "bob@example.com"  # object
        assert call_args[1]["src"] == "reconciler"

    async def test_superseded_outcome_counts_as_reconciled(self):
        """A 'superseded' outcome from the writer also increments rows_reconciled."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type="phone", ci_value="+15550001234")
        pool = _make_pool(rows=[row])

        superseded_result = AssertResult(outcome=AssertOutcome.superseded, fact_id=uuid4())

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=superseded_result):
            result = await run_contact_info_reconciler(pool)

        assert result["rows_reconciled"] == 1
        assert result["rows_skipped"] == 0

    async def test_multiple_rows_all_reconciled(self):
        """Multiple missing rows are all reconciled."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        rows = [
            _ci_row(ci_type="email", ci_value="a@example.com"),
            _ci_row(ci_type="email", ci_value="b@example.com"),
            _ci_row(ci_type="phone", ci_value="+15550001111"),
        ]
        pool = _make_pool(rows=rows)

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
        ) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_scanned"] == 3
        assert result["rows_reconciled"] == 3
        assert mock_writer.call_count == 3


# ---------------------------------------------------------------------------
# (c) Already-have-triple → rows_skipped (race with concurrent write)
# ---------------------------------------------------------------------------


class TestAlreadyHaveTriple:
    async def test_unchanged_outcome_increments_rows_skipped(self):
        """When the writer returns 'unchanged', the reconciler counts it as skipped."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type="email", ci_value="existing@example.com")
        pool = _make_pool(rows=[row])

        unchanged_result = AssertResult(outcome=AssertOutcome.unchanged, fact_id=uuid4())

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=unchanged_result):
            result = await run_contact_info_reconciler(pool)

        assert result["rows_skipped"] == 1
        assert result["rows_reconciled"] == 0


# ---------------------------------------------------------------------------
# (d) secured=true → rows_skipped_credential
# ---------------------------------------------------------------------------


class TestSecuredRowsSkipped:
    async def test_secured_row_increments_credential_counter(self):
        """Rows with secured=true are skipped (credential carve-out)."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        # The sweep query already excludes secured rows at the DB level.
        # This tests the defensive in-Python guard for rows that somehow slip through.
        row = _ci_row(ci_type="email", ci_value="cred@example.com", secured=True)
        pool = _make_pool(rows=[row])

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_skipped_credential"] == 1
        assert result["rows_reconciled"] == 0
        mock_writer.assert_not_called()


# ---------------------------------------------------------------------------
# (e) Owner subject → carve-out (pending_approval)
# ---------------------------------------------------------------------------


class TestOwnerCarveOut:
    async def test_owner_entity_triggers_carveout_counter(self):
        """When the writer returns pending_approval for an owner entity, rows_carveout is incremented."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        owner_entity_id = uuid4()
        row = _ci_row(
            ci_type="email",
            ci_value="owner@example.com",
            entity_id=owner_entity_id,
        )
        pool = _make_pool(rows=[row])

        carveout_result = AssertResult(
            outcome=AssertOutcome.pending_approval, fact_id=None, action_id=uuid4()
        )

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=carveout_result
        ) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_carveout"] == 1
        assert result["rows_reconciled"] == 0
        assert result["rows_error"] == 0
        # The writer IS still called — carve-out logic lives inside the writer.
        mock_writer.assert_called_once()

    async def test_owner_carveout_does_not_increment_reconciled(self):
        """Owner carve-out must not be counted as reconciled."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type="email", ci_value="owner@example.com")
        pool = _make_pool(rows=[row])

        carveout_result = AssertResult(
            outcome=AssertOutcome.pending_approval, fact_id=None, action_id=uuid4()
        )

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=carveout_result):
            result = await run_contact_info_reconciler(pool)

        assert result["rows_reconciled"] == 0
        assert result["rows_carveout"] == 1


# ---------------------------------------------------------------------------
# (f) Writer raises → rows_error (no re-raise; job continues)
# ---------------------------------------------------------------------------


class TestWriterError:
    async def test_writer_exception_increments_rows_error(self):
        """When the writer raises, the reconciler logs and increments rows_error."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type="email", ci_value="fail@example.com")
        pool = _make_pool(rows=[row])

        with patch(
            _WRITER_PATCH_TARGET,
            new_callable=AsyncMock,
            side_effect=RuntimeError("DB connection lost"),
        ):
            result = await run_contact_info_reconciler(pool)

        assert result["rows_error"] == 1
        assert result["rows_reconciled"] == 0

    async def test_writer_error_does_not_abort_subsequent_rows(self):
        """An error on one row must not prevent subsequent rows from being processed."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        rows = [
            _ci_row(ci_type="email", ci_value="fail@example.com"),
            _ci_row(ci_type="email", ci_value="ok@example.com"),
        ]
        pool = _make_pool(rows=rows)

        ok_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())
        call_count = 0

        async def _writer_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("transient error")
            return ok_result

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock, side_effect=_writer_side_effect):
            result = await run_contact_info_reconciler(pool)

        assert result["rows_error"] == 1
        assert result["rows_reconciled"] == 1

    async def test_sweep_query_failure_returns_error_stats(self):
        """If the sweep query itself fails, the job returns with rows_error>=1."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=[_registry_rows(), RuntimeError("Connection refused")])

        result = await run_contact_info_reconciler(pool)

        assert result["rows_error"] >= 1
        assert result["rows_scanned"] == 0

    async def test_registry_fetch_failure_returns_error_stats(self):
        """If the predicate registry lookup fails, the job aborts before the sweep."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("Connection refused"))

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_error"] == 1
        assert result["rows_scanned"] == 0
        assert pool.fetch.await_count == 1
        mock_writer.assert_not_called()


# ---------------------------------------------------------------------------
# (g) Unrecognised ci_type → rows_skipped_no_predicate
# ---------------------------------------------------------------------------


class TestUnrecognisedType:
    async def test_unknown_type_is_skipped_not_errored(self):
        """Contact info rows with an unrecognised type are skipped, not errored."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type="fax", ci_value="+15559999999")
        pool = _make_pool(rows=[row])

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_skipped_no_predicate"] == 1
        assert result["rows_error"] == 0
        assert result["rows_reconciled"] == 0
        mock_writer.assert_not_called()

    async def test_empty_ci_type_is_skipped(self):
        """An empty string ci_type is skipped (no predicate available)."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type="", ci_value="something")
        pool = _make_pool(rows=[row])

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_skipped_no_predicate"] == 1
        mock_writer.assert_not_called()

    async def test_mapped_type_with_unregistered_predicate_is_skipped(self):
        """Mapped ci_types are skipped when the DB registry is missing the predicate."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type="phone", ci_value="+15559999999")
        pool = _make_pool(rows=[row], registered_predicates={"has-email"})

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_skipped_no_predicate"] == 1
        assert result["rows_error"] == 0
        assert result["rows_reconciled"] == 0
        mock_writer.assert_not_called()

    async def test_unregistered_predicate_warns_once_per_predicate(self, caplog):
        """Repeated rows for one missing predicate emit one warning per run."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        rows = [
            _ci_row(ci_type="phone", ci_value="+15550000001"),
            _ci_row(ci_type="phone", ci_value="+15550000002"),
        ]
        pool = _make_pool(rows=rows, registered_predicates={"has-email"})

        with (
            caplog.at_level(logging.WARNING),
            patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer,
        ):
            result = await run_contact_info_reconciler(pool)

        warnings = [
            record for record in caplog.records if "predicate=has-phone" in record.getMessage()
        ]
        assert result["rows_skipped_no_predicate"] == 2
        assert result["rows_error"] == 0
        assert len(warnings) == 1
        mock_writer.assert_not_called()

    async def test_empty_registry_skips_mapped_predicates(self, caplog):
        """An empty registry is registry drift, not a writer error per row."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type="email", ci_value="missing-registry@example.com")
        pool = _make_pool(rows=[row], registered_predicates=set())

        with (
            caplog.at_level(logging.WARNING),
            patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer,
        ):
            result = await run_contact_info_reconciler(pool)

        warnings = [
            record for record in caplog.records if "predicate=has-email" in record.getMessage()
        ]
        assert result["rows_skipped_no_predicate"] == 1
        assert result["rows_error"] == 0
        assert len(warnings) == 1
        mock_writer.assert_not_called()


# ---------------------------------------------------------------------------
# (h) Env-var interval override
# ---------------------------------------------------------------------------


class TestIntervalConfig:
    def test_default_interval_is_30_minutes(self):
        """Default interval is 30 minutes when no env var is set."""
        import os

        from roster.relationship.jobs.relationship_jobs import (
            _RECONCILER_DEFAULT_INTERVAL_MINUTES,
            _reconciler_interval_minutes,
        )

        os.environ.pop("BUTLERS_CONTACT_INFO_RECONCILER_INTERVAL_MINUTES", None)
        assert _reconciler_interval_minutes() == _RECONCILER_DEFAULT_INTERVAL_MINUTES

    def test_env_var_overrides_default_interval(self, monkeypatch):
        """BUTLERS_CONTACT_INFO_RECONCILER_INTERVAL_MINUTES overrides the default."""
        from roster.relationship.jobs.relationship_jobs import _reconciler_interval_minutes

        monkeypatch.setenv("BUTLERS_CONTACT_INFO_RECONCILER_INTERVAL_MINUTES", "60")
        assert _reconciler_interval_minutes() == 60

    def test_invalid_env_var_falls_back_to_default(self, monkeypatch):
        """Non-integer env var value falls back to the default interval."""
        from roster.relationship.jobs.relationship_jobs import (
            _RECONCILER_DEFAULT_INTERVAL_MINUTES,
            _reconciler_interval_minutes,
        )

        monkeypatch.setenv("BUTLERS_CONTACT_INFO_RECONCILER_INTERVAL_MINUTES", "not-a-number")
        assert _reconciler_interval_minutes() == _RECONCILER_DEFAULT_INTERVAL_MINUTES

    async def test_stats_include_interval_minutes(self):
        """The returned stats dict includes interval_minutes."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        pool = _make_pool(rows=[])
        result = await run_contact_info_reconciler(pool)
        assert "interval_minutes" in result
        assert result["interval_minutes"] > 0


# ---------------------------------------------------------------------------
# (i) entity_id IS NULL → rows_skipped_orphan (defensive guard)
# ---------------------------------------------------------------------------


class TestOrphanSkip:
    async def test_null_entity_id_increments_orphan_counter(self):
        """Rows with entity_id=None are counted as skipped_orphan (defensive guard)."""
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        # entity_id=None mimics a row that slipped past the sweep query's IS NOT NULL guard.
        row = _ci_row(ci_type="email", ci_value="orphan@example.com", entity_id=None)
        pool = _make_pool(rows=[row])

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_skipped_orphan"] == 1
        assert result["rows_reconciled"] == 0
        mock_writer.assert_not_called()


# ---------------------------------------------------------------------------
# Predicate mapping — parametrized coverage
# ---------------------------------------------------------------------------


class TestPredicateMapping:
    @pytest.mark.parametrize(
        "ci_type, expected_predicate",
        [
            ("email", "has-email"),
            ("phone", "has-phone"),
            ("telegram", "has-handle"),
            ("telegram_user_id", "has-handle"),
            ("telegram_username", "has-handle"),
            ("linkedin", "has-handle"),
            ("twitter", "has-handle"),
            ("website", "has-website"),
        ],
    )
    async def test_ci_type_maps_to_correct_predicate(self, ci_type, expected_predicate):
        """Verify each known ci_type maps to the right predicate."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        row = _ci_row(ci_type=ci_type, ci_value="test-value")
        pool = _make_pool(rows=[row])

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
        ) as mock_writer:
            await run_contact_info_reconciler(pool)

        assert mock_writer.call_count == 1
        args = mock_writer.call_args[0]
        assert args[2] == expected_predicate, (
            f"Expected predicate {expected_predicate!r} for ci_type={ci_type!r}, got {args[2]!r}"
        )


# ---------------------------------------------------------------------------
# Telegram encoding — reconciler maps telegram_user_id and telegram_username
# to has-handle WITH the "telegram:" prefix (bead bu-wni4z).
#
# Canonical encoding (bead bu-wni4z):
#   Both the backfill script (backfill_contact_info_triples.py) and this
#   reconciler call relationship_assert_fact() with object="telegram:<ci_value>".
#   The prefix disambiguates telegram entries from linkedin/twitter/other
#   has-handle entries so the daemon read path can find the right row.
#
# Previously (before bu-wni4z) these were stored verbatim (no prefix), which
# silently broke notify(channel='telegram') via contact_id resolution.
# ---------------------------------------------------------------------------


class TestTelegramTypesReconcilerEncoding:
    """telegram_user_id and telegram_username are stored as has-handle with 'telegram:' prefix."""

    async def test_telegram_user_id_reconciles_with_prefix(self):
        """telegram_user_id rows produce a has-handle triple with 'telegram:<id>' as object.

        The 'telegram:' prefix is required so the daemon resolver can filter
        telegram entries from linkedin/twitter/other has-handle rows via LIKE.
        """
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        numeric_id = "86807245"
        entity_id = uuid4()
        row = _ci_row(ci_type="telegram_user_id", ci_value=numeric_id, entity_id=entity_id)
        pool = _make_pool(rows=[row])

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
        ) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_reconciled"] == 1
        assert result["rows_error"] == 0
        assert mock_writer.call_count == 1

        args = mock_writer.call_args[0]
        assert args[1] == entity_id, "subject must be the resolved entity_id"
        assert args[2] == "has-handle", "telegram_user_id must map to has-handle"
        # Object must carry the 'telegram:' prefix (bu-wni4z canonical encoding).
        expected_object = f"telegram:{numeric_id}"
        assert args[3] == expected_object, (
            f"object must be {expected_object!r} (prefixed); got {args[3]!r}"
        )
        assert mock_writer.call_args[1]["src"] == "reconciler"

    async def test_telegram_username_reconciles_with_prefix(self):
        """telegram_username rows produce a has-handle triple with 'telegram:<username>' as object.

        The prefix is required for disambiguation; the read path's ef_predicate_to_ci_type
        checks startswith('telegram:') to classify the entry as 'telegram_user_id'.
        """
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        username = "alice_tg"
        entity_id = uuid4()
        row = _ci_row(ci_type="telegram_username", ci_value=username, entity_id=entity_id)
        pool = _make_pool(rows=[row])

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
        ) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_reconciled"] == 1
        assert result["rows_error"] == 0
        assert mock_writer.call_count == 1

        args = mock_writer.call_args[0]
        assert args[1] == entity_id, "subject must be the resolved entity_id"
        assert args[2] == "has-handle", "telegram_username must map to has-handle"
        expected_object = f"telegram:{username}"
        assert args[3] == expected_object, (
            f"object must be {expected_object!r} (prefixed); got {args[3]!r}"
        )

    async def test_telegram_prefix_is_idempotent(self):
        """If ci_value already has 'telegram:' prefix, it must not be double-prefixed."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        already_prefixed = "telegram:86807245"
        entity_id = uuid4()
        row = _ci_row(ci_type="telegram_user_id", ci_value=already_prefixed, entity_id=entity_id)
        pool = _make_pool(rows=[row])

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
        ) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_reconciled"] == 1
        args = mock_writer.call_args[0]
        # Must not double-prefix: 'telegram:86807245' stays 'telegram:86807245'
        assert args[3] == already_prefixed, (
            f"already-prefixed value must not be re-prefixed; got {args[3]!r}"
        )

    async def test_telegram_types_not_skipped_as_unmapped(self):
        """Neither telegram_user_id nor telegram_username falls through to rows_skipped_no_predicate.

        Regression guard: before bead bu-55ggu / bu-8qma8 these types were
        absent from _CI_TYPE_TO_PREDICATE and were silently skipped.
        """
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        rows = [
            _ci_row(ci_type="telegram_user_id", ci_value="12345"),
            _ci_row(ci_type="telegram_username", ci_value="bob_tg"),
        ]
        pool = _make_pool(rows=rows)
        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result):
            result = await run_contact_info_reconciler(pool)

        assert result["rows_skipped_no_predicate"] == 0, (
            "telegram_user_id and telegram_username must not be skipped as unmapped"
        )
        assert result["rows_reconciled"] == 2

    async def test_non_telegram_has_handle_not_prefixed(self):
        """linkedin and twitter has-handle entries must NOT get the 'telegram:' prefix."""
        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        rows = [
            _ci_row(ci_type="linkedin", ci_value="alice-smith"),
            _ci_row(ci_type="twitter", ci_value="@alice"),
            _ci_row(ci_type="other", ci_value="somehandle"),
        ]
        pool = _make_pool(rows=rows)
        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid4())

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
        ) as mock_writer:
            result = await run_contact_info_reconciler(pool)

        assert result["rows_reconciled"] == 3
        for call in mock_writer.call_args_list:
            obj = call[0][3]
            assert not obj.startswith("telegram:"), (
                f"non-telegram handle must not be prefixed; got {obj!r}"
            )


# ---------------------------------------------------------------------------
# Job registry — reconciler retired in migration bead 10 (bu-e2ja9 / core_115)
# ---------------------------------------------------------------------------


class TestJobRegistration:
    def test_reconciler_retired_from_job_registry(self):
        """contact_info_reconciler is retired: public.contact_info is dropped, so
        it must NOT be wired into the relationship job registry (re-adding it would
        throw UndefinedTableError every 30 minutes)."""
        from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

        registry = get_deterministic_schedule_job_registry()
        assert "relationship" in registry
        assert "contact_info_reconciler" not in registry["relationship"], (
            "contact_info_reconciler must be retired from the registry (bu-e2ja9)"
        )
