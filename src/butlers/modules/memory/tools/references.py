"""Bounded stable references for actionable local memory context."""

from __future__ import annotations

import uuid
from typing import NamedTuple

_ACTIONABLE_MEMORY_TYPES = frozenset({"fact", "rule"})


class MemoryActionTarget(NamedTuple):
    memory_type: str
    memory_id: uuid.UUID


def format_memory_ref(memory_type: str, memory_id: object) -> str:
    """Return the canonical type-and-UUID reference for one actionable row."""
    if memory_type not in _ACTIONABLE_MEMORY_TYPES:
        raise ValueError("Memory reference unavailable")
    parsed_id = memory_id if isinstance(memory_id, uuid.UUID) else uuid.UUID(str(memory_id))
    return f"{memory_type}:{parsed_id}"


def parse_memory_ref(value: str) -> MemoryActionTarget:
    """Parse a canonical reference without reflecting rejected input."""
    if not isinstance(value, str):
        raise ValueError("Memory reference unavailable")
    memory_type, separator, raw_id = value.partition(":")
    if separator != ":" or memory_type not in _ACTIONABLE_MEMORY_TYPES:
        raise ValueError("Memory reference unavailable")
    try:
        memory_id = uuid.UUID(raw_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Memory reference unavailable") from exc
    if str(memory_id) != raw_id:
        raise ValueError("Memory reference unavailable")
    return MemoryActionTarget(memory_type, memory_id)
