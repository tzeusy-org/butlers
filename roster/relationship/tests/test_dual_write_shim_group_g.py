"""Unit + parity tests for the dual-write shim — Group G contacts.py writers (bu-8m546).

Covers the four functions in ``roster/relationship/tools/contacts.py``:
  - ``contact_create``  — shim call-site fires; emit_all called after INSERT
  - ``contact_update``  — shim call-site fires; emit_all called after UPDATE
  - ``contact_archive`` — shim call-site fires; retract_all called after archive UPDATE
  - ``contact_merge``   — pre-fetch captured; retract_all called with prefetched snapshot

Design contract (Amendment 14):
  - SQL is authoritative.  Legacy write commits first; triple write is best-effort.
  - Shim failures are swallowed; legacy SQL commit is never blocked or rolled back.
  - Flag is read on every call via ``dual_write_enabled()``.

Test scope per function:
  (a) Flag off → shim called but returns early internally (no DB triple write).
  (b) Flag on → shim helper (emit_all / retract_all) is called after SQL commit.
  (c) Legacy SQL succeeds even when shim helper raises.
  (d) For contact_merge: pre-fetch happens before the transaction; retraction uses snapshot.

All tests are pure unit tests (no Docker / Postgres required).
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
_EMIT_ALL_PATCH = "butlers.tools.relationship.contacts.emit_all_contact_info_facts"
_RETRACT_ALL_PATCH = "butlers.tools.relationship.contacts.retract_all_contact_info_facts"

_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()
_SOURCE_ID = uuid.uuid4()
_TARGET_ID = uuid.uuid4()


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


def _make_contact_row(
    contact_id: uuid.UUID = _CONTACT_ID,
    entity_id: uuid.UUID = _ENTITY_ID,
) -> dict:
    """Minimal contacts row for mock RETURNING *."""
    return {
        "id": contact_id,
        "entity_id": entity_id,
        "first_name": "Alice",
        "last_name": "Smith",
        "nickname": None,
        "company": None,
        "job_title": None,
        "gender": None,
        "pronouns": None,
        "avatar_url": None,
        "listed": True,
        "archived_at": None,
        "metadata": {},
        "details": {},
        "name": "Alice Smith",
    }


def _make_create_pool(contact_row: dict) -> MagicMock:
    """Pool mock wired for contact_create.

    contact_create calls:
      1. pool.fetchrow('SELECT column_name...' schema introspection)  → list of col names
      2. entity creation (mocked via _ensure_entity patch)
      3. pool.fetchrow(INSERT ... RETURNING *)
    """
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[{"column_name": col} for col in contact_row])
    pool.fetchrow = AsyncMock(return_value=contact_row)
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=contact_row)
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


def _make_update_pool(contact_row: dict) -> MagicMock:
    """Pool mock wired for contact_update.

    contact_update calls:
      1. pool.fetchrow(SELECT * FROM contacts)  → existing row
      2. pool.fetchrow(UPDATE contacts ... RETURNING *)
      (table_columns is patched separately)
    """
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[contact_row, contact_row])
    conn = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


def _make_archive_pool(contact_row: dict) -> MagicMock:
    """Pool mock wired for contact_archive.

    contact_archive calls:
      1. pool.fetchrow(UPDATE contacts ... RETURNING *)
      (table_columns is patched separately)
    """
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=contact_row)
    return pool


def _make_merge_pool(
    source_row: dict,
    target_row: dict,
    updated_target_row: dict,
    ci_rows: list[dict] | None = None,
) -> MagicMock:
    """Pool mock wired for contact_merge.

    contact_merge calls:
      1. pool.fetchrow(SELECT * FROM contacts WHERE id = source_id)
      2. pool.fetchrow(SELECT * FROM contacts WHERE id = target_id)
      3. pool.fetch(SELECT * FROM contact_info WHERE contact_id = source_id)  [pre-fetch]
      4. pool.acquire() → conn for the transaction block
      5. pool.fetchrow(SELECT * FROM contacts WHERE id = target_id)  [final fetch]
      (table_columns is patched separately)
    """
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[source_row, target_row, updated_target_row])
    pool.fetch = AsyncMock(return_value=ci_rows or [])

    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


# ---------------------------------------------------------------------------
# Shared patches
# ---------------------------------------------------------------------------

_COLS = {
    "id",
    "entity_id",
    "first_name",
    "last_name",
    "nickname",
    "company",
    "job_title",
    "gender",
    "pronouns",
    "avatar_url",
    "listed",
    "archived_at",
    "metadata",
    "details",
    "name",
    "updated_at",
}


def _patch_table_columns(cols: set[str] = _COLS):
    return patch(
        "butlers.tools.relationship.contacts.table_columns",
        new=AsyncMock(return_value=cols),
    )


def _patch_ensure_entity(entity_id: uuid.UUID = _ENTITY_ID):
    return patch(
        "butlers.tools.relationship.contacts._ensure_entity",
        new=AsyncMock(return_value=str(entity_id)),
    )


# ===========================================================================
# Group G — contact_create
# ===========================================================================


class TestContactCreateDualWriteShim:
    """contact_create: emit_all_contact_info_facts is called after the INSERT."""

    async def test_emit_all_called_when_flag_on(self, monkeypatch):
        """(b) emit_all_contact_info_facts is called once after the INSERT commits."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_create

        contact_row = _make_contact_row()
        pool = _make_create_pool(contact_row)

        with _patch_table_columns(), _patch_ensure_entity():
            with patch(_EMIT_ALL_PATCH, new_callable=AsyncMock) as mock_emit:
                result = await contact_create(pool, first_name="Alice", last_name="Smith")

        mock_emit.assert_awaited_once()
        assert mock_emit.call_args.kwargs["contact_id"] == contact_row["id"]
        assert result["id"] == contact_row["id"]

    async def test_emit_all_called_when_flag_off(self, monkeypatch):
        """(a) emit_all_contact_info_facts is called even when flag is off (returns early internally)."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.contacts import contact_create

        contact_row = _make_contact_row()
        pool = _make_create_pool(contact_row)

        with _patch_table_columns(), _patch_ensure_entity():
            with patch(_EMIT_ALL_PATCH, new_callable=AsyncMock) as mock_emit:
                result = await contact_create(pool, first_name="Alice", last_name="Smith")

        # Call-site always invokes helper; helper checks flag internally.
        mock_emit.assert_awaited_once()
        assert result["id"] == contact_row["id"]

    async def test_shim_failure_does_not_block_return_value(self, monkeypatch):
        """(c) emit_all raising does not propagate — SQL result is returned."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_create

        contact_row = _make_contact_row()
        pool = _make_create_pool(contact_row)

        with _patch_table_columns(), _patch_ensure_entity():
            with patch(
                _EMIT_ALL_PATCH,
                new_callable=AsyncMock,
                side_effect=RuntimeError("triple store down"),
            ):
                # Must not raise — shim failure is swallowed at helper level,
                # and even if it propagates, the call-site should not mask SQL result.
                # (The helper itself already swallows; this test guards the call-site.)
                result = await contact_create(pool, first_name="Alice", last_name="Smith")

        assert result["id"] == contact_row["id"]


