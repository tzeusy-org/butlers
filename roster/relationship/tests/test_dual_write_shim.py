"""Unit tests for the dual-write shim helpers (Amendment 1.1.C bead 4, bu-8w730).

Covers:
  (a) Feature flag off → no triple assertion, no DB lookup.
  (b) Feature flag on, unmapped ci_type → no triple assertion.
  (c) Feature flag on, contact has no entity_id → no triple assertion.
  (d) Feature flag on, contact not found → no triple assertion.
  (e) Happy path: email triple asserted via relationship_assert_fact().
  (f) Happy path: phone triple asserted.
  (g) Happy path: telegram → has-handle predicate.
  (h) relationship_assert_fact raises → error is swallowed (no re-raise).
  (i) retract_contact_info_fact: feature flag off → no DB lookup.
  (j) retract_contact_info_fact: feature flag on, logs retraction intent.
  (k) retract_contact_info_fact: error is swallowed.
  (l) contact_info_type_to_predicate: all mapped types return expected predicates.
  (m) contact_info_type_to_predicate: unmapped type returns None.
  (n) contact_info_add: calls emit_contact_info_fact after SQL commit (integration with shim).
  (o) contact_info_add: emit_contact_info_fact failure does not affect return value.
  (p) contact_info_remove: calls retract_contact_info_fact after DELETE.
  (q) contact_info_update: calls emit_contact_info_fact after SQL commit.

All tests are pure unit tests (no Docker/Postgres required).  The asyncpg pool
and relationship_assert_fact() are mocked via unittest.mock.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WRITER_PATCH_TARGET = (
    "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"
)
_EMIT_PATCH_TARGET = "butlers.tools.relationship.contact_info.emit_contact_info_fact"
_RETRACT_PATCH_TARGET = "butlers.tools.relationship.contact_info.retract_contact_info_fact"
_FLAG_ENV = "BUTLERS_CONTACT_INFO_DUAL_WRITE"

_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCM:
    """Minimal async context manager helper for mocking pool.acquire()."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        return False


def _make_pool(*, entity_id: uuid.UUID | None = _ENTITY_ID) -> MagicMock:
    """Return a mock asyncpg.Pool.

    fetchrow returns a record with entity_id when entity_id is not None;
    returns None when entity_id is None (simulates contact-not-found).
    """
    pool = MagicMock()

    if entity_id is not None:
        contact_row = {"entity_id": entity_id}
        pool.fetchrow = AsyncMock(return_value=contact_row)
    else:
        pool.fetchrow = AsyncMock(return_value=None)

    pool.execute = AsyncMock(return_value=None)
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value={"entity_id": entity_id})
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    return pool


# ===========================================================================
# Tests for dual_write module helpers
# ===========================================================================


class TestDualWriteEnabled:
    def test_off_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.dual_write import dual_write_enabled

        assert dual_write_enabled() is False

    def test_off_when_env_empty(self, monkeypatch):
        monkeypatch.setenv(_FLAG_ENV, "")
        from butlers.tools.relationship.dual_write import dual_write_enabled

        assert dual_write_enabled() is False

    def test_off_when_env_whitespace_only(self, monkeypatch):
        monkeypatch.setenv(_FLAG_ENV, "   ")
        from butlers.tools.relationship.dual_write import dual_write_enabled

        assert dual_write_enabled() is False

    def test_on_when_env_set_to_1(self, monkeypatch):
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import dual_write_enabled

        assert dual_write_enabled() is True

    def test_on_when_env_set_to_true(self, monkeypatch):
        monkeypatch.setenv(_FLAG_ENV, "true")
        from butlers.tools.relationship.dual_write import dual_write_enabled

        assert dual_write_enabled() is True


