"""Integration test for priority_contacts cascade-delete audit trigger.

Verifies that deleting a contact from public.contacts (which cascades to
public.priority_contacts) fires the AFTER DELETE trigger, inserting an
audit_log entry with:
  action = 'ingestion.priority_contact.cascade_remove'
  actor  = 'system:contact_cascade'
  target = '<contact_id>'
  note   = 'contact removed from public.contacts'

The schema is exercised in its post-core_129 form: priority_contacts is
butler-agnostic (the 'butler' column was dropped; PK collapsed to contact_id),
so the trigger target is just '<contact_id>' (no ':<butler>' suffix).

§3.12 / §3.1 — Phase 3a trigger (core_101), butler-drop (core_129, bu-gx13h).

This test requires a real PostgreSQL DB with triggers enabled.
Runs only under the 'integration' mark (needs postgres_container fixture).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

pytestmark = pytest.mark.integration

_VERSIONS_DIR = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "core"


def _load_migration(name: str):
    path = _VERSIONS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _revision_chain() -> None:
    """Verify migration revision metadata."""
    mod = _load_migration("core_101_priority_contacts")
    assert mod.revision == "core_101"
    assert mod.down_revision == "core_100"

    drop = _load_migration("core_129_priority_contacts_drop_butler")
    assert drop.revision == "core_129"
    assert drop.down_revision == "core_128"


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


async def _apply_core_129_schema(pool: asyncpg.Pool, drop_mod) -> None:
    """Apply core_129's schema DDL directly to reach the butler-agnostic shape.

    core_129's dedup/parity/existence steps go through ``op.get_bind()`` (not
    capturable via the mock-op harness above) but are no-ops on an empty,
    freshly-provisioned table.  Only its schema DDL matters here; the trigger
    function body is imported from the migration to avoid duplication/drift.
    """
    sqls = [
        "DROP INDEX IF EXISTS public.idx_priority_contacts_butler",
        "ALTER TABLE public.priority_contacts DROP CONSTRAINT IF EXISTS priority_contacts_pkey",
        "ALTER TABLE public.priority_contacts DROP COLUMN IF EXISTS butler",
        "ALTER TABLE public.priority_contacts ADD PRIMARY KEY (contact_id)",
        drop_mod._TRIGGER_FN_GLOBAL,
    ]
    for sql in sqls:
        await pool.execute(sql)


async def _provision_tables(pool: asyncpg.Pool) -> None:
    """Create prerequisite public tables for priority_contacts migration."""
    # public.contacts — minimal schema required by FK
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS public.contacts (
            id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT
        )
    """)

    # public.audit_log — required by the cascade trigger INSERT
    audit_mod = _load_migration("core_092_audit_log")
    await _run_upgrade_sqls(pool, audit_mod)

    # public.priority_contacts + trigger (core_101 shape, with butler column)
    pc_mod = _load_migration("core_101_priority_contacts")
    await _run_upgrade_sqls(pool, pc_mod)

    # Collapse to the butler-agnostic shape (core_129).
    drop_mod = _load_migration("core_129_priority_contacts_drop_butler")
    await _apply_core_129_schema(pool, drop_mod)


@pytest.fixture
async def cascade_pool(provisioned_postgres_pool):
    async with provisioned_postgres_pool() as pool:
        await _provision_tables(pool)
        yield pool


def test_migration_revision_chain():
    """Migration revision chain is correct."""
    _revision_chain()


@pytest.mark.asyncio(loop_scope="session")
async def test_cascade_delete_emits_audit_entry(cascade_pool: asyncpg.Pool) -> None:
    """Deleting a contact cascades to priority_contacts and fires the audit trigger.

    The trigger inserts into public.audit_log with the expected values.
    """
    pool = cascade_pool

    # Insert a contact
    contact_id = await pool.fetchval(
        "INSERT INTO public.contacts (name) VALUES ($1) RETURNING id",
        "Alice",
    )

    # Add a priority contact (butler-agnostic — contact_id only)
    await pool.execute(
        "INSERT INTO public.priority_contacts (contact_id) VALUES ($1)",
        contact_id,
    )

    # Verify the priority contact exists
    pc = await pool.fetchrow(
        "SELECT * FROM public.priority_contacts WHERE contact_id = $1",
        contact_id,
    )
    assert pc is not None

    # Delete the contact — should cascade-delete the priority_contacts row
    # and fire the audit trigger
    await pool.execute("DELETE FROM public.contacts WHERE id = $1", contact_id)

    # The priority_contacts row should be gone
    pc_after = await pool.fetchrow(
        "SELECT * FROM public.priority_contacts WHERE contact_id = $1",
        contact_id,
    )
    assert pc_after is None, "Cascade delete should have removed the priority_contacts row"

    # The audit_log should have a cascade_remove entry
    audit_row = await pool.fetchrow(
        "SELECT actor, action, target, note FROM public.audit_log "
        "WHERE action = 'ingestion.priority_contact.cascade_remove' "
        "ORDER BY id DESC LIMIT 1"
    )
    assert audit_row is not None, "Cascade trigger should have inserted an audit_log entry"
    assert audit_row["actor"] == "system:contact_cascade"
    assert audit_row["action"] == "ingestion.priority_contact.cascade_remove"
    # Target is the bare contact_id — no ':<butler>' suffix after core_129.
    assert audit_row["target"] == str(contact_id)
    assert audit_row["note"] == "contact removed from public.contacts"


@pytest.mark.asyncio(loop_scope="session")
async def test_cascade_delete_multiple_contacts(cascade_pool: asyncpg.Pool) -> None:
    """Deleting multiple priority contacts emits one audit entry per cascaded row."""
    pool = cascade_pool

    contact_ids = []
    for name in ("Bob", "Carol"):
        cid = await pool.fetchval(
            "INSERT INTO public.contacts (name) VALUES ($1) RETURNING id",
            name,
        )
        await pool.execute(
            "INSERT INTO public.priority_contacts (contact_id) VALUES ($1)",
            cid,
        )
        contact_ids.append(cid)

    # Delete both contacts — both priority_contacts rows cascade, both trigger audits
    for cid in contact_ids:
        await pool.execute("DELETE FROM public.contacts WHERE id = $1", cid)

    targets = {str(cid) for cid in contact_ids}
    audit_rows = await pool.fetch(
        "SELECT target FROM public.audit_log "
        "WHERE action = 'ingestion.priority_contact.cascade_remove' "
        "AND target = ANY($1)",
        list(targets),
    )

    # One audit entry per priority_contacts row
    assert len(audit_rows) == len(contact_ids), (
        f"Expected {len(contact_ids)} audit entries for cascade delete, got {len(audit_rows)}"
    )
    assert {r["target"] for r in audit_rows} == targets