# ===========================================================================
# Group G — contact_update
# ===========================================================================


class TestContactUpdateDualWriteShim:
    """contact_update: emit_all_contact_info_facts is called after the UPDATE."""

    async def test_emit_all_called_when_flag_on(self, monkeypatch):
        """(b) emit_all_contact_info_facts is called once after the UPDATE commits."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_update

        contact_row = _make_contact_row()
        pool = _make_update_pool(contact_row)

        with _patch_table_columns():
            with patch(_EMIT_ALL_PATCH, new_callable=AsyncMock) as mock_emit:
                result = await contact_update(pool, _CONTACT_ID, first_name="Alicia")

        mock_emit.assert_awaited_once()
        assert mock_emit.call_args.kwargs["contact_id"] == _CONTACT_ID
        assert result["id"] == contact_row["id"]

    async def test_emit_all_called_when_flag_off(self, monkeypatch):
        """(a) emit_all called even when flag is off (returns early internally)."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.contacts import contact_update

        contact_row = _make_contact_row()
        pool = _make_update_pool(contact_row)

        with _patch_table_columns():
            with patch(_EMIT_ALL_PATCH, new_callable=AsyncMock) as mock_emit:
                result = await contact_update(pool, _CONTACT_ID, first_name="Alicia")

        mock_emit.assert_awaited_once()
        assert result["id"] == contact_row["id"]

    async def test_shim_failure_does_not_block_return_value(self, monkeypatch):
        """(c) emit_all raising does not affect the returned updated contact."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_update

        contact_row = _make_contact_row()
        pool = _make_update_pool(contact_row)

        with _patch_table_columns():
            with patch(
                _EMIT_ALL_PATCH,
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB down"),
            ):
                result = await contact_update(pool, _CONTACT_ID, first_name="Alicia")

        assert result["id"] == contact_row["id"]


# ===========================================================================
# Group G — contact_archive
# ===========================================================================


class TestContactArchiveDualWriteShim:
    """contact_archive: retract_all_contact_info_facts is called after the archive UPDATE."""

    async def test_retract_all_called_when_flag_on(self, monkeypatch):
        """(b) retract_all_contact_info_facts is called once after the UPDATE commits."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_archive

        contact_row = _make_contact_row()
        pool = _make_archive_pool(contact_row)

        with _patch_table_columns():
            with patch(_RETRACT_ALL_PATCH, new_callable=AsyncMock) as mock_retract:
                result = await contact_archive(pool, _CONTACT_ID)

        mock_retract.assert_awaited_once()
        assert mock_retract.call_args.kwargs["contact_id"] == _CONTACT_ID
        assert result["id"] == contact_row["id"]

    async def test_retract_all_called_when_flag_off(self, monkeypatch):
        """(a) retract_all called even when flag is off (returns early internally)."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.contacts import contact_archive

        contact_row = _make_contact_row()
        pool = _make_archive_pool(contact_row)

        with _patch_table_columns():
            with patch(_RETRACT_ALL_PATCH, new_callable=AsyncMock) as mock_retract:
                result = await contact_archive(pool, _CONTACT_ID)

        mock_retract.assert_awaited_once()
        assert result["id"] == contact_row["id"]

    async def test_shim_failure_does_not_block_return_value(self, monkeypatch):
        """(c) retract_all raising does not affect the archived contact result."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_archive

        contact_row = _make_contact_row()
        pool = _make_archive_pool(contact_row)

        with _patch_table_columns():
            with patch(
                _RETRACT_ALL_PATCH,
                new_callable=AsyncMock,
                side_effect=RuntimeError("triple store down"),
            ):
                result = await contact_archive(pool, _CONTACT_ID)

        assert result["id"] == contact_row["id"]

    async def test_contact_not_found_does_not_call_shim(self, monkeypatch):
        """contact_archive raises ValueError when contact not found; shim is never called."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_archive

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)

        with _patch_table_columns():
            with patch(_RETRACT_ALL_PATCH, new_callable=AsyncMock) as mock_retract:
                with pytest.raises(ValueError, match="not found"):
                    await contact_archive(pool, _CONTACT_ID)

        mock_retract.assert_not_called()


# ===========================================================================
# Group G — contact_merge
# ===========================================================================


class TestContactMergeDualWriteShim:
    """contact_merge: pre-fetch source contact_info rows; retract_all after merge."""

    def _make_ci_rows(self, contact_id: uuid.UUID) -> list[dict]:
        return [
            {"type": "email", "value": "src@example.com", "contact_id": contact_id},
            {"type": "phone", "value": "+15550001", "contact_id": contact_id},
        ]

    async def test_retract_all_called_with_prefetched_rows(self, monkeypatch):
        """(b+d) retract_all is called with the pre-merge contact_info snapshot."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_merge

        source_row = _make_contact_row(contact_id=_SOURCE_ID)
        target_row = _make_contact_row(contact_id=_TARGET_ID)
        ci_rows = self._make_ci_rows(_SOURCE_ID)
        pool = _make_merge_pool(source_row, target_row, target_row, ci_rows=ci_rows)

        with _patch_table_columns():
            with patch(_RETRACT_ALL_PATCH, new_callable=AsyncMock) as mock_retract:
                result = await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        mock_retract.assert_awaited_once()
        call_kwargs = mock_retract.call_args.kwargs
        assert call_kwargs["contact_id"] == _SOURCE_ID
        # Prefetched rows are passed so the retraction reflects pre-merge state.
        assert call_kwargs["prefetched_rows"] is not None
        assert len(call_kwargs["prefetched_rows"]) == 2
        assert result["id"] == _TARGET_ID

    async def test_retract_all_not_called_when_no_ci_rows(self, monkeypatch):
        """(b) If source has no contact_info rows the retraction shim is skipped."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_merge

        source_row = _make_contact_row(contact_id=_SOURCE_ID)
        target_row = _make_contact_row(contact_id=_TARGET_ID)
        pool = _make_merge_pool(source_row, target_row, target_row, ci_rows=[])

        with _patch_table_columns():
            with patch(_RETRACT_ALL_PATCH, new_callable=AsyncMock) as mock_retract:
                await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        mock_retract.assert_not_called()

    async def test_retract_all_called_when_flag_off(self, monkeypatch):
        """(a) retract_all is called even when flag is off (returns early internally)."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.contacts import contact_merge

        source_row = _make_contact_row(contact_id=_SOURCE_ID)
        target_row = _make_contact_row(contact_id=_TARGET_ID)
        ci_rows = self._make_ci_rows(_SOURCE_ID)
        pool = _make_merge_pool(source_row, target_row, target_row, ci_rows=ci_rows)

        with _patch_table_columns():
            with patch(_RETRACT_ALL_PATCH, new_callable=AsyncMock) as mock_retract:
                await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        mock_retract.assert_awaited_once()

    async def test_merge_succeeds_when_prefetch_fails(self, monkeypatch):
        """(c) If pre-fetch of source CI rows fails, merge still completes."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_merge

        source_row = _make_contact_row(contact_id=_SOURCE_ID)
        target_row = _make_contact_row(contact_id=_TARGET_ID)
        pool = _make_merge_pool(source_row, target_row, target_row, ci_rows=[])
        # Override fetch to raise, simulating pre-fetch failure.
        pool.fetch = AsyncMock(side_effect=ConnectionError("DB unreachable"))

        with _patch_table_columns():
            with patch(_RETRACT_ALL_PATCH, new_callable=AsyncMock) as mock_retract:
                result = await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        # Merge completes; no retraction was attempted (empty _src_ci_rows sentinel).
        mock_retract.assert_not_called()
        assert result["id"] == _TARGET_ID

    async def test_shim_failure_does_not_block_return_value(self, monkeypatch):
        """(c) retract_all raising does not affect the returned target contact."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contacts import contact_merge

        source_row = _make_contact_row(contact_id=_SOURCE_ID)
        target_row = _make_contact_row(contact_id=_TARGET_ID)
        ci_rows = self._make_ci_rows(_SOURCE_ID)
        pool = _make_merge_pool(source_row, target_row, target_row, ci_rows=ci_rows)

        with _patch_table_columns():
            with patch(
                _RETRACT_ALL_PATCH,
                new_callable=AsyncMock,
                side_effect=RuntimeError("triple store down"),
            ):
                result = await contact_merge(pool, _SOURCE_ID, _TARGET_ID)

        assert result["id"] == _TARGET_ID


