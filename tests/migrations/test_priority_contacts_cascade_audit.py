"""Contract: ``public.priority_contacts`` carries NO cascade-audit trigger (bu-fi36x).

History
-------
``core_101`` created an unconditional ``AFTER DELETE`` row trigger on
``public.priority_contacts`` whose only purpose was to make *cascaded* removals
observable: when a row in ``public.contacts`` was deleted, the
``ON DELETE CASCADE`` FK removed the priority-contact row silently, so the
trigger wrote one ``ingestion.priority_contact.cascade_remove`` audit row with
``note = 'contact removed from public.contacts'``.

Two later migrations dismantled that premise:

- ``core_131`` dropped ``priority_contacts_contact_id_fkey`` (the only cascading
  inbound FK). The replacement FK, ``priority_contacts_entity_id_fkey`` →
  ``public.entities(id)``, is ``ON DELETE SET NULL`` — it nulls a column, it never
  deletes a row.
- ``core_134`` dropped ``public.contacts`` outright.

So no cascade path into ``priority_contacts`` remains. Every firing of the
trigger was in fact a *direct* DELETE — overwhelmingly the router's own
``DELETE /api/ingestion/priority-contacts/{contact_id}``, which already writes
its own ``ingestion.priority_contact.remove`` audit row. One removal therefore
produced two audit rows, the second asserting a provenance
(``contact removed from public.contacts``) that could no longer occur.

``core_205`` drops the trigger and its function. A conditional trigger was
rejected: there is no surviving cascade path for a condition to select for, and
a trigger cannot distinguish the router's DELETE from any other direct DELETE.

Historical rows: the pre-existing ``ingestion.priority_contact.cascade_remove``
rows are left untouched. Audit history is immutable — rewriting or deleting
landed audit rows to make them retroactively accurate would be a worse defect
than the inaccurate note. ``core_205``'s docstring records the same decision.

These tests run the real core migration chain (``core_101`` → ``core_129`` →
``core_131`` → ``core_134`` → ``core_205`` and on to head) against a live
PostgreSQL instance, then exercise the real router against the resulting schema.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.api.routers.priority_contacts import router as priority_contacts_router
from butlers.testing.migration import load_migration

pytestmark = pytest.mark.integration

_VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "core"

_CASCADE_ACTION = "ingestion.priority_contact.cascade_remove"
_REMOVE_ACTION = "ingestion.priority_contact.remove"
_TRIGGER_FN = "priority_contacts_cascade_audit"
_RETIRE_MIGRATION = "core_205_priority_contacts_drop_cascade_audit"


def _core_migration(name: str):
    return load_migration(_VERSIONS_DIR / f"{name}.py", name)


async def _run_upgrade_sqls(pool: asyncpg.Pool, mod) -> None:
    """Collect upgrade() SQL via mock op and execute against the pool."""
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: sqls.append(sql)
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    for sql in sqls:
        try:
            await pool.execute(sql)
        except asyncpg.DuplicateObjectError:
            pass  # idempotent re-runs OK


@pytest.fixture
async def cascade_pool(db_pool: asyncpg.Pool) -> asyncpg.Pool:
    """The real core chain at head: core_101 → core_129 → core_131 → core_134 → core_205."""
    return db_pool


class _StubDatabaseManager:
    """Minimal DatabaseManager stand-in handing the router the live test pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def credential_shared_pool(self) -> asyncpg.Pool:
        return self._pool


@pytest.fixture
async def priority_contacts_client(cascade_pool: asyncpg.Pool) -> AsyncIterator[httpx.AsyncClient]:
    """The real priority-contacts router bound to the live migrated database."""
    app = FastAPI()
    app.include_router(priority_contacts_router)
    app.dependency_overrides[get_db] = lambda: _StubDatabaseManager(cascade_pool)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _seed_priority_contact(pool: asyncpg.Pool, name: str = "Alice") -> UUID:
    """Insert one entity + its priority-contact row; return the contact_id."""
    entity_id = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name) VALUES ($1) RETURNING id", name
    )
    await pool.execute(
        "INSERT INTO public.priority_contacts (contact_id, entity_id) VALUES ($1, $2)",
        entity_id,
        entity_id,
    )
    return entity_id


