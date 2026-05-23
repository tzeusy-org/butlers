"""Parity tests for dual-write shim Group C — telegram_chat_id enrichment.

``_enrich_telegram_chat_ids`` (contacts/__init__.py) inserts rows with
``type='telegram_chat_id'`` into ``public.contact_info``.  After each
committed INSERT it calls ``emit_contact_info_fact()`` best-effort.

Design contract (Amendment 14):
- SQL is authoritative.  The legacy INSERT commits first; the shim is
  post-commit and best-effort.
- ``telegram_chat_id`` is NOT in ``_CI_TYPE_TO_PREDICATE``, so
  ``emit_contact_info_fact()`` will log a debug skip and return early
  (unmapped-type skip).  The shim call is kept for pattern consistency.
- Shim failures are swallowed; the enrichment counter still increments.
- The shim is gated by ``BUTLERS_CONTACT_INFO_DUAL_WRITE``.

Test scope:
  (a) Flag on  → emit_contact_info_fact called with correct args per chat entry.
  (b) Flag off → emit_contact_info_fact still called (helper checks flag internally).
  (c) Shim raises → failure swallowed; enrichment proceeds normally.
  (d) Unmapped-type skip → shim call is made even though the type is unmapped;
      helper returns early; verified by confirming the function is called and
      the reconciler's predicate map has no entry for telegram_chat_id.

[bu-3jfvv]
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.contacts import _enrich_telegram_chat_ids
from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

pytestmark = pytest.mark.unit

_FLAG_ENV = "BUTLERS_CONTACT_INFO_DUAL_WRITE"
# Patch in the source module so the deferred-import path in the call site resolves correctly.
_EMIT_FACT_PATCH = "butlers.tools.relationship.dual_write.emit_contact_info_fact"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider_with_mapping(user_to_chat: dict[int, int]) -> MagicMock:
    """Return a mock TelegramContactsProvider with enrich_chat_ids returning user_to_chat."""
    provider = MagicMock()
    provider.enrich_chat_ids = AsyncMock(return_value=user_to_chat)
    return provider


def _make_pool(local_contact_id: uuid.UUID | None) -> Any:
    """Return a pool mock.

    fetchrow returns a row with local_contact_id when not None, else None
    (simulates a contact not found in contacts_source_links).
    """
    pool = AsyncMock()
    if local_contact_id is None:
        pool.fetchrow = AsyncMock(return_value=None)
    else:
        mock_row = MagicMock()
        mock_row.__getitem__.side_effect = lambda k: {"local_contact_id": local_contact_id}[k]
        pool.fetchrow = AsyncMock(return_value=mock_row)
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Unmapped-type contract
# ---------------------------------------------------------------------------


class TestTelegramChatIdUnmappedType:
    def test_telegram_chat_id_not_in_predicate_map(self) -> None:
        """``telegram_chat_id`` returns None from the predicate map — it is intentionally unmapped.

        Chat IDs are routing identifiers, not user-facing handles.  This confirms the
        option-A decision: the shim no-ops for this type without predicate-map changes.
        """
        assert contact_info_type_to_predicate("telegram_chat_id") is None

    def test_telegram_handle_type_is_mapped(self) -> None:
        """Sanity: plain 'telegram' (handle) IS in the map, so the two types are distinct."""
        assert contact_info_type_to_predicate("telegram") == "has-handle"


# ---------------------------------------------------------------------------
# Dual-write shim parity tests
# ---------------------------------------------------------------------------


class TestEnrichTelegramChatIdsDualWriteShim:
    """_enrich_telegram_chat_ids: emit_contact_info_fact is called after each INSERT."""

    async def test_emit_fact_called_when_flag_on(self, monkeypatch) -> None:
        """(a) emit_contact_info_fact is called once per enriched entry when flag is on."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool(contact_id)
        provider = _make_provider_with_mapping({100: 200})

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await _enrich_telegram_chat_ids(provider, pool)

        mock_emit.assert_awaited_once()
        call_kwargs = mock_emit.call_args.kwargs
        assert call_kwargs["contact_id"] == contact_id
        assert call_kwargs["ci_type"] == "telegram_chat_id"
        assert call_kwargs["value"] == "200"
        assert call_kwargs["is_primary"] is False
        assert call_kwargs["src"] == "dual-write"

    async def test_emit_fact_called_when_flag_off(self, monkeypatch) -> None:
        """(b) emit_contact_info_fact is called even when flag is off (helper short-circuits internally)."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)

        contact_id = uuid.uuid4()
        pool = _make_pool(contact_id)
        provider = _make_provider_with_mapping({100: 200})

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await _enrich_telegram_chat_ids(provider, pool)

        # Call-site always invokes helper; the helper's dual_write_enabled() check
        # causes it to return early rather than the call-site skipping it.
        mock_emit.assert_awaited_once()

    async def test_shim_failure_swallowed_enrichment_completes(self, monkeypatch) -> None:
        """(c) emit_contact_info_fact raising is swallowed; enrichment count logs normally."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool(contact_id)
        provider = _make_provider_with_mapping({100: 200})

        with patch(
            _EMIT_FACT_PATCH,
            new_callable=AsyncMock,
            side_effect=RuntimeError("triple store down"),
        ):
            # Must not raise
            await _enrich_telegram_chat_ids(provider, pool)

        # SQL INSERT still executed
        pool.execute.assert_awaited_once()

    async def test_sql_committed_before_shim_call(self, monkeypatch) -> None:
        """SQL INSERT is always executed regardless of shim outcome."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool(contact_id)
        provider = _make_provider_with_mapping({42: 99})

        call_order: list[str] = []
        pool.execute = AsyncMock(side_effect=lambda *a, **kw: call_order.append("sql") or None)

        async def _record_emit(*_args: Any, **_kw: Any) -> None:
            call_order.append("shim")

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock, side_effect=_record_emit):
            await _enrich_telegram_chat_ids(provider, pool)

        assert call_order == ["sql", "shim"], f"Expected sql before shim, got: {call_order}"

    async def test_emit_called_for_each_entry(self, monkeypatch) -> None:
        """emit_contact_info_fact is called once per enriched user when multiple mappings exist."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        # Three distinct contacts in the pool
        ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        pool = AsyncMock()
        rows = [MagicMock() for _ in ids]
        for row, cid in zip(rows, ids):
            row.__getitem__.side_effect = (lambda c: lambda k: {"local_contact_id": c}[k])(cid)
        pool.fetchrow = AsyncMock(side_effect=rows)
        pool.execute = AsyncMock()

        provider = _make_provider_with_mapping({1: 101, 2: 102, 3: 103})

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await _enrich_telegram_chat_ids(provider, pool)

        assert mock_emit.await_count == 3

    async def test_no_emit_when_no_source_link_found(self, monkeypatch) -> None:
        """Shim is NOT called when contacts_source_links has no match (fetchrow returns None)."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        pool = _make_pool(None)  # fetchrow returns None → contact not found
        provider = _make_provider_with_mapping({100: 200})

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await _enrich_telegram_chat_ids(provider, pool)

        mock_emit.assert_not_awaited()
        pool.execute.assert_not_awaited()

    async def test_no_emit_when_provider_returns_empty(self, monkeypatch) -> None:
        """Shim is NOT called when enrich_chat_ids returns an empty mapping."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool(contact_id)
        provider = _make_provider_with_mapping({})

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await _enrich_telegram_chat_ids(provider, pool)

        mock_emit.assert_not_awaited()
        pool.execute.assert_not_awaited()

    async def test_provider_failure_no_emit(self, monkeypatch) -> None:
        """When enrich_chat_ids raises, the enrichment exits early; shim is never called."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool(contact_id)
        provider = MagicMock()
        provider.enrich_chat_ids = AsyncMock(side_effect=RuntimeError("network error"))

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await _enrich_telegram_chat_ids(provider, pool)

        mock_emit.assert_not_awaited()