# ===========================================================================
# emit_all_contact_info_facts and retract_all_contact_info_facts helpers
# ===========================================================================


class TestEmitAllContactInfoFacts:
    """Tests for the bulk emit helper added in dual_write.py (bu-8m546)."""

    async def test_flag_off_returns_early(self, monkeypatch):
        """Flag off → no fetch, no emit calls."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.dual_write import emit_all_contact_info_facts

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await emit_all_contact_info_facts(pool, contact_id=_CONTACT_ID)
        pool.fetch.assert_not_called()

    async def test_flag_on_no_ci_rows(self, monkeypatch):
        """Flag on, no contact_info rows → no emit calls."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_all_contact_info_facts

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        with patch(
            "butlers.tools.relationship.dual_write.emit_contact_info_fact",
            new_callable=AsyncMock,
        ) as mock_emit:
            await emit_all_contact_info_facts(pool, contact_id=_CONTACT_ID)
        mock_emit.assert_not_called()

    async def test_flag_on_emits_each_row(self, monkeypatch):
        """Flag on, two CI rows → emit called twice with correct args."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_all_contact_info_facts

        rows = [
            {"type": "email", "value": "a@example.com", "is_primary": True},
            {"type": "phone", "value": "+15550001", "is_primary": False},
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)

        with patch(
            "butlers.tools.relationship.dual_write.emit_contact_info_fact",
            new_callable=AsyncMock,
        ) as mock_emit:
            await emit_all_contact_info_facts(pool, contact_id=_CONTACT_ID)

        assert mock_emit.await_count == 2
        calls_kwargs = [c.kwargs for c in mock_emit.call_args_list]
        assert any(
            kw["ci_type"] == "email" and kw["value"] == "a@example.com" for kw in calls_kwargs
        )
        assert any(kw["ci_type"] == "phone" and kw["value"] == "+15550001" for kw in calls_kwargs)

    async def test_fetch_failure_swallowed(self, monkeypatch):
        """DB error during fetch is swallowed — no re-raise."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_all_contact_info_facts

        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=ConnectionError("DB down"))
        # Must not raise.
        await emit_all_contact_info_facts(pool, contact_id=_CONTACT_ID)


