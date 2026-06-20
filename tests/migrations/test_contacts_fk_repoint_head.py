"""Acceptance test for the public.contacts DROP at head (bu-vcfyg → bu-y6o7q).

Runs the REAL migration chains (core + contacts + memory + relationship + home)
empty -> head against a live Postgres and asserts that at head:

1. There are ZERO foreign-key constraints referencing public.contacts(id)
   anywhere in the database (the FK-repoint precondition from core_133 / rel_030
   / contacts_005 holds), AND
2. ``public.contacts`` itself has been DROPPED (core_134, bu-y6o7q) — the final,
   irreversible step of the contacts-schema retirement.

This is the single end-to-end check that the retirement lands cleanly on a
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
        # public.contacts is now DROPPED at head (core_134, bu-y6o7q); the
        # permanent recovery snapshot remains.
        assert await conn.fetchval("SELECT to_regclass('public.contacts')") is None
        assert await conn.fetchval("SELECT to_regclass('public.contacts_dropbak')") is not None
    finally:
        await conn.close()
