"""Synthetic owner admission for tests in both roots; no authentication evidence."""

from contextlib import asynccontextmanager

from butlers.core.fact_authority import FactWriteContext, _current_report


@asynccontextmanager
async def synthetic_owner(pool):
    owner = await pool.fetchrow(
        "SELECT id,created_at FROM public.entities WHERE 'owner'=ANY(roles) ORDER BY id LIMIT 1"
    )
    if owner is None:
        owner = await pool.fetchrow(
            "INSERT INTO public.entities(canonical_name,entity_type,roles) "
            "VALUES('Synthetic fixture owner','person',ARRAY['owner']) RETURNING id,created_at"
        )
    token = _current_report.set(
        FactWriteContext(
            "owner_device",
            owner["id"],
            owner["created_at"],
            owner["id"],
        )
    )
    try:
        yield
    finally:
        _current_report.reset(token)
