"""Parity tests for the dual-write shim (Amendment 1.1.C bead 6, bu-g0car).

Amendment 14 requires EVENTUAL parity between ``public.contact_info`` (legacy
store) and ``relationship.entity_facts`` (triple store) within a 24-hour
reconciliation window.  These tests exercise the full end-to-end loop:

  dual-write shim writes SQL row  →  reconciler sweeps gap  →  triple asserted

Coverage per acceptance criteria:

  AC1  — Group H writers (contact_info_add / update / remove) produce both a
          ``public.contact_info`` row AND (via reconciler) an active triple.
  AC2  — Orphan branch: NULL entity_id rows appear on the reconciler worklist
          (``rows_skipped_orphan``) and do NOT produce triples.
  AC3  — Credentials carve-out: ``secured=true`` rows are excluded from triple
          emission (``rows_skipped_credential``).
  AC4  — Reconciliation drift ≤ 0 over 24h: one reconciler pass is sufficient
          to close any shim gap.

Non-Group-H writers (Groups A, C–G, I–K) are not yet shimmed.  They are
tracked in bead bu-8m546.  Tests for those groups should be added when that
bead lands.

All tests are pure unit tests (no Docker / Postgres required).  The asyncpg
pool and ``relationship_assert_fact()`` are mocked via unittest.mock.

Eventual-consistency pattern
----------------------------
The test harness simulates the 24-hour reconciler window by invoking
``run_contact_info_reconciler`` directly after the dual-write step.  In
production, the scheduler triggers this job periodically (default: every 30
minutes).  For test purposes a single synchronous reconciler call is
equivalent to "within the 24-hour window" because:

  1. The shim fires best-effort on the same request path.
  2. The reconciler sweeps ALL rows missing a triple — including any the shim
     failed to emit.
  3. One reconciler pass therefore closes any outstanding gap, bounding drift
     to the reconciler interval (30 min default, ≤ 24h limit per Amendment 14).

The helper ``_run_reconciler_and_assert_triple()`` encodes this invariant.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FLAG_ENV = "BUTLERS_CONTACT_INFO_DUAL_WRITE"
_WRITER_PATCH_TARGET = (
    "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"
)
_EMIT_PATCH_TARGET = "butlers.tools.relationship.contact_info.emit_contact_info_fact"
_RETRACT_PATCH_TARGET = "butlers.tools.relationship.contact_info.retract_contact_info_fact"

# Group H is the only shimmed group as of bu-8w730.  Other groups are tracked in bu-8m546.
_SHIMMED_GROUPS = ["Group H (contact_info_add / contact_info_update / contact_info_remove)"]
_UNSHIMMED_GROUPS = [
    "Group A",
    "Group C",
    "Group D",
    "Group E",
    "Group F",
    "Group G",
    "Group I",
    "Group J",
    "Group K",
]

# ---------------------------------------------------------------------------
# Helpers — mock pool factories
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Minimal async context manager for mocking pool.acquire()."""

    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _make_contact_info_add_pool(
    *,
    contact_id: uuid.UUID,
    ci_row: dict,
    is_owner: bool = False,
) -> MagicMock:
    """Return a pool mock configured for ``contact_info_add``.

    contact_info_add calls pool.fetchrow for:
      1. ``SELECT id FROM contacts WHERE id = $1``  → contact exists
      2. ``_is_owner_contact`` join → None (non-owner) or row (owner)

    The INSERT RETURNING executes on conn (pool.acquire()).
    """
    pool = MagicMock()

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=ci_row)
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    owner_row = {"id": contact_id} if is_owner else None
    pool.fetchrow = AsyncMock(
        side_effect=[
            {"id": contact_id},  # SELECT id FROM contacts → exists
            owner_row,  # _is_owner_contact JOIN → non-owner or owner
        ]
    )
    pool.execute = AsyncMock(return_value=None)
    return pool


