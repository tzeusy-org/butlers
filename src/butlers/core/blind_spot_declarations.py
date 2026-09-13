"""Per-module declared-signal namespaces for the blind-spot preamble.

Maps an enabled ``[modules.<name>]`` section to the ``public.expected_signals``
``signal_key`` LIKE-patterns that module's own domain code is responsible for
producing. A butler's declared dependencies are the union of the patterns for
every module it has enabled -- a butler with no ``[modules.email]`` section
declares no email-shaped patterns and therefore can never surface an email
blind spot, regardless of what rows happen to exist in the shared ledger.

Only ``health`` is wired to a real signal today
(``health:measurement-gap:{measurement_type}``, written by
``roster/health/api/router.py``). Other modules will gain entries here as
their own domain code starts producing ``public.expected_signals`` rows for a
connector worth reporting as a blind spot -- adding a module is a one-line
registry addition, not a new mechanism.
"""

from __future__ import annotations

from typing import Any

#: module name -> SQL LIKE patterns it declares a dependency on.
MODULE_BLIND_SPOT_SIGNAL_PATTERNS: dict[str, tuple[str, ...]] = {
    "health": ("health:measurement-gap:%",),
}


def declared_signal_patterns(
    modules: dict[str, Any],
    *,
    registry: dict[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    """Return the deduplicated, order-preserving LIKE patterns declared by *modules*.

    *modules* is a butler's ``ButlerConfig.modules`` mapping (module name ->
    parsed config or raw dict); only its keys matter here. *registry* defaults
    to :data:`MODULE_BLIND_SPOT_SIGNAL_PATTERNS` and exists so callers (and
    tests) can prove the scoping behavior with a synthetic module without
    waiting on a real connector-backed signal to exist.
    """
    active_registry = MODULE_BLIND_SPOT_SIGNAL_PATTERNS if registry is None else registry
    patterns: list[str] = []
    seen: set[str] = set()
    for module_name in modules:
        for pattern in active_registry.get(module_name, ()):
            if pattern not in seen:
                seen.add(pattern)
                patterns.append(pattern)
    return tuple(patterns)


__all__ = ["MODULE_BLIND_SPOT_SIGNAL_PATTERNS", "declared_signal_patterns"]
