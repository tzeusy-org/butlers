"""Canonical identity-slot serialization, also used by the lightweight operator."""

from __future__ import annotations

from typing import Any


def identity_slot_key(predicate: str, value: str) -> str:
    if predicate == "has-phone":
        # Suffix equivalence has no transitive hash key. Serialize phone changes.
        return "relationship:identity:phone"
    normalized = value.strip().casefold()
    if predicate == "has-handle" and normalized.startswith("telegram:"):
        normalized = "telegram:" + normalized[len("telegram:") :].lstrip("@")
    return f"relationship:identity:{predicate}:{normalized}"


async def lock_identity_slot(conn: Any, predicate: str, value: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", identity_slot_key(predicate, value)
    )