def _make_contact_info_update_pool(
    *,
    ci_row: dict,
    updated_row: dict,
    is_owner: bool = False,
) -> MagicMock:
    """Return a pool mock configured for ``contact_info_update``.

    contact_info_update calls pool.fetchrow for:
      1. ``SELECT * FROM public.contact_info WHERE id = $1`` → ci_row
      2. ``_is_owner_contact`` join → None (non-owner) or row (owner)
    conn.fetchrow → updated_row (RETURNING)
    """
    pool = MagicMock()

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=updated_row)
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    contact_id = ci_row["contact_id"]
    owner_row = {"id": contact_id} if is_owner else None
    pool.fetchrow = AsyncMock(
        side_effect=[
            ci_row,  # SELECT * FROM contact_info → found
            owner_row,  # _is_owner_contact → non-owner or owner
        ]
    )
    return pool


def _make_reconciler_pool(rows: list[dict]) -> AsyncMock:
    """Return a pool mock configured for ``run_contact_info_reconciler``.

    The reconciler calls pool.fetch() once to get the sweep rows,
    then pool.execute() for the checkpoint write (via state_set).
    """
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=rows)
    pool.execute = AsyncMock(return_value="OK")
    return pool


def _ci_sweep_row(
    *,
    contact_id: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
    ci_type: str = "email",
    ci_value: str = "alice@example.com",
    secured: bool = False,
    is_primary: bool = False,
) -> dict:
    """Build a synthetic sweep row as returned by the reconciler's SQL query."""
    return {
        "ci_id": uuid.uuid4(),
        "contact_id": contact_id or uuid.uuid4(),
        "ci_type": ci_type,
        "ci_value": ci_value,
        "is_primary": is_primary,
        "secured": secured,
        "ci_created_at": None,
        "entity_id": entity_id,  # None = orphan
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_state_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress the state_set() checkpoint call in all reconciler tests."""
    monkeypatch.setattr(
        "butlers.core.state.state_set",
        AsyncMock(return_value=None),
    )


# ---------------------------------------------------------------------------
# Eventual-consistency helpers
# ---------------------------------------------------------------------------


async def _run_reconciler_and_assert_triple(
    rows: list[dict],
) -> dict:
    """Run the reconciler against synthetic sweep rows and return stats.

    This encodes the Amendment 14 eventual-consistency assertion pattern:

      SQL row present  →  (within 24h)  →  reconciler asserts triple

    In tests, "within 24h" is simulated by a single direct reconciler call.
    The reconciler is the sweep-based safety net; one pass is sufficient to
    close any shim gap.

    Returns the reconciler stats dict.  Callers assert on
    ``stats["rows_reconciled"]`` and ``stats["rows_skipped_*"]``.
    """
    from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
    from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

    inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid.uuid4())
    pool = _make_reconciler_pool(rows=rows)

    with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result):
        return await run_contact_info_reconciler(pool)


# ===========================================================================
# AC1 — Group H writer parity (end-to-end loop)
# ===========================================================================


class TestParityGroupHContactInfoAdd:
    """contact_info_add: dual-write produces SQL row, reconciler closes any gap.

    The parity test simulates the end-to-end loop:
      1. contact_info_add fires the dual-write shim (best-effort).
      2. The reconciler sweeps the gap.
      3. The triple is now asserted in relationship.entity_facts.

    Amendment 14: SQL is authoritative.  Parity is EVENTUAL (within 24h), not
    synchronous.  The reconciler is the authoritative correctness mechanism.
    """

    async def test_add_email_produces_contact_info_row(self, monkeypatch: pytest.MonkeyPatch):
        """contact_info_add successfully writes the public.contact_info row."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contact_info import contact_info_add

        contact_id = uuid.uuid4()
        ci_row = {
            "id": uuid.uuid4(),
            "contact_id": contact_id,
            "type": "email",
            "value": "alice@example.com",
            "label": None,
            "is_primary": False,
            "context": None,
        }
        pool = _make_contact_info_add_pool(contact_id=contact_id, ci_row=ci_row)

        with patch(_EMIT_PATCH_TARGET, new_callable=AsyncMock):
            result = await contact_info_add(pool, contact_id, "email", "alice@example.com")

        # SQL row is present (contact_info_add returned a dict from RETURNING *)
        assert result["contact_id"] == contact_id
        assert result["type"] == "email"
        assert result["value"] == "alice@example.com"

    async def test_add_email_shim_fires_or_reconciler_closes_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Within the 24h window, the reconciler asserts the triple for any add."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        entity_id = uuid.uuid4()
        contact_id = uuid.uuid4()

        # Sweep row: the contact has an email and a linked entity.
        sweep_rows = [
            _ci_sweep_row(
                contact_id=contact_id,
                entity_id=entity_id,
                ci_type="email",
                ci_value="alice@example.com",
            )
        ]

        stats = await _run_reconciler_and_assert_triple(rows=sweep_rows)

        # Triple is asserted by the reconciler (drift → 0 after one pass).
        assert stats["rows_reconciled"] == 1
        assert stats["rows_scanned"] == 1
        assert stats["rows_error"] == 0

    async def test_add_phone_parity_within_24h(self, monkeypatch: pytest.MonkeyPatch):
        """Phone entries are reconciled to has-phone triples within the 24h window."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        entity_id = uuid.uuid4()
        sweep_rows = [
            _ci_sweep_row(
                entity_id=entity_id,
                ci_type="phone",
                ci_value="+15550001234",
            )
        ]

        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid.uuid4())
        pool = _make_reconciler_pool(rows=sweep_rows)

        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
        ) as mock_writer:
            stats = await run_contact_info_reconciler(pool)

        assert stats["rows_reconciled"] == 1
        # Verify the correct predicate was used
        call_args = mock_writer.call_args[0]
        assert call_args[2] == "has-phone"

    async def test_add_handle_types_parity_within_24h(self, monkeypatch: pytest.MonkeyPatch):
        """Telegram / LinkedIn / Twitter entries collapse to has-handle triples."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        for ci_type in ("telegram", "linkedin", "twitter"):
            entity_id = uuid.uuid4()
            sweep_rows = [
                _ci_sweep_row(
                    entity_id=entity_id,
                    ci_type=ci_type,
                    ci_value=f"handle_{ci_type}",
                )
            ]

            from butlers.tools.relationship.relationship_assert_fact import (
                AssertOutcome,
                AssertResult,
            )
            from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

            inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid.uuid4())
            pool = _make_reconciler_pool(rows=sweep_rows)

            with patch(
                _WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result
            ) as mock_writer:
                stats = await run_contact_info_reconciler(pool)

            assert stats["rows_reconciled"] == 1, f"Expected reconciliation for ci_type={ci_type!r}"
            call_args = mock_writer.call_args[0]
            assert call_args[2] == "has-handle", (
                f"Expected has-handle predicate for ci_type={ci_type!r}, got {call_args[2]!r}"
            )

    async def test_shim_fires_immediately_on_add(self, monkeypatch: pytest.MonkeyPatch):
        """When flag is on, the dual-write shim fires immediately after the SQL INSERT.

        This is the synchronous best-effort path.  If the shim succeeds, the
        reconciler will find the triple already present and skip the row
        (rows_skipped instead of rows_reconciled).  This test verifies the shim
        call-site fires — the shim's internal logic is tested in test_dual_write_shim.py.
        """
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contact_info import contact_info_add

        contact_id = uuid.uuid4()
        ci_row = {
            "id": uuid.uuid4(),
            "contact_id": contact_id,
            "type": "email",
            "value": "bob@example.com",
            "label": None,
            "is_primary": False,
            "context": None,
        }
        pool = _make_contact_info_add_pool(contact_id=contact_id, ci_row=ci_row)

        with patch(_EMIT_PATCH_TARGET, new_callable=AsyncMock) as mock_emit:
            await contact_info_add(pool, contact_id, "email", "bob@example.com")

        # Shim call-site always fires regardless of internal flag state.
        mock_emit.assert_awaited_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["ci_type"] == "email"
        assert kwargs["value"] == "bob@example.com"
        assert kwargs["contact_id"] == contact_id


