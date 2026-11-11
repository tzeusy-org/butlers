"""Declared dependency IDs a tool descriptor may name in ``depends_on`` (RFC 0038 S6).

A dependency is an external provider a capability needs in order to work. The
set is closed and static so the catalog validates without a database; the
capability-health reducer later reports one health value per ID. Adding a
dependency means adding its ID here, in sorted order.
"""

from __future__ import annotations

from uuid import UUID, uuid5

#: Namespace of the deterministic UUIDs perception uses for these dependencies.
#: ``scripts/init-db.sql`` derives the same values with ``uuid_generate_v5``.
DEPENDENCY_NAMESPACE = UUID("fbb3d862-d597-5afb-b727-949f3a0e8829")

DEPENDENCY_IDS: frozenset[str] = frozenset(
    {"conditions", "google", "home_assistant", "owntracks", "spotify", "steam", "telegram"}
)


def dependency_problem(value: object) -> bool:
    """True when ``value`` is not a sorted, duplicate-free tuple of declared IDs."""
    return (
        not isinstance(value, tuple)
        or any(not isinstance(item, str) or item not in DEPENDENCY_IDS for item in value)
        or list(value) != sorted(set(value))
    )


def dependency_subject(dependency: str) -> UUID:
    """The ``system.dep`` perception subject registered for a declared dependency."""
    if dependency not in DEPENDENCY_IDS:
        raise ValueError(f"undeclared dependency: {dependency!r}")
    return uuid5(DEPENDENCY_NAMESPACE, f"dependency:{dependency}")


def dependency_evidence_ref(dependency: str) -> UUID:
    """The evidence reference the health reducer cites when it publishes a dependency."""
    if dependency not in DEPENDENCY_IDS:
        raise ValueError(f"undeclared dependency: {dependency!r}")
    return uuid5(DEPENDENCY_NAMESPACE, f"evidence:{dependency}")
