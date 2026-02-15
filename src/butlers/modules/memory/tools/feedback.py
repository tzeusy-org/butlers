"""Memory feedback tools — confirm accuracy and mark rule effectiveness."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _serialize_row, _storage

logger = logging.getLogger(__name__)


async def memory_confirm(
    pool: Pool,
    memory_type: str,
    memory_id: str,
) -> dict[str, Any]:
    """Confirm a fact or rule is still accurate, resetting confidence decay.

    Converts memory_id to UUID, delegates to _storage.confirm_memory().
    Returns {"confirmed": bool}.
    """
    result = await _storage.confirm_memory(pool, memory_type, uuid.UUID(memory_id))
    return {"confirmed": result}


async def memory_mark_helpful(
    pool: Pool,
    rule_id: str,
) -> dict[str, Any]:
    """Report a rule was applied successfully.

    Delegates to _storage.mark_helpful() and returns the serialized result.
    """
    result = await _storage.mark_helpful(pool, uuid.UUID(rule_id))
    if result is None:
        return {"error": "Rule not found"}
    return _serialize_row(result)


async def memory_mark_harmful(
    pool: Pool,
    rule_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Report a rule caused problems.

    Delegates to _storage.mark_harmful() and returns the serialized result.
    """
    result = await _storage.mark_harmful(pool, uuid.UUID(rule_id), reason=reason)
    if result is None:
        return {"error": "Rule not found"}
    return _serialize_row(result)