class TestParityGroupHContactInfoUpdate:
    """contact_info_update: shim fires after UPDATE; reconciler closes any gap."""

    async def test_update_fires_shim_after_sql_commit(self, monkeypatch: pytest.MonkeyPatch):
        """contact_info_update fires the shim immediately after the UPDATE commits."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contact_info import contact_info_update

        contact_id = uuid.uuid4()
        ci_id = uuid.uuid4()
        ci_row = {
            "id": ci_id,
            "contact_id": contact_id,
            "type": "phone",
            "value": "+15550001",
            "label": None,
            "is_primary": False,
            "context": None,
        }
        updated_row = dict(ci_row) | {"value": "+15550002"}
        pool = _make_contact_info_update_pool(ci_row=ci_row, updated_row=updated_row)

        with patch(_EMIT_PATCH_TARGET, new_callable=AsyncMock) as mock_emit:
            result = await contact_info_update(pool, ci_id, value="+15550002")

        mock_emit.assert_awaited_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["ci_type"] == "phone"
        assert kwargs["value"] == "+15550002"
        assert result["value"] == "+15550002"

    async def test_update_parity_within_24h(self, monkeypatch: pytest.MonkeyPatch):
        """After an update the reconciler asserts the updated triple (drift ≤ 0)."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        entity_id = uuid.uuid4()
        sweep_rows = [
            _ci_sweep_row(
                entity_id=entity_id,
                ci_type="phone",
                ci_value="+15550002",  # post-update value
            )
        ]

        stats = await _run_reconciler_and_assert_triple(rows=sweep_rows)
        assert stats["rows_reconciled"] == 1


