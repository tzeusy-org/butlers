"""Constructor-owned pool identities without projection/runtime imports."""

from typing import Any

import asyncpg

# Source-configured exact pool objects, not caller butler/session strings.
_copy_pools: set[Any] = set()
_api_copy_pools: set[Any] = set()


def register_api_copy_pool(pool: Any) -> None:
    """Actual DatabaseManager pool at the fixed owning API constructor seam."""
    for old in tuple(_api_copy_pools):
        if isinstance(old, asyncpg.Pool) and old.is_closing():
            _api_copy_pools.discard(old)
    if isinstance(pool, asyncpg.Pool) and not pool.is_closing():
        _api_copy_pools.add(pool)


def _prune_closed_copy_pools() -> None:
    # asyncpg.Pool has slots and does not support weak references. Keep the
    # exact live pool until shutdown; a closing pool refuses new acquisitions.
    for pool in tuple(_copy_pools):
        if isinstance(pool, asyncpg.Pool) and pool.is_closing():
            _copy_pools.discard(pool)


def register_native_copy_pool(pool: Any) -> None:
    """Fixed owning module constructor; no model/caller enrollment door."""
    _prune_closed_copy_pools()
    if isinstance(pool, asyncpg.Pool) and not pool.is_closing():
        _copy_pools.add(pool)


def native_copy_pool(pool: Any) -> bool:
    _prune_closed_copy_pools()
    return pool in _copy_pools


def unregister_native_copy_pool(pool: Any) -> None:
    # Module shutdown may precede a final in-flight completion. Keep its fence
    # for the exact live pool; closed-pool pruning releases it after disposal.
    if isinstance(pool, asyncpg.Pool) and not pool.is_closing():
        return
    _copy_pools.discard(pool)


# Fixed DatabaseManager enrollment is writer-only. It cannot enroll a runtime,
# receiving invocation, source loan or remote consumer.
_api_writers: dict[Any, tuple[str, str, str]] = {}


async def register_api_memory_writer(
    pool: Any, domain_schema: str | None, memory_schema: str | None
):
    from butlers.chronicler.location_policy import PolicyUnavailableError

    if not isinstance(pool, asyncpg.Pool):
        return  # Unconfigured doubles establish no real writer authority.
    if domain_schema != "chronicler" or memory_schema != "chronicler_mem" or pool.is_closing():
        raise PolicyUnavailableError("Native API Memory configuration is unavailable")
    from butlers.db import schema_search_path

    schema_search_path(memory_schema)  # Exact constructor-owned identifier validation.
    async with pool.acquire() as conn:
        schema, role = (
            await conn.fetchval("SELECT current_schema()"),
            await conn.fetchval("SELECT current_user"),
        )
        if schema != domain_schema or not isinstance(role, str):
            raise PolicyUnavailableError("Native API Memory writer identity differs")
    for old in tuple(_api_writers):
        if isinstance(old, asyncpg.Pool) and old.is_closing():
            _api_writers.pop(old)
    _api_writers[pool] = (domain_schema, memory_schema, role)
