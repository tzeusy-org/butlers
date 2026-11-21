"""An unreadable spend-rule set refuses the dispatch; an empty one stays unconstrained.

``apply_spend_routing_rules`` used to log and return the tier-resolved model with no cap when the
rules read failed, silently dropping an owner-set ``max_cost_per_call`` (bu-svqpzo.15).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.model_routing import (
    SPEND_RULE_CAP_NOT_POSITIVE_REASON,
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


def _rule(cap: object) -> dict:
    return {"id": uuid.UUID(int=7), "condition": {}, "action": {"max_cost_per_call": cap}}


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), "nan", "inf", "-inf"])
async def test_a_non_finite_cap_fails_closed_with_its_own_reason(cap: object) -> None:
    """A NaN cap never fires and an infinite one never binds: the rule is unreadable."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_rule(cap)])

    with pytest.raises(SpendRulesUnavailable) as raised:
        await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, _RESOLVED)

    assert "not a finite number" in str(raised.value)
    assert str(raised.value).startswith("Spend rules unavailable")
    assert str(raised.value) != SPEND_RULES_UNAVAILABLE_REASON


async def test_a_finite_cap_is_still_surfaced() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_rule(0.25)])

    result = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, _RESOLVED)

    assert result.max_cost_per_call == 0.25


@pytest.mark.parametrize("cap", ["abc", [1], {"usd": 1}, True, 0, -1, "-0.5", "0"])
async def test_a_non_numeric_or_non_positive_cap_fails_closed_with_its_own_reason(
    cap: object,
) -> None:
    """An owner meant a cap; ignoring a malformed one would run the dispatch uncapped."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_rule(cap)])

    with pytest.raises(SpendRulesUnavailable) as raised:
        await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, _RESOLVED)

    assert "not a positive number" in str(raised.value)
    assert str(raised.value).startswith("Spend rules unavailable")
    assert str(raised.value) == SPEND_RULE_CAP_NOT_POSITIVE_REASON


async def test_an_absent_cap_is_still_no_cap() -> None:
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[{"id": uuid.UUID(int=7), "condition": {}, "action": {"model": "x"}}]
    )

    result = await apply_spend_routing_rules(pool, "general", Complexity.WORKHORSE, _RESOLVED)

    assert result.max_cost_per_call is None
