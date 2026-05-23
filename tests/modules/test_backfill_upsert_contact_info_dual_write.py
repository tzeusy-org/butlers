"""Parity tests for dual-write shim Group D — backfill upsert_contact_info.

``ContactBackfillWriter.upsert_contact_info`` (backfill.py) inserts rows with
variable types (email, phone, website, telegram_username, etc.) into
``public.contact_info``.  After each new INSERT it calls
``emit_contact_info_fact()`` best-effort (Amendment 14).

Design contract:
- SQL is authoritative.  The legacy INSERT commits first; the shim is
  post-commit and best-effort.
- ``emit_contact_info_fact()`` is called only when the INSERT actually created
  a row (asyncpg status == "INSERT 0 1").  When ON CONFLICT DO NOTHING silently
  skips the insert because the (type, value) pair is already claimed by a
  *different* contact, the shim is NOT called — calling it would assert a triple
  linking the caller's entity to a value it does not own, contradicting the
  authoritative SQL state.
- Mapped types (email, phone) invoke the triple store via the predicate map.
  Unmapped types (address, telegram_chat_id, telegram_username, telegram_user_id)
  are skipped inside the helper — no call-site filtering is needed.
- Shim failures are swallowed; the SQL commit is never rolled back.
- The shim is gated by ``BUTLERS_CONTACT_INFO_DUAL_WRITE``.

Test scope:
  (a) Mapped type (email) flag on  → emit_contact_info_fact called with correct args.
  (b) Mapped type (phone) flag on  → emit_contact_info_fact called with correct args.
  (c) Unmapped type (address/telegram_username) → shim still called; helper no-ops internally.
  (d) Flag off → shim still called (helper short-circuits internally).
  (e) ON CONFLICT skip (row already exists for another contact) → shim NOT called.
  (f) Shim raises → failure swallowed; no exception propagated.
  (g) SQL INSERT executed before shim (Amendment 14 ordering).
  (h) Multiple entries in one call → shim called once per new INSERT.

[bu-3jfvv]
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.modules.contacts.backfill import ContactBackfillWriter
from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

pytestmark = pytest.mark.unit

_FLAG_ENV = "BUTLERS_CONTACT_INFO_DUAL_WRITE"
# Patch at the source so the deferred-import path resolves correctly.
_EMIT_FACT_PATCH = "butlers.tools.relationship.dual_write.emit_contact_info_fact"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_writer(pool: Any) -> ContactBackfillWriter:
    """Build a minimal ContactBackfillWriter with a mock pool."""
    writer = ContactBackfillWriter.__new__(ContactBackfillWriter)
    writer._pool = pool  # type: ignore[attr-defined]
    writer._provider = "test"  # type: ignore[attr-defined]
    writer._table_flags: dict[str, bool] = {}  # type: ignore[attr-defined]
    writer._table_flags_loaded = True  # type: ignore[attr-defined]
    return writer


def _make_canonical_contact(
    *,
    emails: list[str] | None = None,
    phones: list[str] | None = None,
    urls: list[str] | None = None,
    usernames: list[tuple[str, str]] | None = None,  # (service, value)
    external_id: str | None = None,
) -> MagicMock:
    """Build a minimal CanonicalContact mock."""
    contact = MagicMock()

    def _make_email(v: str) -> MagicMock:
        m = MagicMock()
        m.value = v
        m.label = None
        m.primary = False
        return m

    def _make_phone(v: str) -> MagicMock:
        m = MagicMock()
        m.value = v
        m.label = None
        m.primary = False
        return m

    def _make_url(v: str) -> MagicMock:
        m = MagicMock()
        m.value = v
        m.label = None
        return m

    def _make_username(service: str, v: str) -> MagicMock:
        m = MagicMock()
        m.service = service
        m.value = v
        return m

    contact.emails = [_make_email(e) for e in (emails or [])]
    contact.phones = [_make_phone(p) for p in (phones or [])]
    contact.urls = [_make_url(u) for u in (urls or [])]
    contact.usernames = [_make_username(s, v) for s, v in (usernames or [])]
    contact.external_id = external_id
    contact.addresses = []
    return contact


def _make_pool_no_existing() -> Any:
    """Pool mock where contact_info row does NOT yet exist; INSERT succeeds (status INSERT 0 1)."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    return pool


