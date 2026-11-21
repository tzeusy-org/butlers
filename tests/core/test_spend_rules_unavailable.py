"""An unreadable spend-rule set refuses the dispatch; an empty one stays unconstrained.

``apply_spend_routing_rules`` used to log and return the tier-resolved model with no cap when the
rules read failed, silently dropping an owner-set ``max_cost_per_call`` (bu-svqpzo.15).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.model_routing import (
    SPEND_RULES_UNAVAILABLE_REASON,
    Complexity,
    SpendRulesUnavailable,
    apply_spend_routing_rules,
)

pytestmark = pytest.mark.unit

_RESOLVED = ("claude", "model-a", [], uuid.UUID(int=1), 600)


async def test_a_failed_rules_read_raises_spend_rules_unavailable() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=OSError("connection refused"))

    with pytest.raises(SpendRulesUnavailable) as raised:
        await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, _RESOLVED)

    assert str(raised.value) == SPEND_RULES_UNAVAILABLE_REASON


async def test_a_readable_empty_rule_set_stays_unconstrained() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])

    result = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, _RESOLVED)

    assert result.resolved == _RESOLVED
    assert result.max_cost_per_call is None


def test_the_refusal_reason_is_distinct_from_the_cap_and_ceiling_reasons() -> None:
    assert "Per-call spend cap exceeded" not in SPEND_RULES_UNAVAILABLE_REASON
    assert "Monthly spend ceiling" not in SPEND_RULES_UNAVAILABLE_REASON