@pytest.mark.asyncio(loop_scope="session")
async def test_no_cascade_audit_trigger_remains(cascade_pool: asyncpg.Pool) -> None:
    """core_205 leaves no user trigger on public.priority_contacts."""
    triggers = await cascade_pool.fetch(
        """
        SELECT tgname FROM pg_trigger
        WHERE tgrelid = 'public.priority_contacts'::regclass
          AND NOT tgisinternal
        """
    )
    assert [t["tgname"] for t in triggers] == [], (
        "public.priority_contacts must carry no user trigger: the cascade path the "
        "cascade-audit trigger represented was removed by core_131/core_134"
    )

    # Plant historical evidence using the actual retired trigger, then replay
    # the retirement. Empty audit history cannot prove immutable row survival.
    retirement = _core_migration(_RETIRE_MIGRATION)
    legacy_sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = lambda sql: legacy_sqls.append(sql)
    with patch.object(retirement, "op", mock_op):
        retirement.downgrade()
    for sql in legacy_sqls:
        await cascade_pool.execute(sql)
    contact_id = await _seed_priority_contact(cascade_pool, "Historical sentinel")
    await cascade_pool.execute(
        "DELETE FROM public.priority_contacts WHERE contact_id = $1", contact_id
    )
    before = await cascade_pool.fetch(
        "SELECT * FROM public.audit_log WHERE action = $1 AND target = $2 ORDER BY id",
        _CASCADE_ACTION,
        str(contact_id),
    )
    assert len(before) == 1
    assert before[0]["actor"] == "system:contact_cascade"
    assert before[0]["note"] == "contact removed from public.contacts"

    await _run_upgrade_sqls(cascade_pool, retirement)

    after = await cascade_pool.fetch(
        "SELECT * FROM public.audit_log WHERE action = $1 AND target = $2 ORDER BY id",
        _CASCADE_ACTION,
        str(contact_id),
    )
    assert [dict(row) for row in after] == [dict(row) for row in before]
    assert (
        await cascade_pool.fetchval(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgrelid = 'public.priority_contacts'::regclass AND NOT tgisinternal"
        )
        == 0
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_no_cascade_audit_function_remains(cascade_pool: asyncpg.Pool) -> None:
    """core_205 also drops the orphaned trigger function, not just the trigger."""
    count = await cascade_pool.fetchval(
        """
        SELECT count(*) FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = $1
        """,
        _TRIGGER_FN,
    )
    assert count == 0, (
        f"public.{_TRIGGER_FN}() must be dropped alongside its trigger; leaving the "
        "function behind lets a future CREATE TRIGGER silently resurrect the defect"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_no_inbound_fk_can_cascade_delete_priority_contacts(
    cascade_pool: asyncpg.Pool,
) -> None:
    """The premise of core_205: no FK deletes a priority_contacts row for us.

    ``confdeltype`` is ``'c'`` for ON DELETE CASCADE. The surviving FK
    (``entity_id`` → ``public.entities``) is ``'n'`` (SET NULL), which nulls a
    column rather than removing the row. If a cascading FK is ever re-added,
    this fails and forces a deliberate re-decision about the removed trigger.
    """
    cascading = await cascade_pool.fetch(
        """
        SELECT conname FROM pg_constraint
        WHERE contype = 'f'
          AND conrelid = 'public.priority_contacts'::regclass
          AND confdeltype = 'c'
        """
    )
    assert [c["conname"] for c in cascading] == [], (
        "A cascading inbound FK on public.priority_contacts would delete rows with no "
        "audit trail — core_205 removed the trigger precisely because none exists"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_direct_delete_writes_no_audit_row(cascade_pool: asyncpg.Pool) -> None:
    """A raw DELETE no longer self-audits — the caller owns the audit row."""
    contact_id = await _seed_priority_contact(cascade_pool, "Bob")

    await cascade_pool.execute(
        "DELETE FROM public.priority_contacts WHERE contact_id = $1", contact_id
    )

    remaining = await cascade_pool.fetchval(
        "SELECT count(*) FROM public.priority_contacts WHERE contact_id = $1", contact_id
    )
    assert remaining == 0

    audited = await cascade_pool.fetchval("SELECT count(*) FROM public.audit_log")
    assert audited == 0, "a direct DELETE must not emit any audit row of its own"


@pytest.mark.asyncio(loop_scope="session")
async def test_api_removal_writes_exactly_one_audit_row(
    cascade_pool: asyncpg.Pool,
    priority_contacts_client: httpx.AsyncClient,
) -> None:
    """One DELETE through the API writes exactly one audit row, with a true note."""
    contact_id = await _seed_priority_contact(cascade_pool, "Carol")

    response = await priority_contacts_client.delete(
        f"/api/ingestion/priority-contacts/{contact_id}"
    )
    assert response.status_code == 204, response.text

    rows = await cascade_pool.fetch("SELECT actor, action, target, note FROM public.audit_log")
    assert len(rows) == 1, (
        "one API removal must produce exactly one audit row; a second row means the "
        f"cascade-audit trigger is back: {[dict(r) for r in rows]}"
    )
    assert rows[0]["action"] == _REMOVE_ACTION
    assert rows[0]["actor"] == "dashboard"
    assert rows[0]["target"] == str(contact_id)

    cascade_rows = await cascade_pool.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE action = $1", _CASCADE_ACTION
    )
    assert cascade_rows == 0, f"{_CASCADE_ACTION} can no longer be produced by any live path"


@pytest.mark.asyncio(loop_scope="session")
async def test_no_audit_note_references_dropped_contacts_table(
    cascade_pool: asyncpg.Pool,
    priority_contacts_client: httpx.AsyncClient,
) -> None:
    """No audit note may assert provenance from public.contacts — it was dropped."""
    for name in ("Dave", "Erin"):
        contact_id = await _seed_priority_contact(cascade_pool, name)
        response = await priority_contacts_client.delete(
            f"/api/ingestion/priority-contacts/{contact_id}"
        )
        assert response.status_code == 204, response.text

    offending = await cascade_pool.fetch(
        "SELECT action, note FROM public.audit_log WHERE note LIKE '%public.contacts%'"
    )
    assert [dict(r) for r in offending] == [], (
        "public.contacts was dropped by core_134; no newly-written audit note may "
        "claim a removal originated there"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_api_removal_of_unknown_contact_writes_no_audit_row(
    cascade_pool: asyncpg.Pool,
    priority_contacts_client: httpx.AsyncClient,
) -> None:
    """A 404 removal audits nothing — no row deleted, no trigger, no router row."""
    response = await priority_contacts_client.delete(f"/api/ingestion/priority-contacts/{uuid4()}")
    assert response.status_code == 404, response.text

    audited = await cascade_pool.fetchval("SELECT count(*) FROM public.audit_log")
    assert audited == 0
