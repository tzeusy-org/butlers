"""Dependency-inversion hooks for memory module operations.

``core`` and ``core_tools`` need to invoke memory operations (forget a memory,
fetch context, store an episode, run scheduled consolidation) without importing
the memory module directly.

This module provides:

1. A hook-registration API that ``modules.memory`` calls during startup to wire
   up its concrete implementations.
2. Thin async stubs that ``core`` calls; each stub delegates to the registered
   hook when available, or no-ops (returning a safe default) when the memory
   module is not loaded.

Design rationale
----------------
Rather than coupling core to ``butlers.modules.memory.*``, core defines the
*shape* of the operations it needs (via ``register_*`` calls) and modules supply
the implementations.  This is classic dependency inversion: core owns the
interface; modules own the implementation.

Thread safety
-------------
The hooks are module-level globals mutated during daemon startup (single-
threaded phase).  They are read-only during concurrent session dispatch, so no
locking is required.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hook slots
# ---------------------------------------------------------------------------

#: ``async (pool, memory_type, memory_id, **kwargs) -> dict``
#: Registered by modules.memory on startup.
_memory_forget_hook: Callable[..., Coroutine[Any, Any, dict[str, Any]]] | None = None

#: ``async (pool, butler_name, prompt, *, token_budget) -> str | None``
#: Registered by modules.memory on startup.
_memory_context_hook: Callable[..., Coroutine[Any, Any, str | None]] | None = None

#: ``async (pool, butler_name, session_output, session_id) -> bool``
#: Registered by modules.memory on startup.
_memory_store_episode_hook: Callable[..., Coroutine[Any, Any, bool]] | None = None

#: ``async (pool, query, *, limit, mode) -> list[dict]``
#: Registered by modules.memory on startup.
_catalog_search_hook: Callable[..., Coroutine[Any, Any, list[dict[str, Any]]]] | None = None

#: ``async (*, spawner, batch_size, enable_shared_catalog) -> dict``
#: Registered by modules.memory on startup.  The module-owned closure resolves
#: its configured pool and embedding engine at dispatch time.
_memory_consolidation_hook: Callable[..., Coroutine[Any, Any, dict[str, Any]]] | None = None


# ---------------------------------------------------------------------------
# Registration API (called by modules.memory)
# ---------------------------------------------------------------------------


def register_memory_forget(
    fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
) -> None:
    """Register the memory-forget implementation from ``modules.memory``.

    Args:
        fn: Async callable with signature
            ``(pool, memory_type, memory_id, **kwargs) -> dict``.
    """
    global _memory_forget_hook
    _memory_forget_hook = fn


def register_memory_context(
    fn: Callable[..., Coroutine[Any, Any, str | None]],
) -> None:
    """Register the memory-context fetcher from ``modules.memory``.

    Args:
        fn: Async callable with signature
            ``(pool, butler_name, prompt, *, token_budget) -> str | None``.
    """
    global _memory_context_hook
    _memory_context_hook = fn


def register_memory_store_episode(
    fn: Callable[..., Coroutine[Any, Any, bool]],
) -> None:
    """Register the memory episode-store from ``modules.memory``.

    Args:
        fn: Async callable with signature
            ``(pool, butler_name, session_output, session_id) -> bool``.
    """
    global _memory_store_episode_hook
    _memory_store_episode_hook = fn


def register_catalog_search(
    fn: Callable[..., Coroutine[Any, Any, list[dict[str, Any]]]],
) -> None:
    """Register the ``public.memory_catalog`` search implementation from ``modules.memory``.

    Args:
        fn: Async callable with signature
            ``(pool, query, *, limit, mode) -> list[dict]``.
    """
    global _catalog_search_hook
    _catalog_search_hook = fn


def register_memory_consolidation(
    fn: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
) -> None:
    """Register scheduled consolidation against the started memory module.

    The concrete hook owns the module-specific pool and embedding-engine
    lifecycle.  Core supplies only the daemon Spawner and bounded batch size.
    """
    global _memory_consolidation_hook
    _memory_consolidation_hook = fn


def clear_memory_consolidation() -> None:
    """Clear the scheduled-consolidation hook during module shutdown."""
    global _memory_consolidation_hook
    _memory_consolidation_hook = None


# ---------------------------------------------------------------------------
# Core-callable stubs
# ---------------------------------------------------------------------------


async def memory_forget(
    pool: Any,
    memory_type: str,
    memory_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Soft-delete a memory.  No-ops if the memory module is not loaded.

    Delegates to the hook registered by ``modules.memory``.  Returns
    ``{"forgotten": False, "error": "memory module not loaded"}`` when
    no hook is registered rather than raising so callers remain safe.
    """
    if _memory_forget_hook is None:
        logger.debug("memory_forget called but memory module hook not registered; skipping")
        return {"forgotten": False, "error": "memory module not loaded"}
    return await _memory_forget_hook(pool, memory_type, memory_id, **kwargs)


async def fetch_memory_context(
    pool: Any,
    butler_name: str,
    prompt: str,
    *,
    token_budget: int = 3000,
) -> str | None:
    """Fetch memory context for a butler session.  Returns None if not loaded.

    Delegates to the hook registered by ``modules.memory``.
    """
    if _memory_context_hook is None:
        return None
    return await _memory_context_hook(pool, butler_name, prompt, token_budget=token_budget)


async def store_session_episode(
    pool: Any,
    butler_name: str,
    session_output: str,
    session_id: Any = None,
) -> bool:
    """Store a session episode.  Returns False if the memory module is not loaded.

    Delegates to the hook registered by ``modules.memory``.
    """
    if _memory_store_episode_hook is None:
        return False
    return await _memory_store_episode_hook(pool, butler_name, session_output, session_id)


async def search_memory_catalog(
    pool: Any,
    query: str,
    *,
    limit: int = 1,
    mode: str = "hybrid",
) -> list[dict[str, Any]]:
    """Hybrid-search ``public.memory_catalog``. Returns ``[]`` if not loaded.

    Delegates to the hook registered by ``modules.memory`` (which resolves the
    embedding engine internally, mirroring ``fetch_memory_context`` above).
    Used by ``core.delegation_ledger.resolve_target_via_catalog`` (bu-gxmfx)
    to resolve "whose domain covers this question" via the shared catalog
    discovery index without core importing ``modules.memory`` directly.
    """
    if _catalog_search_hook is None:
        return []
    return await _catalog_search_hook(pool, query, limit=limit, mode=mode)


async def consolidate_memory(
    *,
    spawner: Any,
    batch_size: int,
    enable_shared_catalog: bool,
) -> dict[str, Any]:
    """Run consolidation through the active memory module runtime.

    Unlike best-effort memory context/search hooks, scheduled consolidation is
    durable work.  Missing runtime wiring therefore fails closed so the
    scheduler records a diagnostic error instead of claiming the wrong schema
    or silently leaving episodes pending.
    """
    if _memory_consolidation_hook is None:
        raise RuntimeError(
            "memory_consolidation requires the memory module runtime hook to be registered"
        )
    return await _memory_consolidation_hook(
        spawner=spawner,
        batch_size=batch_size,
        enable_shared_catalog=enable_shared_catalog,
    )
