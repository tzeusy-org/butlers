"""Dependency-inversion hook for the Spawner.

``modules`` that need to dispatch LLM sessions (QaModule, SelfHealingModule)
must not hold a reference to the daemon's Spawner singleton — that would
violate Vision Rule 2 (modules only add tools, never touch core infrastructure).

This module provides:

1. A hook-registration API that the butler daemon calls during startup to wire
   up the live Spawner instance.
2. A ``get_spawner()`` accessor that modules call at dispatch time.  It returns
   the currently-registered Spawner, or ``None`` when no Spawner is registered
   (e.g. in unit tests that do not wire a spawner).

Design rationale
----------------
Core defines the *interface*; the daemon supplies the *implementation* by
calling ``register_spawner(spawner)`` during ``_wire_module_runtime()``.
Modules call ``get_spawner()`` at the moment they need to dispatch — they never
store the reference themselves, which keeps their ``__init__`` free of core
infrastructure attributes and satisfies the module-boundary contract test.

Thread safety
-------------
The hook slot is a module-level global mutated once during daemon startup
(single-threaded phase) and read-only during concurrent session dispatch, so
no locking is required.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hook slot
# ---------------------------------------------------------------------------

#: Registered by the butler daemon during _wire_module_runtime().
_spawner_hook: Any | None = None


# ---------------------------------------------------------------------------
# Registration API (called by the daemon)
# ---------------------------------------------------------------------------


def register_spawner(spawner: Any) -> None:
    """Register the daemon's Spawner instance.

    Called by :meth:`butlers.daemon.ButlerDaemon._wire_module_runtime` after
    the Spawner is fully initialised.  Subsequent calls replace the registered
    instance (e.g. to support hot-restart in tests).

    Args:
        spawner: The live Spawner instance.
    """
    global _spawner_hook
    _spawner_hook = spawner
    logger.debug("spawn_hooks: Spawner registered (%r)", type(spawner).__name__)


def clear_spawner() -> None:
    """Clear the registered Spawner.

    Intended for test teardown — clears the module-level slot so tests are
    isolated from each other.
    """
    global _spawner_hook
    _spawner_hook = None


# ---------------------------------------------------------------------------
# Accessor (called by modules at dispatch time)
# ---------------------------------------------------------------------------


def get_spawner() -> Any | None:
    """Return the registered Spawner instance, or ``None`` if not yet wired.

    Modules should guard dispatch paths with::

        spawner = get_spawner()
        if spawner is None:
            return {"accepted": False, "reason": "not_configured", ...}
        await dispatch_fn(..., spawner=spawner, ...)
    """
    return _spawner_hook
