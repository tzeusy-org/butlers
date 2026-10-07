"""Canonical identity-slot serialization, also used by the lightweight operator."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from uuid import UUID


def identity_slot_key(predicate: str, value: str) -> str:
    normalized = value.strip().casefold()
    if predicate == "has-phone":
        normalized = re.sub(r"\D", "", value)
    elif predicate == "has-handle" and normalized.startswith("telegram:"):
        normalized = "telegram:" + normalized[len("telegram:") :].lstrip("@")
    return f"relationship:identity:{predicate}:{normalized}"


def identity_slot_keys(predicate: str, value: str, subject: UUID | None = None) -> set[str]:
    if predicate == "prefers-channel":
        if subject is None:
            raise ValueError("preferred channel serialization requires its actual subject")
        return {f"relationship:prefers-channel:{subject}"}
    if predicate != "has-phone":
        return {identity_slot_key(predicate, value)}
    digits = re.sub(r"\D", "", value)
    # Exactly the existing core233 suffix universe: both numbers have >=8
    # digits and differ by at most two leading digits. Every colliding pair
    # shares keys, while unrelated phone changes have no global mutex.
    numbers = {digits}
    if len(digits) >= 8:
        numbers.update(f"{prefix:01d}{digits}" for prefix in range(10))
        numbers.update(f"{prefix:02d}{digits}" for prefix in range(100))
        numbers.update(digits[n:] for n in (1, 2) if len(digits) - n >= 8)
    return {identity_slot_key(predicate, number) for number in numbers}


async def lock_identity_slots(conn: Any, slots: Iterable[tuple[str, str, UUID | None]]) -> None:
    keys = {
        key
        for predicate, value, subject in slots
        for key in identity_slot_keys(predicate, value, subject)
    }
    # A merge acquires the complete union in one order, rather than ordering
    # each overlapping phone universe independently.
    for key in sorted(keys):
        await conn.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1,0))", key
        )


async def lock_identity_slot(
    conn: Any, predicate: str, value: str, subject: UUID | None = None
) -> None:
    await lock_identity_slots(conn, [(predicate, value, subject)])
