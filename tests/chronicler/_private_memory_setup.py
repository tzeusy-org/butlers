"""Explicit migration setup for Chronicler tests that reach 030 or later.

Current-head fixtures use a prepared template with the canonical 029 boundary.
Migration-subject tests can still opt in to individual preparation stages.
"""

from __future__ import annotations

import asyncio

from butlers.migrations import run_migrations
from butlers.testing.migration import FollowupMigrations


async def prepare_chronicler_private_core(db_url: str) -> None:
    """Provision the configured private core chain before its memory module."""
    await run_migrations(db_url, chain="core", schema="chronicler_mem")


async def prepare_chronicler_private_memory(db_url: str) -> None:
    """Provision Chronicler's canonical private memory after private core."""
    await prepare_chronicler_private_core(db_url)
    await run_migrations(db_url, chain="memory", schema="chronicler_mem")


def _current_head_migrations(domain_schema: str) -> FollowupMigrations:
    return (
        ("core", "chronicler_mem"),
        ("memory", "chronicler_mem"),
        ("chronicler", domain_schema),
    )


async def prepare_chronicler_current_head(db_url: str, *, domain_schema: str | None = None) -> None:
    """Prepare private core+memory, then advance the already-pinned domain chain."""
    for chain, schema in _current_head_migrations(domain_schema or "chronicler"):
        await run_migrations(db_url, chain=chain, schema=schema)


def prepare_chronicler_private_core_sync(db_url: str) -> None:
    asyncio.run(prepare_chronicler_private_core(db_url))


def prepare_chronicler_private_memory_sync(db_url: str) -> None:
    asyncio.run(prepare_chronicler_private_memory(db_url))


def prepare_chronicler_current_head_sync(db_url: str, *, domain_schema: str | None = None) -> None:
    asyncio.run(prepare_chronicler_current_head(db_url, domain_schema=domain_schema))


async def prepared_chronicler_url(
    fresh_db,
    *chains: str,
    domain_schema: str | None = None,
    schemas: dict[str, str] | None = None,
) -> str:
    """Provision a fresh database prepared through the Chronicler head."""
    # 030's protected functions and canonical domain relations are fixed to
    # chronicler. Build 029 there too; a public-domain fixture is not equivalent.
    resolved_schemas = dict(schemas or {})
    resolved_domain = resolved_schemas.setdefault("chronicler", domain_schema or "chronicler")
    return await asyncio.to_thread(
        fresh_db,
        *chains,
        schemas=resolved_schemas,
        revisions={"chronicler": "chronicler_029"},
        followup_migrations=_current_head_migrations(resolved_domain),
    )


async def open_prepared_chronicler_pool(
    db,
    fresh_db,
    *chains: str,
    domain_schema: str | None = None,
    pool_schema: str | None = None,
    schemas: dict[str, str] | None = None,
    **pool_kwargs,
):
    """Open a pool on a fresh, fully prepared Chronicler database."""
    db_url = await prepared_chronicler_url(
        fresh_db, *chains, domain_schema=domain_schema, schemas=schemas
    )
    resolved_domain = (schemas or {}).get("chronicler", domain_schema or "chronicler")
    return await db.open(db_url, schema=pool_schema or resolved_domain, **pool_kwargs)