class TestContactInfoTypeToPredicate:
    def test_email_maps_to_has_email(self):
        from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

        assert contact_info_type_to_predicate("email") == "has-email"

    def test_phone_maps_to_has_phone(self):
        from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

        assert contact_info_type_to_predicate("phone") == "has-phone"

    def test_telegram_maps_to_has_handle(self):
        from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

        assert contact_info_type_to_predicate("telegram") == "has-handle"

    def test_linkedin_maps_to_has_handle(self):
        from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

        assert contact_info_type_to_predicate("linkedin") == "has-handle"

    def test_twitter_maps_to_has_handle(self):
        from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

        assert contact_info_type_to_predicate("twitter") == "has-handle"

    def test_website_maps_to_has_website(self):
        from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

        assert contact_info_type_to_predicate("website") == "has-website"

    def test_other_maps_to_has_handle(self):
        from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

        assert contact_info_type_to_predicate("other") == "has-handle"

    def test_unmapped_type_returns_none(self):
        from butlers.tools.relationship.dual_write import contact_info_type_to_predicate

        assert contact_info_type_to_predicate("fax") is None
        assert contact_info_type_to_predicate("address") is None
        assert contact_info_type_to_predicate("unknown_type_xyz") is None


# ===========================================================================
# Tests for emit_contact_info_fact
# ===========================================================================


class TestEmitContactInfoFactFlagOff:
    """(a) Feature flag off → early return, no DB lookup."""

    async def test_no_db_lookup_when_flag_off(self, monkeypatch):
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = _make_pool()
        await emit_contact_info_fact(pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com")
        pool.fetchrow.assert_not_called()

    async def test_no_assert_call_when_flag_off(self, monkeypatch):
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = _make_pool()
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com"
            )
            mock_writer.assert_not_called()


class TestEmitContactInfoFactUnmappedType:
    """(b) Feature flag on, unmapped ci_type → no triple assertion."""

    async def test_unmapped_type_skips_assert(self, monkeypatch):
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = _make_pool()
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="fax", value="555-1234"
            )
            mock_writer.assert_not_called()


class TestEmitContactInfoFactNoEntityId:
    """(c) Feature flag on, contact has no entity_id → no triple assertion."""

    async def test_no_entity_id_skips_assert(self, monkeypatch):
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"entity_id": None})

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com"
            )
            mock_writer.assert_not_called()


class TestEmitContactInfoFactContactNotFound:
    """(d) Feature flag on, contact not found → no triple assertion."""

    async def test_contact_not_found_skips_assert(self, monkeypatch):
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)

        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com"
            )
            mock_writer.assert_not_called()


class TestEmitContactInfoFactHappyPath:
    """(e-g) Happy path: triples asserted correctly."""

    async def test_email_asserts_has_email(self, monkeypatch):
        """(e) Email → has-email predicate passed to relationship_assert_fact."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = _make_pool(entity_id=_ENTITY_ID)
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="email", value="alice@example.com"
            )
            mock_writer.assert_awaited_once()
            call_args = mock_writer.call_args
            # positional: (pool, entity_id, predicate, value)
            assert call_args.args[1] == _ENTITY_ID
            assert call_args.args[2] == "has-email"
            assert call_args.args[3] == "alice@example.com"
            assert call_args.kwargs.get("src") == "dual-write"

    async def test_phone_asserts_has_phone(self, monkeypatch):
        """(f) Phone → has-phone predicate."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = _make_pool(entity_id=_ENTITY_ID)
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="phone", value="+1-555-0001"
            )
            assert mock_writer.call_args.args[2] == "has-phone"

    async def test_telegram_asserts_has_handle(self, monkeypatch):
        """(g) Telegram → has-handle predicate (channel-scoped)."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = _make_pool(entity_id=_ENTITY_ID)
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="telegram", value="tg_handle_123"
            )
            assert mock_writer.call_args.args[2] == "has-handle"

    async def test_is_primary_forwarded(self, monkeypatch):
        """is_primary=True is forwarded to the triple's primary field."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = _make_pool(entity_id=_ENTITY_ID)
        with patch(_WRITER_PATCH_TARGET, new_callable=AsyncMock) as mock_writer:
            await emit_contact_info_fact(
                pool,
                contact_id=_CONTACT_ID,
                ci_type="email",
                value="alice@example.com",
                is_primary=True,
            )
            assert mock_writer.call_args.kwargs.get("primary") is True


