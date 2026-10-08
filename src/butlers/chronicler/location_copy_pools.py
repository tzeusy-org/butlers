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
