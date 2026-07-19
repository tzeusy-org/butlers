"""Dependency-inversion hooks for memory module operations.

``core`` and ``core_tools`` need to invoke memory operations (forget a memory,
fetch context, store an episode, run scheduled consolidation) without importing
the memory module directly.

This module provides:

1. A hook-registration API that ``modules.memory`` calls during startup to wire
   up its concrete implementations.
2. Thin async stubs that ``core`` calls; each stub delegates to the registered
   hook when available, or no-ops (returning a safe default) when the memory
   module is not loaded. Durable scheduled maintenance is the exception: it
   fails closed when the module runtime is unavailable.

Design rationale
----------------
Rather than coupling core to ``butlers.modules.memory.*``, core defines the
*shape* of the operations it needs (via ``register_*`` calls) and modules supply
the implementations.  This is classic dependency inversion: core owns the
interface; modules own the implementation.

Thread safety
-------------
Best-effort session hooks and durable maintenance runtimes are keyed by butler
identity.  Session callers provide that identity directly; maintenance callers
bind it through a ``ContextVar`` for each scheduler dispatch.  Neither path can
cross-resolve another daemon's pool or Spawner when multiple daemons share a
process.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hook slots
# ---------------------------------------------------------------------------

#: ``async (pool, memory_type, memory_id, **kwargs) -> dict``
#: Registered by modules.memory on startup.
_memory_forget_hook: Callable[..., Coroutine[Any, Any, dict[str, Any]]] | None = None

#: ``async (pool, query, *, limit, mode) -> list[dict]``
#: Registered by modules.memory on startup.
_catalog_search_hook: Callable[..., Coroutine[Any, Any, list[dict[str, Any]]]] | None = None


@dataclass(frozen=True)
class MemorySessionRuntime:
    """One started memory module's best-effort session hooks.

    Context and episode storage share one runtime because both callbacks capture
    the same module-owned memory pool.  Keeping them together makes startup
    replacement atomic and lets shutdown remove only the lifecycle instance it
    registered.
    """

    context: Callable[..., Coroutine[Any, Any, str | None]]
    store_episode: Callable[..., Coroutine[Any, Any, bool]]


@dataclass(frozen=True)
class MemoryMaintenanceRuntime:
    """One started memory module's maintenance-only runtime hooks.

    The owner key lives outside this value so a module can replace its own
    registration atomically while an older instance shuts down without
    clearing the replacement.
    """

    pool_resolver: Callable[[], Any]
    consolidation: Callable[..., Coroutine[Any, Any, dict[str, Any]]]


@dataclass(frozen=True)
class _MemoryMaintenanceDispatch:
    """Ambient scheduler identity for one deterministic handler invocation."""

    butler_name: str
    spawner: Any


# Maintenance is intentionally not a single process-global hook.  Multiple
# daemons run in one process during development/tests, and each can own a
# different memory schema and embedding lifecycle (notably chronicler_mem).
_memory_session_runtimes: dict[str, MemorySessionRuntime] = {}
_memory_maintenance_runtimes: dict[str, MemoryMaintenanceRuntime] = {}
_memory_maintenance_dispatch: ContextVar[_MemoryMaintenanceDispatch | None] = ContextVar(
    "memory_maintenance_dispatch",
    default=None,
)


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


def _normalize_memory_session_owner(butler_name: str) -> str:
    """Return a registry key for a started memory module's daemon identity."""
    if not isinstance(butler_name, str) or not butler_name.strip():
        raise ValueError("memory session runtime requires a non-empty butler name")
    return butler_name.strip()


def register_memory_session_runtime(
    butler_name: str,
    *,
    context: Callable[..., Coroutine[Any, Any, str | None]],
    store_episode: Callable[..., Coroutine[Any, Any, bool]],
) -> MemorySessionRuntime:
    """Register one daemon's started memory session runtime.

    Session callers already supply ``butler_name`` to the core stubs, so the
    registry uses that explicit owner instead of a process-global last-started
    callback.  The returned runtime is an opaque lifecycle token for
    :func:`unregister_memory_session_runtime`.
    """
    owner = _normalize_memory_session_owner(butler_name)
    runtime = MemorySessionRuntime(context=context, store_episode=store_episode)
    _memory_session_runtimes[owner] = runtime
    return runtime


def unregister_memory_session_runtime(
    butler_name: str,
    runtime: MemorySessionRuntime,
) -> None:
    """Remove *runtime* only when it remains that owner's registration.

    A module can be replaced before its older instance finishes shutdown.
    Identity comparison protects the newer runtime rather than clearing the
    owner's session hooks altogether.
    """
    owner = _normalize_memory_session_owner(butler_name)
    if _memory_session_runtimes.get(owner) is runtime:
        del _memory_session_runtimes[owner]


def _resolve_memory_session_runtime(butler_name: str) -> MemorySessionRuntime | None:
    """Return the active runtime for a session owner, if it has one."""
    if not isinstance(butler_name, str):
        return None
    return _memory_session_runtimes.get(butler_name.strip())


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


