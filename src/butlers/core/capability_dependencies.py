"""Declared dependency IDs a tool descriptor may name in ``depends_on`` (RFC 0038 S6).

A dependency is an external provider a capability needs in order to work. The
set is closed and static so the catalog validates without a database; the
capability-health reducer later reports one health value per ID. Adding a
dependency means adding its ID here, in sorted order.
"""

from __future__ import annotations

DEPENDENCY_IDS: frozenset[str] = frozenset(
    {"google", "home_assistant", "owntracks", "spotify", "steam", "telegram"}
)


def dependency_problem(value: object) -> bool:
    """True when ``value`` is not a sorted, duplicate-free tuple of declared IDs."""
    return (
        not isinstance(value, tuple)
        or any(not isinstance(item, str) or item not in DEPENDENCY_IDS for item in value)
        or list(value) != sorted(set(value))
    )