def _make_pool_on_conflict() -> Any:
    """Pool mock simulating ON CONFLICT DO NOTHING skip (value owned by another contact).

    fetchrow returns None (no row for THIS contact), but execute returns "INSERT 0 0"
    indicating the INSERT was silently skipped by the UNIQUE(type, value) constraint.
    """
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="INSERT 0 0")
    return pool


def _make_pool_existing_row(row_id: uuid.UUID, is_primary: bool = False) -> Any:
    """Pool mock where contact_info row ALREADY EXISTS (simulates existing-row path)."""
    pool = AsyncMock()
    mock_row = MagicMock()
    mock_row.__getitem__.side_effect = lambda k: {"id": row_id, "is_primary": is_primary}[k]
    pool.fetchrow = AsyncMock(return_value=mock_row)
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


# ---------------------------------------------------------------------------
# Predicate mapping sanity
# ---------------------------------------------------------------------------


class TestPredicateMappingContract:
    """Verify the type → predicate assumptions that Group D relies on."""

    def test_email_is_mapped(self) -> None:
        assert contact_info_type_to_predicate("email") == "has-email"

    def test_phone_is_mapped(self) -> None:
        assert contact_info_type_to_predicate("phone") == "has-phone"

    def test_website_is_mapped(self) -> None:
        assert contact_info_type_to_predicate("website") == "has-website"

    def test_telegram_handle_is_mapped(self) -> None:
        """Plain 'telegram' is mapped; telegram_username and telegram_user_id are not."""
        assert contact_info_type_to_predicate("telegram") == "has-handle"

    def test_telegram_username_not_mapped(self) -> None:
        """telegram_username is a routing type — not in the predicate map."""
        assert contact_info_type_to_predicate("telegram_username") is None

    def test_telegram_user_id_not_mapped(self) -> None:
        assert contact_info_type_to_predicate("telegram_user_id") is None

    def test_address_not_mapped(self) -> None:
        """address is not a contact_info type but confirms the unmapped path."""
        assert contact_info_type_to_predicate("address") is None


# ---------------------------------------------------------------------------
# Core dual-write shim tests
# ---------------------------------------------------------------------------