def register_memory_maintenance_runtime(
    butler_name: str,
    *,
    pool_resolver: Callable[[], Any],
    consolidation: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
) -> MemoryMaintenanceRuntime:
    """Register one butler's started memory-maintenance runtime.

    ``butler_name`` is the daemon's schema-backed identity.  Scheduler
    dispatch binds that same identity with a ContextVar, so maintenance work
    cannot borrow a runtime pool, configured embedding engine, or Spawner from
    the most recently started daemon.
    """
    if not isinstance(butler_name, str) or not butler_name.strip():
        raise ValueError("memory maintenance runtime requires a non-empty butler name")

    runtime = MemoryMaintenanceRuntime(
        pool_resolver=pool_resolver,
        consolidation=consolidation,
    )
    _memory_maintenance_runtimes[butler_name.strip()] = runtime
    return runtime


def unregister_memory_maintenance_runtime(
    butler_name: str,
    runtime: MemoryMaintenanceRuntime,
) -> None:
    """Remove *runtime* only when it is still the owner registration.

    An older module instance may finish shutdown after a replacement has
    started.  Identity comparison preserves the newer registration instead of
    clearing another daemon's maintenance path.
    """
    if _memory_maintenance_runtimes.get(butler_name) is runtime:
        del _memory_maintenance_runtimes[butler_name]


@contextmanager
def bind_memory_maintenance_dispatch(
    *,
    butler_name: str,
    spawner: Any,
) -> Iterator[None]:
    """Bind one scheduler invocation to its daemon's memory runtime.

    The scheduler owns the live Spawner and the butler name.  Context-local
    binding keeps concurrent deterministic handlers isolated without widening
    every maintenance-handler signature.
    """
    if not isinstance(butler_name, str) or not butler_name.strip():
        raise ValueError("memory maintenance dispatch requires a non-empty butler name")

    token = _memory_maintenance_dispatch.set(
        _MemoryMaintenanceDispatch(butler_name=butler_name.strip(), spawner=spawner)
    )
    try:
        yield
    finally:
        _memory_maintenance_dispatch.reset(token)


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

    Delegates to the invoking butler's runtime registered by ``modules.memory``.
    A missing owner returns the existing best-effort ``None`` default rather
    than borrowing another butler's runtime.
    """
    runtime = _resolve_memory_session_runtime(butler_name)
    if runtime is None:
        logger.debug(
            "memory context runtime not registered for butler %r; skipping",
            butler_name,
        )
        return None
    return await runtime.context(pool, butler_name, prompt, token_budget=token_budget)


async def store_session_episode(
    pool: Any,
    butler_name: str,
    session_output: str,
    session_id: Any = None,
) -> bool:
    """Store a session episode.  Returns False if the memory module is not loaded.

    Delegates to the invoking butler's runtime registered by ``modules.memory``.
    A missing owner returns the existing best-effort ``False`` default rather
    than storing through another butler's runtime.
    """
    runtime = _resolve_memory_session_runtime(butler_name)
    if runtime is None:
        logger.debug(
            "memory episode runtime not registered for butler %r; skipping",
            butler_name,
        )
        return False
    return await runtime.store_episode(pool, butler_name, session_output, session_id)


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


def _resolve_memory_maintenance_runtime() -> tuple[
    MemoryMaintenanceRuntime, _MemoryMaintenanceDispatch
]:
    """Return the runtime selected by the current scheduler dispatch.

    Direct execution is deliberately rejected: the deterministic scheduler is
    the only layer that can establish the owner identity and live Spawner.
    Falling back to a process-global or supplied daemon pool would silently
    target the wrong schema after another daemon starts.
    """
    dispatch = _memory_maintenance_dispatch.get()
    if dispatch is None:
        raise RuntimeError(
            "memory maintenance requires a dispatch-scoped runtime context from the scheduler"
        )

    runtime = _memory_maintenance_runtimes.get(dispatch.butler_name)
    if runtime is None:
        raise RuntimeError(
            f"memory maintenance runtime is not registered for butler {dispatch.butler_name!r}"
        )
    return runtime, dispatch


async def consolidate_memory(
    *,
    batch_size: int,
    enable_shared_catalog: bool,
) -> dict[str, Any]:
    """Run consolidation through this dispatch's started memory module.

    Unlike best-effort memory context/search hooks, scheduled consolidation is
    durable work.  Missing runtime wiring therefore fails closed so the
    scheduler records a diagnostic error instead of claiming the wrong schema
    or silently leaving episodes pending.
    """
    runtime, dispatch = _resolve_memory_maintenance_runtime()
    if dispatch.spawner is None:
        raise RuntimeError("memory_consolidation requires the dispatching daemon's live Spawner")
    return await runtime.consolidation(
        spawner=dispatch.spawner,
        batch_size=batch_size,
        enable_shared_catalog=enable_shared_catalog,
    )


def resolve_memory_runtime_pool() -> Any:
    """Return this dispatch's MemoryModule authoritative storage pool.

    The scheduler gives every deterministic job the daemon/domain pool, but
    memory maintenance may instead need a module-private schema.  Do not fall
    back to that supplied domain pool when no MemoryModule has registered: the
    job would either target the wrong schema or hide a stopped module.  Raising
    lets the scheduler record a diagnostic error and retry after startup
    wiring is restored.
    """
    runtime, _dispatch = _resolve_memory_maintenance_runtime()
    pool = runtime.pool_resolver()
    if pool is None:
        raise RuntimeError("memory module runtime pool resolver returned no pool")
    return pool
