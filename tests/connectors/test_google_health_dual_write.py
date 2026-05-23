"""Parity tests for dual-write shim Group F — google_health connector.

``upsert_google_health_contact_info`` (connectors/google_health.py) inserts a
row with ``type='google_health'`` into ``public.contact_info`` during the Google
Health OAuth callback flow (called by the connector module's public API).  After
each new INSERT it calls ``emit_contact_info_fact()`` best-effort (Amendment 14).

Design contract:
- SQL is authoritative.  The legacy INSERT commits first; the shim is
  post-commit and best-effort.
- ``emit_contact_info_fact()`` is called only when the INSERT actually created
  a row (asyncpg status == "INSERT 0 1").  When ON CONFLICT DO NOTHING silently
  skips the insert, the shim is NOT called — calling it would assert a triple
  for an entity that does not own the value, contradicting the authoritative SQL.
- ``google_health`` is currently unmapped in ``_CI_TYPE_TO_PREDICATE``, so
  ``emit_contact_info_fact()`` will no-op internally.  The gate is kept as a
  correctness safeguard for future predicate-map additions.
- Shim failures are swallowed; the SQL commit is never rolled back.
- The shim is gated by ``BUTLERS_CONTACT_INFO_DUAL_WRITE`` (checked inside the
  helper, not at the call site).

Test scope:
  (a) Successful INSERT (status "INSERT 0 1") + flag on → shim called with correct args.
  (b) ON CONFLICT skip (status "INSERT 0 0") → shim NOT called.
  (c) INSERT 0 1 + flag off → shim IS called (flag gated inside helper, not at call site).
  (d) Shim raises → failure swallowed; function returns normally.
  (e) SQL INSERT executes before the shim (Amendment 14 ordering).
  (f) Helper called with kwargs: contact_id, ci_type, value, is_primary, src.

[bu-3jfvv]
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_FLAG_ENV = "BUTLERS_CONTACT_INFO_DUAL_WRITE"
# Patch at the source so the deferred-import path resolves correctly.
_EMIT_FACT_PATCH = "butlers.tools.relationship.dual_write.emit_contact_info_fact"


def _import_fn():
    from butlers.connectors.google_health import upsert_google_health_contact_info

    return upsert_google_health_contact_info


# ---------------------------------------------------------------------------
# Pool mock helpers
# ---------------------------------------------------------------------------


def _make_conn_mock(
    owner_contact_row: dict[str, Any] | None,
    insert_status: str = "INSERT 0 1",
) -> MagicMock:
    """Build a mock asyncpg connection for upsert_google_health_contact_info.

    Parameters
    ----------
    owner_contact_row:
        Returned by the first ``fetchrow`` (contact lookup by entity_id).
        ``None`` causes the fallback INSERT branch; pass a dict with an ``"id"``
        key to simulate an existing contact.
    insert_status:
        asyncpg command tag returned by ``conn.execute`` for the INSERT.
        "INSERT 0 1" = row created; "INSERT 0 0" = ON CONFLICT skipped.
    """
    conn = AsyncMock()

    # fetchrow is called for: (1) contact lookup; if None, (2) contact INSERT.
    if owner_contact_row is None:
        # First fetchrow returns None → code calls fetchrow again for INSERT RETURNING.
        new_contact_id = uuid.uuid4()
        conn.fetchrow = AsyncMock(side_effect=[None, {"id": new_contact_id}])
    else:
        conn.fetchrow = AsyncMock(return_value=owner_contact_row)

    conn.execute = AsyncMock(return_value=insert_status)

    # transaction() context manager
    txn_cm = AsyncMock()
    txn_cm.__aenter__ = AsyncMock(return_value=None)
    txn_cm.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn_cm)

    return conn


def _make_pool(conn: MagicMock) -> MagicMock:
    """Wrap a connection mock inside an acquire() context-manager pool mock."""
    pool = MagicMock()
    acquire_cm = AsyncMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_cm)
    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGoogleHealthConnectorDualWriteShim:
    """upsert_google_health_contact_info: emit_contact_info_fact gated on insert_status."""

    async def test_insert_success_shim_called_with_correct_args(self, monkeypatch: Any) -> None:
        """(a) INSERT 0 1 + flag on → shim called with correct args."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        owner_entity_id = uuid.uuid4()
        owner_contact_id = uuid.uuid4()
        google_user_id = "google-user-abc123"

        conn = _make_conn_mock(
            owner_contact_row={"id": owner_contact_id},
            insert_status="INSERT 0 1",
        )
        pool = _make_pool(conn)

        fn = _import_fn()
        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await fn(pool, google_user_id=google_user_id, owner_entity_id=owner_entity_id)

        mock_emit.assert_awaited_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["contact_id"] == owner_contact_id
        assert kwargs["ci_type"] == "google_health"
        assert kwargs["value"] == google_user_id
        assert kwargs["is_primary"] is False
        assert kwargs["src"] == "dual-write"

    async def test_on_conflict_skip_shim_not_called(self, monkeypatch: Any) -> None:
        """(b) ON CONFLICT DO NOTHING (INSERT 0 0) → shim NOT called.

        When the (type, value) pair is already claimed by a different contact,
        asyncpg returns "INSERT 0 0".  The shim must not be called — emitting a
        triple here would assert a fact for an entity that doesn't own the value.
        """
        monkeypatch.setenv(_FLAG_ENV, "1")

        owner_entity_id = uuid.uuid4()
        owner_contact_id = uuid.uuid4()

        conn = _make_conn_mock(
            owner_contact_row={"id": owner_contact_id},
            insert_status="INSERT 0 0",
        )
        pool = _make_pool(conn)

        fn = _import_fn()
        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await fn(
                pool, google_user_id="already-claimed-user-id", owner_entity_id=owner_entity_id
            )

        mock_emit.assert_not_awaited()

    async def test_insert_success_flag_off_shim_still_called(self, monkeypatch: Any) -> None:
        """(c) INSERT 0 1 + flag off → shim IS called at the call site.

        The call-site gate is ``if insert_status == "INSERT 0 1"`` only — there is
        no flag check at the call site.  The flag (``BUTLERS_CONTACT_INFO_DUAL_WRITE``)
        is only checked inside ``emit_contact_info_fact()`` itself via
        ``dual_write_enabled()``.  With the function mocked here, the internal flag
        check is bypassed, so the mock is always called when insert_status is
        "INSERT 0 1", regardless of the environment variable.

        This test verifies that the call site correctly delegates flag responsibility
        to the helper — it does NOT short-circuit before calling the helper.
        """
        monkeypatch.delenv(_FLAG_ENV, raising=False)

        owner_entity_id = uuid.uuid4()
        owner_contact_id = uuid.uuid4()

        conn = _make_conn_mock(
            owner_contact_row={"id": owner_contact_id},
            insert_status="INSERT 0 1",
        )
        pool = _make_pool(conn)

        fn = _import_fn()
        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await fn(pool, google_user_id="test-user-flag-off", owner_entity_id=owner_entity_id)

        # insert_status is "INSERT 0 1" → gate passes → shim IS called.
        # The flag check happens inside the helper, not here.
        mock_emit.assert_awaited_once()
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["ci_type"] == "google_health"
        assert kwargs["is_primary"] is False

    async def test_shim_failure_swallowed(self, monkeypatch: Any) -> None:
        """(d) Shim raises → failure swallowed; function returns normally."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        owner_entity_id = uuid.uuid4()
        owner_contact_id = uuid.uuid4()

        conn = _make_conn_mock(
            owner_contact_row={"id": owner_contact_id},
            insert_status="INSERT 0 1",
        )
        pool = _make_pool(conn)

        fn = _import_fn()
        with patch(
            _EMIT_FACT_PATCH,
            new_callable=AsyncMock,
            side_effect=RuntimeError("triple store down"),
        ):
            # Must not raise — shim exceptions are swallowed per Amendment 14.
            await fn(pool, google_user_id="crash-test-user", owner_entity_id=owner_entity_id)

        # SQL INSERT was still executed
        conn.execute.assert_awaited_once()

    async def test_sql_before_shim_ordering(self, monkeypatch: Any) -> None:
        """(e) SQL INSERT executes before the shim call (Amendment 14 ordering)."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        owner_entity_id = uuid.uuid4()
        owner_contact_id = uuid.uuid4()

        call_order: list[str] = []

        conn = _make_conn_mock(owner_contact_row={"id": owner_contact_id})

        async def _record_sql(*_a: Any, **_kw: Any) -> str:
            call_order.append("sql")
            return "INSERT 0 1"

        conn.execute = AsyncMock(side_effect=_record_sql)

        pool = _make_pool(conn)

        async def _record_emit(*_args: Any, **_kw: Any) -> None:
            call_order.append("shim")

        fn = _import_fn()
        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock, side_effect=_record_emit):
            await fn(pool, google_user_id="order-test-user", owner_entity_id=owner_entity_id)

        assert call_order == ["sql", "shim"], f"Expected sql before shim, got: {call_order}"

    async def test_helper_signature_kwargs(self, monkeypatch: Any) -> None:
        """(f) emit_contact_info_fact called with all required keyword args."""
        monkeypatch.setenv(_FLAG_ENV, "1")

        owner_entity_id = uuid.uuid4()
        owner_contact_id = uuid.uuid4()
        google_user_id = "kwarg-test-user"

        conn = _make_conn_mock(
            owner_contact_row={"id": owner_contact_id},
            insert_status="INSERT 0 1",
        )
        pool = _make_pool(conn)

        fn = _import_fn()
        with patch(_EMIT_FACT_PATCH, new_callable=AsyncMock) as mock_emit:
            await fn(pool, google_user_id=google_user_id, owner_entity_id=owner_entity_id)

        assert mock_emit.call_count == 1
        call_args = mock_emit.call_args
        # First positional arg is pool
        assert call_args.args[0] is pool
        # All domain args are kwargs
        kwargs = call_args.kwargs
        assert set(kwargs) >= {"contact_id", "ci_type", "value", "is_primary", "src"}
        assert kwargs["contact_id"] == owner_contact_id
        assert kwargs["ci_type"] == "google_health"
        assert kwargs["value"] == google_user_id
        assert kwargs["is_primary"] is False
        assert kwargs["src"] == "dual-write"