class TestEmitContactInfoFactErrorSwallowed:
    """(h) relationship_assert_fact raises → error is swallowed."""

    async def test_writer_exception_is_swallowed(self, monkeypatch):
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = _make_pool(entity_id=_ENTITY_ID)
        with patch(
            _WRITER_PATCH_TARGET, new_callable=AsyncMock, side_effect=RuntimeError("DB down")
        ):
            # Must not raise
            await emit_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com"
            )

    async def test_fetchrow_exception_is_swallowed(self, monkeypatch):
        """DB error during entity lookup must not propagate."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import emit_contact_info_fact

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=ConnectionError("DB unreachable"))
        # Must not raise
        await emit_contact_info_fact(pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com")


# ===========================================================================
# Tests for retract_contact_info_fact
# ===========================================================================


class TestRetractContactInfoFact:
    """(i-k) retract_contact_info_fact tests."""

    async def test_flag_off_no_db_lookup(self, monkeypatch):
        """(i) Feature flag off → no DB lookup."""
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.dual_write import retract_contact_info_fact

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await retract_contact_info_fact(
            pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com"
        )
        pool.fetchrow.assert_not_called()

    async def test_flag_on_logs_retraction_intent(self, monkeypatch, caplog):
        """(j) Feature flag on, contact found → logs retraction intent."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import retract_contact_info_fact

        pool = _make_pool(entity_id=_ENTITY_ID)
        import logging

        with caplog.at_level(logging.INFO):
            await retract_contact_info_fact(
                pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com"
            )

        # Should log the retraction intent (placeholder)
        assert any("retract" in rec.message.lower() for rec in caplog.records)

    async def test_error_swallowed(self, monkeypatch):
        """(k) DB error during lookup → swallowed, no raise."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import retract_contact_info_fact

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=ConnectionError("DB down"))
        # Must not raise
        await retract_contact_info_fact(
            pool, contact_id=_CONTACT_ID, ci_type="email", value="a@b.com"
        )

    async def test_unmapped_type_no_db_lookup(self, monkeypatch):
        """Unmapped ci_type returns early after flag check."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.dual_write import retract_contact_info_fact

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)
        await retract_contact_info_fact(
            pool, contact_id=_CONTACT_ID, ci_type="fax", value="555-1234"
        )
        # fax has no predicate mapping → no fetchrow call needed
        pool.fetchrow.assert_not_called()


# ===========================================================================
# Integration: contact_info_add / contact_info_update / contact_info_remove
# with the dual-write shim patched
# ===========================================================================


