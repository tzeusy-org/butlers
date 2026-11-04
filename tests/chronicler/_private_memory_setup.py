"""Explicit migration setup for Chronicler tests that reach 030 or later.

Call this directly from an owning test fixture after its existing domain chain is
pinned at 029. It is intentionally not an autouse fixture or a root DB bootstrap.
"""

from __future__ import annotations

import asyncio

from butlers.migrations import run_migrations


async def prepare_chronicler_private_core(db_url: str) -> None:
    """Provision the configured private core chain before its memory module."""
    await run_migrations(db_url, chain="core", schema="chronicler_mem")


async def prepare_chronicler_private_memory(db_url: str) -> None:
    """Provision Chronicler's canonical private memory after private core."""
    await prepare_chronicler_private_core(db_url)
    await run_migrations(db_url, chain="memory", schema="chronicler_mem")


async def prepare_chronicler_current_head(db_url: str, *, domain_schema: str | None = None) -> None:
    """Prepare private core+memory, then advance the already-pinned domain chain."""
    await prepare_chronicler_private_memory(db_url)
    await run_migrations(db_url, chain="chronicler", schema=domain_schema)


def prepare_chronicler_private_core_sync(db_url: str) -> None:
    asyncio.run(prepare_chronicler_private_core(db_url))


def prepare_chronicler_private_memory_sync(db_url: str) -> None:
    asyncio.run(prepare_chronicler_private_memory(db_url))


def prepare_chronicler_current_head_sync(db_url: str, *, domain_schema: str | None = None) -> None:
    asyncio.run(prepare_chronicler_current_head(db_url, domain_schema=domain_schema))


async def open_prepared_chronicler_pool(
    db,
    fresh_db,
    *chains: str,
    domain_schema: str | None = None,
    pool_schema: str | None = None,
    schemas: dict[str, str] | None = None,
    **pool_kwargs,
):
    """Open a Chronicler pool after explicit private prep and domain upgrade.

    The requested existing chains are first cloned with Chronicler pinned at 029;
    then private core+memory are installed before that same domain chain reaches head.
    """
    db_url = await asyncio.to_thread(
        fresh_db,
        *chains,
        schemas=schemas,
        revisions={"chronicler": "chronicler_029"},
    )
    await asyncio.to_thread(
        prepare_chronicler_current_head_sync,
        db_url,
        domain_schema=domain_schema,
    )
    return await db.open(db_url, schema=pool_schema, **pool_kwargs)
