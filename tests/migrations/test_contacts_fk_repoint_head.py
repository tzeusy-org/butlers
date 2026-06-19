"""Acceptance test for the public.contacts FK-repoint precondition (bu-vcfyg).

Runs the REAL migration chains (core + contacts + memory + relationship + home)
empty -> head against a live Postgres and asserts that AFTER core_133 + rel_030
there are ZERO foreign-key constraints referencing public.contacts(id) anywhere
in the database — i.e. ``public.contacts`` can be dropped (bu-y6o7q) without FK
errors.  This is the single end-to-end check that the precondition holds on a
freshly-migrated schema.
"""

from __future__ import annotations

import asyncio
import shutil

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

_FK_TO_CONTACTS_SQL = """
SELECT tn.nspname || '.' || rel.relname AS tbl, con.conname AS cname
FROM pg_constraint con
JOIN pg_class rel    ON rel.oid = con.conrelid
JOIN pg_namespace tn ON tn.oid = rel.relnamespace
JOIN pg_class refrel ON refrel.oid = con.confrelid
JOIN pg_namespace rn ON rn.oid = refrel.relnamespace
WHERE con.contype = 'f' AND refrel.relname = 'contacts' AND rn.nspname = 'public'
ORDER BY tbl, cname
"""


async def test_no_fk_references_public_contacts_at_head(postgres_container) -> None:
    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "contacts", "memory", "relationship", "home"],
        {"relationship": "relationship", "home": "home"},
    )
    conn = await asyncpg.connect(db_url.replace("postgresql://", "postgres://"))
    try:
        rows = await conn.fetch(_FK_TO_CONTACTS_SQL)
        leftovers = [f"{r['tbl']}.{r['cname']}" for r in rows]
        assert leftovers == [], f"FK constraints still reference public.contacts(id): {leftovers}"
        # Sanity: public.contacts itself still exists (this bead does NOT drop it).
        assert await conn.fetchval("SELECT to_regclass('public.contacts')") is not None
    finally:
        await conn.close()