class TestContactInfoAddDualWriteShim:
    """(n-o) contact_info_add calls emit_contact_info_fact after SQL commit."""

    def _make_ci_pool(self, ci_row):
        """Build a pool mock for contact_info_add.

        contact_info_add calls pool.fetchrow for:
          1. contact-exists check: ``SELECT id FROM contacts WHERE id = $1``
          2. _is_owner_contact: JOIN entities — returns None (non-owner)

        The INSERT RETURNING is on the connection (conn.fetchrow).
        """
        pool = MagicMock()

        conn = AsyncMock()
        conn.execute = AsyncMock(return_value=None)
        conn.fetchrow = AsyncMock(return_value=ci_row)
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        # pool.fetchrow: first call = contact exists; second call = owner check (None = non-owner)
        contact_id = ci_row["contact_id"]
        pool.fetchrow = AsyncMock(
            side_effect=[
                {"id": contact_id},  # SELECT id FROM contacts → contact exists
                None,  # _is_owner_contact → not owner
            ]
        )
        return pool

    async def test_emit_called_after_sql_commit(self, monkeypatch):
        """(n) emit_contact_info_fact is called once after successful INSERT."""
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

        pool = self._make_ci_pool(ci_row=ci_row)

        with patch(_EMIT_PATCH_TARGET, new_callable=AsyncMock) as mock_emit:
            await contact_info_add(pool, contact_id, "email", "alice@example.com")
            mock_emit.assert_awaited_once()
            call_kwargs = mock_emit.call_args.kwargs
            assert call_kwargs["ci_type"] == "email"
            assert call_kwargs["value"] == "alice@example.com"

    async def test_emit_called_even_when_flag_off(self, monkeypatch):
        """(o) emit_contact_info_fact is still called when flag is off (it returns early internally).

        This verifies the call-site always reaches emit regardless of the flag —
        the flag is evaluated inside emit_contact_info_fact, not at the call site.
        """
        monkeypatch.delenv(_FLAG_ENV, raising=False)
        from butlers.tools.relationship.contact_info import contact_info_add

        contact_id = uuid.uuid4()
        ci_id = uuid.uuid4()
        ci_row = {
            "id": ci_id,
            "contact_id": contact_id,
            "type": "email",
            "value": "bob@example.com",
            "label": None,
            "is_primary": False,
            "context": None,
        }

        pool = self._make_ci_pool(ci_row=ci_row)

        with patch(_EMIT_PATCH_TARGET, new_callable=AsyncMock) as mock_emit:
            result = await contact_info_add(pool, contact_id, "email", "bob@example.com")

        # Call site always invokes emit; emit itself checks the flag and returns early.
        mock_emit.assert_awaited_once()
        assert result["id"] == ci_id


class TestContactInfoRemoveDualWriteShim:
    """(p) contact_info_remove calls retract_contact_info_fact after DELETE."""

    async def test_retract_called_after_delete(self, monkeypatch):
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
            call_kwargs = mock_retract.call_args.kwargs
            assert call_kwargs["ci_type"] == "email"
            assert call_kwargs["value"] == "alice@example.com"


class TestContactInfoUpdateDualWriteShim:
    """(q) contact_info_update calls emit_contact_info_fact after SQL commit."""

    def _make_update_pool(self, ci_row, updated_row):
        """Build a pool mock for contact_info_update.

        contact_info_update calls pool.fetchrow for:
          1. ``SELECT * FROM public.contact_info WHERE id = $1`` → ci_row
          2. ``_is_owner_contact`` join → None (non-owner)
        conn.fetchrow → updated_row (RETURNING)
        """
        pool = MagicMock()

        conn = AsyncMock()
        conn.execute = AsyncMock(return_value=None)
        conn.fetchrow = AsyncMock(return_value=updated_row)
        conn.transaction = MagicMock(return_value=_AsyncCM(None))
        pool.acquire = MagicMock(return_value=_AsyncCM(conn))

        pool.fetchrow = AsyncMock(
            side_effect=[
                ci_row,  # SELECT * FROM contact_info → found
                None,  # _is_owner_contact → not owner
            ]
        )
        return pool

    async def test_emit_called_after_update(self, monkeypatch):
        """(q) emit_contact_info_fact is called once after the UPDATE commits."""
        monkeypatch.setenv(_FLAG_ENV, "1")
        from butlers.tools.relationship.contact_info import contact_info_update

        contact_id = uuid.uuid4()
        ci_id = uuid.uuid4()
        ci_row = {
            "id": ci_id,
            "contact_id": contact_id,
            "type": "phone",
            "value": "+1-555-0001",
            "label": None,
            "is_primary": False,
            "context": None,
        }
        updated_row = dict(ci_row) | {"value": "+1-555-0002"}
        pool = self._make_update_pool(ci_row=ci_row, updated_row=updated_row)

        with patch(_EMIT_PATCH_TARGET, new_callable=AsyncMock) as mock_emit:
            result = await contact_info_update(pool, ci_id, value="+1-555-0002")
            mock_emit.assert_awaited_once()
            call_kwargs = mock_emit.call_args.kwargs
            assert call_kwargs["ci_type"] == "phone"
            # Effective value comes from the updated row
            assert call_kwargs["value"] == "+1-555-0002"

        assert result["value"] == "+1-555-0002"