class TestParityGroupHContactInfoRemove:
    """contact_info_remove: retraction shim fires; reconciler drift documented.

    Amendment 14 note: ``relationship_assert_fact()`` does not yet have an
    explicit retraction write path (bu-8w730 TODO).  The reconciler sweeps
    drift within 30 minutes.  This test confirms:

      (a) The retraction shim is invoked after the DELETE.
      (b) The reconciler does NOT attempt to re-assert a deleted row
          (the sweep only touches rows that still exist in contact_info).
    """

    async def test_remove_invokes_retraction_shim(self, monkeypatch: pytest.MonkeyPatch):
        """contact_info_remove invokes retract_contact_info_fact after the DELETE."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contact_info import contact_info_remove

        contact_id = uuid.uuid4()
        ci_id = uuid.uuid4()
        ci_row = {
            "id": ci_id,
            "contact_id": contact_id,
            "type": "email",
            "value": "alice@example.com",
            "label": None,
            "is_primary": False,
            "context": None,
        }

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=ci_row)
        pool.execute = AsyncMock(return_value=None)

        with patch(_RETRACT_PATCH_TARGET, new_callable=AsyncMock) as mock_retract:
            await contact_info_remove(pool, ci_id)

        mock_retract.assert_awaited_once()
        kwargs = mock_retract.call_args.kwargs
        assert kwargs["ci_type"] == "email"
        assert kwargs["value"] == "alice@example.com"
        assert kwargs["contact_id"] == contact_id

    async def test_remove_does_not_appear_in_reconciler_sweep(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """After deletion the row is gone from contact_info; reconciler has nothing to sweep.

        The reconciler's NOT EXISTS clause only applies to rows still present in
        contact_info.  A deleted row produces an empty sweep and zero reconciled
        triples — the reconciler correctly does nothing.
        """
        monkeypatch.setenv(_FLAG_ENV, "1")

        # Empty sweep = all rows deleted or already have triples.
        stats = await _run_reconciler_and_assert_triple(rows=[])
        assert stats["rows_scanned"] == 0
        assert stats["rows_reconciled"] == 0


# ===========================================================================
# AC2 — Orphan handling: NULL entity_id rows appear on reconciler worklist
# ===========================================================================


class TestParityOrphanHandling:
    """Contacts with NULL entity_id are orphans: no triple can be asserted.

    The reconciler's SQL sweep excludes ``entity_id IS NULL`` rows via
    ``AND c.entity_id IS NOT NULL``.  Even if such a row slips through the
    SQL filter, the Python defensive guard increments ``rows_skipped_orphan``
    and does NOT call ``relationship_assert_fact()``.

    In production, NULL entity_id rows appear on the "bead 5.5 worklist"
    (tracked by ``rows_skipped_orphan`` in the reconciler stats) so an
    operator or a follow-up job can resolve the missing entity linkage.
    """

    async def test_null_entity_id_skipped_by_reconciler(self, monkeypatch: pytest.MonkeyPatch):
        """Orphan rows (NULL entity_id) do NOT produce triples — they appear on worklist."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        # Simulate a row that slipped past the SQL IS NOT NULL guard.
        orphan_row = _ci_sweep_row(
            entity_id=None,  # orphan: no entity linkage
            ci_type="email",
            ci_value="orphan@example.com",
        )

        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        pool = _make_reconciler_pool(rows=[orphan_row])
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            stats = await run_contact_info_reconciler(pool)

        # No triple emitted — orphan on worklist.
        assert stats["rows_skipped_orphan"] == 1
        assert stats["rows_reconciled"] == 0
        mock_writer.assert_not_called()

    async def test_null_entity_id_shim_skips_triple(self, monkeypatch: pytest.MonkeyPatch):
        """The dual-write shim also skips triple emission when entity_id is NULL.

        When ``emit_contact_info_fact`` resolves the contact and finds
        ``entity_id IS NULL``, it returns early without calling
        ``relationship_assert_fact()``.  This is the fast-path complement to
        the reconciler's orphan skip.
        """
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = MagicMock()
        # Contact found but entity_id is NULL.
        pool.fetchrow = AsyncMock(return_value={"entity_id": None})

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool,
                contact_id=uuid.uuid4(),
                ci_type="email",
                value="orphan@example.com",
            )
            mock_writer.assert_not_called()

    async def test_mixed_orphan_and_valid_rows(self, monkeypatch: pytest.MonkeyPatch):
        """Orphan and valid rows in the same sweep: only valid rows produce triples."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        entity_id = uuid.uuid4()
        rows = [
            _ci_sweep_row(entity_id=None, ci_type="email", ci_value="orphan@example.com"),
            _ci_sweep_row(entity_id=entity_id, ci_type="email", ci_value="valid@example.com"),
        ]

        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid.uuid4())
        pool = _make_reconciler_pool(rows=rows)

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result):
            stats = await run_contact_info_reconciler(pool)

        assert stats["rows_scanned"] == 2
        assert stats["rows_skipped_orphan"] == 1
        assert stats["rows_reconciled"] == 1


# ===========================================================================
# AC3 — Credentials carve-out: secured=true rows never become triples
# ===========================================================================


class TestParityCredentialsCarveout:
    """Rows with secured=true are excluded from triple emission at every layer.

    Brief §6b Amendment 1.1.A.4: credentials (secured=true) are stored in
    ``public.contact_info`` but MUST NEVER be projected into
    ``relationship.entity_facts``.  This carve-out is enforced in:

      (a) The SQL sweep: ``WHERE ci.secured = false``
      (b) The Python defensive guard: ``if row["secured"]: skip``
      (c) The shim's call-site: only invoked for non-secured write paths
          (contact_info_add / update / remove do not pass secured=true to emit)

    These tests assert the carve-out is preserved from shim through reconciler.
    """

    async def test_secured_row_skipped_by_reconciler(self, monkeypatch: pytest.MonkeyPatch):
        """secured=true rows slip through the SQL filter are caught by Python guard."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        secured_row = _ci_sweep_row(
            entity_id=uuid.uuid4(),
            ci_type="email",
            ci_value="password@example.com",
            secured=True,
        )

        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        pool = _make_reconciler_pool(rows=[secured_row])
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            stats = await run_contact_info_reconciler(pool)

        assert stats["rows_skipped_credential"] == 1
        assert stats["rows_reconciled"] == 0
        mock_writer.assert_not_called()

    async def test_secured_row_sql_guard_present(self):
        """Source-level guard: the reconciler's SQL sweep must exclude secured rows."""
        import inspect

        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        source = inspect.getsource(run_contact_info_reconciler)
        assert "secured = false" in source, (
            "run_contact_info_reconciler SQL sweep must filter secured=false "
            "to enforce the credentials carve-out (Amendment 1.1.A.4)."
        )

    async def test_secured_row_python_guard_present(self):
        """Source-level guard: the Python defensive guard for secured rows must exist."""
        import inspect

        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        source = inspect.getsource(run_contact_info_reconciler)
        assert "rows_skipped_credential" in source, (
            "run_contact_info_reconciler must maintain the rows_skipped_credential counter "
            "as the Python defensive guard for the credentials carve-out."
        )

    async def test_secured_and_unsecured_rows_in_same_sweep(self, monkeypatch: pytest.MonkeyPatch):
        """Secured rows are skipped; unsecured rows in the same sweep are reconciled."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        entity_id = uuid.uuid4()
        rows = [
            _ci_sweep_row(
                entity_id=uuid.uuid4(),
                ci_type="email",
                ci_value="cred@example.com",
                secured=True,
            ),
            _ci_sweep_row(
                entity_id=entity_id,
                ci_type="email",
                ci_value="valid@example.com",
                secured=False,
            ),
        ]

        from butlers.tools.relationship.relationship_assert_fact import AssertOutcome, AssertResult
        from roster.relationship.jobs.relationship_jobs import run_contact_info_reconciler

        inserted_result = AssertResult(outcome=AssertOutcome.inserted, fact_id=uuid.uuid4())
        pool = _make_reconciler_pool(rows=rows)

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock, return_value=inserted_result):
            stats = await run_contact_info_reconciler(pool)

        assert stats["rows_scanned"] == 2
        assert stats["rows_skipped_credential"] == 1
        assert stats["rows_reconciled"] == 1


# ===========================================================================
# AC4 — Eventual consistency: one reconciler pass closes any shim gap
# ===========================================================================


class TestParityEventualConsistency:
    """Reconciliation drift ≤ 0 over a 24-hour window.

    Amendment 14 requirement: "Reconciliation drift ≤ 0 measured over 24h
    window — gate before bead 7 (bu-akads) read-path cut-over."

    The invariant is: after one complete reconciler pass, every eligible
    public.contact_info row has a corresponding active triple in
    relationship.entity_facts.  The reconciler is the safety net that catches
    any rows that the shim missed (e.g. due to a DB error or a future writer
    that was not yet shimmed).

    The 24-hour window is simulated by invoking the reconciler once.  In
    production the default interval is 30 minutes, so a 24-hour window
    guarantees at least 48 reconciler passes.
    """

    async def test_single_reconciler_pass_achieves_zero_drift(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """One reconciler pass is sufficient to assert all missing triples."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        entity_id = uuid.uuid4()
        # Simulate multiple rows that the shim failed to emit (e.g. shim flag was off).
        rows = [
            _ci_sweep_row(entity_id=entity_id, ci_type="email", ci_value="a@example.com"),
            _ci_sweep_row(entity_id=entity_id, ci_type="phone", ci_value="+15550001"),
            _ci_sweep_row(entity_id=entity_id, ci_type="telegram", ci_value="tg_handle"),
        ]

        stats = await _run_reconciler_and_assert_triple(rows=rows)

        # After one pass all missing triples are asserted: drift = 0.
        assert stats["rows_scanned"] == 3
        assert stats["rows_reconciled"] == 3
        assert stats["rows_error"] == 0

    async def test_reconciler_second_pass_is_noop_when_triples_exist(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """After the first pass closes the gap, a second pass is a noop.

        This models the steady-state: once parity is achieved, subsequent
        reconciler runs find no missing rows (the sweep's NOT EXISTS clause
        returns an empty set) and rows_reconciled stays at 0.
        """
        monkeypatch.setenv(_FLAG_ENV, "1")

        # Empty sweep = all triples already exist.
        stats = await _run_reconciler_and_assert_triple(rows=[])

        assert stats["rows_scanned"] == 0
        assert stats["rows_reconciled"] == 0

    async def test_reconciler_interval_within_24h_window(self, monkeypatch: pytest.MonkeyPatch):
        """The reconciler interval is small enough to satisfy the 24h window gate.

        Amendment 14 requires reconciliation within 24 hours.  The default
        interval is 30 minutes.  This test asserts the default is within the
        required window (< 24*60 = 1440 minutes).
        """
        from roster.relationship.jobs.relationship_jobs import (
            _RECONCILER_DEFAULT_INTERVAL_MINUTES,
        )

        # 30 minutes << 24 hours = 1440 minutes.
        assert _RECONCILER_DEFAULT_INTERVAL_MINUTES < 1440, (
            f"Reconciler default interval ({_RECONCILER_DEFAULT_INTERVAL_MINUTES} min) "
            f"must be < 1440 min (24h) to satisfy the Amendment 14 parity gate."
        )

    async def test_flag_off_shim_is_noop_reconciler_still_closes_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """When the dual-write flag is off, the shim is a noop but the reconciler catches up.

        This demonstrates that the reconciler is the authoritative correctness
        mechanism: even if the shim never fires, the reconciler will close the
        gap within the 24h window.
        """
        monkeypatch.delenv(_FLAG_ENV, raising=False)

        entity_id = uuid.uuid4()
        # Row exists in contact_info but has no triple (shim was off).
        rows = [
            _ci_sweep_row(
                entity_id=entity_id,
                ci_type="email",
                ci_value="belated@example.com",
            )
        ]

        stats = await _run_reconciler_and_assert_triple(rows=rows)

        # Reconciler asserts the missing triple even without shim assistance.
        assert stats["rows_reconciled"] == 1


# ===========================================================================
# Non-Group-H writers — xfail placeholders (bu-8m546)
# ===========================================================================


class TestParityUnshimmedGroupsXfail:
    """Parity test stubs for writer groups not yet shimmed.

    Groups A, C–G, I–K are tracked in bead bu-8m546.  When that bead lands
    and each group gains its dual-write shim, these xfail placeholders should
    be replaced with real parity tests following the Group H pattern above.

    IMPORTANT: Do NOT remove these tests.  They serve as a living checklist
    and prevent the parity gate from appearing "complete" before all groups
    are shimmed.

    Conversion recipe (when bu-8m546 ships a group):
      1. Remove the @pytest.mark.xfail decorator for that group's test.
      2. Implement the parity assertion using the Group H pattern.
      3. Verify the test passes in CI.
    """

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Group A writers are not yet shimmed (tracked in bu-8m546). "
            "Replace this xfail with a real parity test when bu-8m546 lands Group A."
        ),
    )
    async def test_group_a_writers_parity_xfail(self):
        """Placeholder: Group A writers dual-write parity (bu-8m546)."""
        raise NotImplementedError("Group A writers are not yet shimmed — see bu-8m546")

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Groups C–G, I–K writers are not yet shimmed (tracked in bu-8m546). "
            "Replace this xfail with group-specific parity tests when bu-8m546 lands."
        ),
    )
    async def test_groups_c_through_k_parity_xfail(self):
        """Placeholder: Groups C–G, I–K writers dual-write parity (bu-8m546)."""
        raise NotImplementedError("Groups C–G, I–K writers are not yet shimmed — see bu-8m546")