class TestUpsertContactInfoDualWriteShim:
    """upsert_contact_info: emit_contact_info_fact called after each new INSERT."""

    async def test_mapped_email_shim_called_with_correct_args(self, monkeypatch) -> None:
        """(a) Mapped type email, flag on → shim called with correct args."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool_no_existing()
        writer = _make_writer(pool)
        contact = _make_canonical_contact(emails=["alice@example.com"])

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await writer.upsert_contact_info(contact_id, contact)

        mock_emit.assert_awaited_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["contact_id"] == contact_id
        assert kwargs["ci_type"] == "email"
        assert kwargs["value"] == "alice@example.com"
        assert kwargs["is_primary"] is False
        assert kwargs["src"] == "dual-write"

    async def test_mapped_phone_shim_called_with_correct_args(self, monkeypatch) -> None:
        """(b) Mapped type phone, flag on → shim called with correct args."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool_no_existing()
        writer = _make_writer(pool)
        contact = _make_canonical_contact(phones=["+15551234567"])

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await writer.upsert_contact_info(contact_id, contact)

        mock_emit.assert_awaited_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["ci_type"] == "phone"
        assert kwargs["value"] == "+15551234567"

    async def test_unmapped_type_shim_still_called(self, monkeypatch) -> None:
        """(c) Unmapped type (telegram_username) → shim called; helper no-ops internally."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool_no_existing()
        writer = _make_writer(pool)
        # telegram_username is the type written for telegram usernames
        contact = _make_canonical_contact(usernames=[("telegram", "@alice")])

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await writer.upsert_contact_info(contact_id, contact)

        # Shim is called unconditionally; the helper handles unmapped-type skip internally.
        mock_emit.assert_awaited_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["ci_type"] == "telegram_username"
        assert kwargs["value"] == "alice"  # leading @ stripped

    async def test_flag_off_shim_still_called(self, monkeypatch) -> None:
        """(d) Flag off → shim still called (helper's dual_write_enabled() short-circuits)."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)

        contact_id = uuid.uuid4()
        pool = _make_pool_no_existing()
        writer = _make_writer(pool)
        contact = _make_canonical_contact(emails=["bob@example.com"])

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await writer.upsert_contact_info(contact_id, contact)

        # Call-site always invokes helper; helper checks flag and returns early.
        mock_emit.assert_awaited_once()

    async def test_on_conflict_skip_shim_not_called(self, monkeypatch) -> None:
        """(e) ON CONFLICT DO NOTHING (value owned by another contact) → shim NOT called.

        When the INSERT is silently skipped because the (type, value) pair is claimed
        by a different contact (asyncpg returns "INSERT 0 0"), we must NOT call the
        shim.  Calling it would assert a triple linking the caller's entity to a value
        it does not own, contradicting the authoritative SQL state.
        """
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()

        # Pool returns None for fetchrow (no existing row for THIS contact) and
        # execute() returns "INSERT 0 0" — the value was claimed by another contact.
        pool = _make_pool_on_conflict()
        writer = _make_writer(pool)
        contact = _make_canonical_contact(emails=["shared@example.com"])

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await writer.upsert_contact_info(contact_id, contact)

        # Shim must NOT be called — emitting a triple here would be incorrect.
        mock_emit.assert_not_awaited()

    async def test_shim_failure_swallowed(self, monkeypatch) -> None:
        """(f) Shim raises → failure swallowed; no exception propagated."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool_no_existing()
        writer = _make_writer(pool)
        contact = _make_canonical_contact(emails=["crash@example.com"])

        with patch(
            _EMIT_FACT_PATCH,
            new_callable=AsyncMock,
            side_effect=RuntimeError("triple store down"),
        ):
            # Must not raise
            await writer.upsert_contact_info(contact_id, contact)

        # SQL INSERT was still executed
        pool.execute.assert_awaited_once()

    async def test_sql_before_shim_ordering(self, monkeypatch) -> None:
        """(g) SQL INSERT executes before the shim call (Amendment 14 ordering)."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool_no_existing()
        writer = _make_writer(pool)
        contact = _make_canonical_contact(emails=["order@example.com"])

        call_order: list[str] = []

        async def _record_sql(*_a: Any, **_kw: Any) -> str:
            call_order.append("sql")
            return "INSERT 0 1"

        pool.execute = AsyncMock(side_effect=_record_sql)

        async def _record_emit(*_args: Any, **_kw: Any) -> None:
            call_order.append("shim")

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock, side_effect=_record_emit):
            await writer.upsert_contact_info(contact_id, contact)

        assert call_order == ["sql", "shim"], f"Expected sql before shim, got: {call_order}"

    async def test_multiple_entries_shim_called_per_entry(self, monkeypatch) -> None:
        """(h) Multiple new entries → shim called once per INSERT (email + phone)."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool_no_existing()
        writer = _make_writer(pool)
        contact = _make_canonical_contact(
            emails=["multi@example.com"],
            phones=["+15559876543"],
        )

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await writer.upsert_contact_info(contact_id, contact)

        assert mock_emit.await_count == 2
        # Check both type args were used
        ci_types = {c.kwargs["ci_type"] for c in mock_emit.call_args_list}
        assert ci_types == {"email", "phone"}

    async def test_existing_row_path_no_shim_call(self, monkeypatch) -> None:
        """Shim is NOT called on the existing-row update path (primary-flip only; no INSERT).

        When a row already exists for THIS contact, upsert_contact_info reaches
        ``continue`` before the INSERT block — so no INSERT and no shim call.
        """
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        existing_row_id = uuid.uuid4()
        pool = _make_pool_existing_row(existing_row_id, is_primary=False)
        writer = _make_writer(pool)
        contact = _make_canonical_contact(emails=["existing@example.com"])

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await writer.upsert_contact_info(contact_id, contact)

        # The existing-row branch hits ``continue`` — shim is never reached.
        mock_emit.assert_not_awaited()

    async def test_no_entries_no_shim_call(self, monkeypatch) -> None:
        """No-op contact (no emails, phones, etc.) → shim never called."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        contact_id = uuid.uuid4()
        pool = _make_pool_no_existing()
        writer = _make_writer(pool)
        contact = _make_canonical_contact()  # all empty

        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await writer.upsert_contact_info(contact_id, contact)

        mock_emit.assert_not_awaited()
        pool.execute.assert_not_awaited()
