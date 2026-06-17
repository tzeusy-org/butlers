"""Atomicity of new-contact creation in the Google Contacts backfill engine.

Regression for duplicate `public.contacts` rows for the same Google contact
(the Ang-Zhi-Yuan duplication). Root cause: on the new-contact path the engine
created the contact and wrote its `contacts_source_links` provenance row as two
separate pool statements. If the link write failed (or the process died)
in between, the contact landed *without* a source link — so the next sync
could not resolve it by `external_id` and re-created it, fanning out duplicates.

The engine now runs `create_contact` + `upsert_source_link` inside a single
transaction on one acquired connection, so they commit or roll back as a unit.
"""

from __future__ import annotations

import uuid

import pytest

from butlers.modules.contacts.backfill import ContactBackfillEngine
from butlers.modules.contacts.sync import CanonicalContact

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes: a pool whose acquire()/transaction() record entry/exit so we can prove
# the writer calls happen inside the transaction.
# ---------------------------------------------------------------------------


class _FakeTxn:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeTxn:
        self._conn.events.append("txn_enter")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._conn.events.append(("txn_exit", exc_type))
        return False  # never swallow — real asyncpg rolls back on exception


class _FakeConn:
    def __init__(self) -> None:
        self.events: list = []

    def transaction(self) -> _FakeTxn:
        return _FakeTxn(self)


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *a) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


def _contact() -> CanonicalContact:
    return CanonicalContact(
        external_id="people/c123",
        etag="etag-1",
        display_name="Zhi Yuan Ang",
        first_name="Zhi",
        last_name="Ang",
    )


def _engine_with_recording_writer(conn: _FakeConn):
    """Build an engine whose resolver returns 'new' and whose writer records calls."""
    engine = ContactBackfillEngine(_FakePool(conn), provider="google", account_id="default")

    calls: list[tuple] = []
    new_id = uuid.uuid4()

    async def _resolve(_contact):
        return (None, "new")

    async def _create_contact(_contact, *, executor=None):
        conn.events.append("create_contact")
        calls.append(("create_contact", executor))
        return new_id

    async def _upsert_source_link(_local_id, _contact, *, executor=None):
        conn.events.append("upsert_source_link")
        calls.append(("upsert_source_link", _local_id, executor))

    async def _noop(*a, **k):
        calls.append(("child_upsert", k.get("executor")))

    engine._resolver.resolve = _resolve  # type: ignore[assignment]
    engine._writer.create_contact = _create_contact  # type: ignore[assignment]
    engine._writer.upsert_source_link = _upsert_source_link  # type: ignore[assignment]
    engine._writer.upsert_contact_info = _noop  # type: ignore[assignment]
    engine._writer.upsert_addresses = _noop  # type: ignore[assignment]
    engine._writer.upsert_important_dates = _noop  # type: ignore[assignment]
    engine._writer.upsert_labels = _noop  # type: ignore[assignment]
    return engine, calls, conn, new_id


async def test_new_contact_create_and_source_link_share_one_transaction():
    conn = _FakeConn()
    engine, calls, conn, new_id = _engine_with_recording_writer(conn)

    await engine(_contact())

    # create_contact and upsert_source_link both ran on the acquired connection.
    create = next(c for c in calls if c[0] == "create_contact")
    link = next(c for c in calls if c[0] == "upsert_source_link")
    assert create[1] is conn  # executor == transactional connection
    assert link[2] is conn
    assert link[1] == new_id  # linked to the just-created contact

    # ...and both happened strictly between txn_enter and txn_exit.
    enter = conn.events.index("txn_enter")
    exit_ = next(
        i for i, e in enumerate(conn.events) if isinstance(e, tuple) and e[0] == "txn_exit"
    )
    create_at = conn.events.index("create_contact")
    link_at = conn.events.index("upsert_source_link")
    assert enter < create_at < link_at < exit_

    # Child-table upserts are best-effort and run OUTSIDE the transaction
    # (no executor), so a child failure cannot strand the idempotency anchor.
    child_calls = [c for c in calls if c[0] == "child_upsert"]
    assert child_calls, "expected child-table upserts after the transaction"
    assert all(c[1] is None for c in child_calls)


async def test_source_link_failure_rolls_back_and_propagates():
    """If the source-link write fails, the error propagates (txn rolls back).

    The sync engine treats a raised exception as a failed apply and will retry
    on the next cycle — at which point no half-written contact exists to find,
    so it creates cleanly rather than duplicating.
    """
    conn = _FakeConn()
    engine, _calls, conn, _new_id = _engine_with_recording_writer(conn)

    async def _boom(_local_id, _contact, *, executor=None):
        conn.events.append("upsert_source_link")
        raise RuntimeError("link write failed")

    engine._writer.upsert_source_link = _boom  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="link write failed"):
        await engine(_contact())

    # The transaction context manager saw the exception on exit (would roll
    # back the contact INSERT in real asyncpg).
    txn_exit = next(e for e in conn.events if isinstance(e, tuple) and e[0] == "txn_exit")
    assert txn_exit[1] is RuntimeError