class TestRetractAllContactInfoFacts:
    """Tests for the bulk retract helper added in dual_write.py (bu-8m546)."""

    async def test_flag_off_returns_early(self, monkeypatch):
        """Flag off → no fetch, no retract calls."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.dual_write import retract_all_contact_info_facts

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await retract_all_contact_info_facts(pool, contact_id=_CONTACT_ID)
        pool.fetch.assert_not_called()

    async def test_flag_on_no_ci_rows(self, monkeypatch):
        """Flag on, no contact_info rows → no retract calls."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import retract_all_contact_info_facts

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        with patch(
            "butlers.tools.relationship.dual_write.retract_contact_info_fact",
            new_callable=AsyncMock,
        ) as mock_retract:
            await retract_all_contact_info_facts(pool, contact_id=_CONTACT_ID)
        mock_retract.assert_not_called()

    async def test_flag_on_retracts_each_fetched_row(self, monkeypatch):
        """Flag on, two CI rows fetched → retract called twice."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import retract_all_contact_info_facts

        rows = [
            {"type": "email", "value": "a@example.com"},
            {"type": "phone", "value": "+15550001"},
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=rows)

        with patch(
            "butlers.tools.relationship.dual_write.retract_contact_info_fact",
            new_callable=AsyncMock,
        ) as mock_retract:
            await retract_all_contact_info_facts(pool, contact_id=_CONTACT_ID)

        assert mock_retract.await_count == 2

    async def test_flag_on_uses_prefetched_rows_skips_db(self, monkeypatch):
        """When prefetched_rows is supplied, DB fetch is skipped."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import retract_all_contact_info_facts

        prefetched = [{"type": "email", "value": "snap@example.com"}]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])  # should NOT be called

        with patch(
            "butlers.tools.relationship.dual_write.retract_contact_info_fact",
            new_callable=AsyncMock,
        ) as mock_retract:
            await retract_all_contact_info_facts(
                pool, contact_id=_CONTACT_ID, prefetched_rows=prefetched
            )

        pool.fetch.assert_not_called()
        mock_retract.assert_awaited_once()
        assert mock_retract.call_args.kwargs["ci_type"] == "email"
        assert mock_retract.call_args.kwargs["value"] == "snap@example.com"

    async def test_fetch_failure_swallowed(self, monkeypatch):
        """DB error during fetch is swallowed — no re-raise."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import retract_all_contact_info_facts

        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=ConnectionError("DB down"))
        # Must not raise.
        await retract_all_contact_info_facts(pool, contact_id=_CONTACT_ID)
