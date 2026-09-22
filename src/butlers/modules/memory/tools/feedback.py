"""Memory feedback tools — confirm accuracy and mark rule effectiveness."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _search, _serialize_row, _storage
from butlers.modules.memory.tools.references import MemoryActionTarget, parse_memory_ref

logger = logging.getLogger(__name__)

_UNAVAILABLE = "Memory reference unavailable"


def _resolve_target(
    *,
    memory_ref: str | None,
    memory_type: str | None,
    memory_id: str | None,
    expected_type: str | None = None,
) -> MemoryActionTarget | None:
    """Resolve exactly one target shape without reflecting rejected values."""
    if memory_ref is not None:
        if memory_type is not None or memory_id is not None:
            return None
        try:
            target = parse_memory_ref(memory_ref)
        except ValueError:
            return None
    else:
        if memory_type is None or memory_id is None:
            return None
        try:
            target = MemoryActionTarget(memory_type, uuid.UUID(memory_id))
        except (AttributeError, TypeError, ValueError):
            return None
    if expected_type is not None and target.memory_type != expected_type:
        return None
    if target.memory_type not in {"fact", "rule"}:
        return None
    return target


async def _held_policy(pool: Pool, read_policy):
    return read_policy or await _search.load_catalog_read_policy(pool)


async def memory_confirm(
    pool: Pool,
    memory_type: str | None = None,
    memory_id: str | None = None,
    *,
    memory_ref: str | None = None,
    read_policy=None,
) -> dict[str, Any]:
    """Confirm a fact or rule is still accurate, resetting confidence decay.

    A typed ``memory_ref`` is mutually exclusive with the legacy type/ID pair.
    Authority comes from the server-held read policy, never from the reference.
    """
    target = _resolve_target(
        memory_ref=memory_ref,
        memory_type=memory_type,
        memory_id=memory_id,
    )
    if target is None:
        return {"confirmed": False, "error": _UNAVAILABLE}
    policy = await _held_policy(pool, read_policy)
    result = await _storage.confirm_memory(
        pool,
        target.memory_type,
        target.memory_id,
        allowed_sensitivities=policy.allowed_sensitivities,
    )
    if not result:
        return {"confirmed": False, "error": _UNAVAILABLE}
    return {"confirmed": True}


async def memory_mark_helpful(
    pool: Pool,
    rule_id: str | None = None,
    *,
    memory_ref: str | None = None,
    read_policy=None,
) -> dict[str, Any]:
    """Report a rule was applied successfully.

    Reference-based calls return only a bounded acknowledgement. Legacy ID
    calls retain their existing serialized-row response for compatibility.
    """
    target = _resolve_target(
        memory_ref=memory_ref,
        memory_type="rule" if rule_id is not None else None,
        memory_id=rule_id,
        expected_type="rule",
    )
    if target is None:
        return {"error": _UNAVAILABLE}
    policy = await _held_policy(pool, read_policy)
    result = await _storage.mark_helpful(
        pool,
        target.memory_id,
        allowed_sensitivities=policy.allowed_sensitivities,
    )
    if result is None:
        return {"error": _UNAVAILABLE}
    if memory_ref is not None:
        return {"helpful": True}
    return _serialize_row(result)


async def memory_mark_harmful(
    pool: Pool,
    rule_id: str | None = None,
    *,
    memory_ref: str | None = None,
    reason: str | None = None,
    read_policy=None,
) -> dict[str, Any]:
    """Report a rule caused problems.

    Reference-based calls return only a bounded acknowledgement. Legacy ID
    calls retain their existing serialized-row response for compatibility.
    """
    target = _resolve_target(
        memory_ref=memory_ref,
        memory_type="rule" if rule_id is not None else None,
        memory_id=rule_id,
        expected_type="rule",
    )
    if target is None:
        return {"error": _UNAVAILABLE}
    policy = await _held_policy(pool, read_policy)
    result = await _storage.mark_harmful(
        pool,
        target.memory_id,
        reason=reason,
        allowed_sensitivities=policy.allowed_sensitivities,
    )
    if result is None:
        return {"error": _UNAVAILABLE}
    if memory_ref is not None:
        return {"harmful": True}
    return _serialize_row(result)
