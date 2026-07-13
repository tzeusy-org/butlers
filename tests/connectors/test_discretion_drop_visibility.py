"""Tests for the WhatsApp discretion fail-open policy + per-channel drop metric (bu-cicgb).

Audit finding: on ``whatsapp_user_client`` the discretion filter dropped 90% of
messages (288 filtered vs 32 ingested all-time), and every *classifiable* recent
drop was ``failover_exhausted`` — the same-tier discretion-model failover
exhausted and the low-weight (``unknown`` = 0.3 < the 0.5 fail-open default)
WhatsApp sender fail-CLOSED to IGNORE. Not one drop was a genuine LLM
``llm_verdict`` noise-judgment. The fix makes WhatsApp fail OPEN on a discretion
infra failure (so the owner's messages are not silently lost when the model is
down), while a genuine LLM IGNORE still drops; and adds a low-cardinality
``discretion_ignore_total{channel, kind}`` counter so per-channel over-filtering
(and the genuine-vs-infra split) is visible without re-sampling payloads.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.connectors.discretion import (
    DiscretionEvaluator,
    WeightTier,
    discretion_ignore_total,
    record_discretion_ignore,
)
from butlers.connectors.whatsapp_user_client import _WHATSAPP_DISCRETION_WEIGHT_FAIL_OPEN

pytestmark = pytest.mark.unit


_FAILOVER_EXC = RuntimeError(
    "same_tier_failover_exhausted: tier=specialty after 5 attempt(s); last error: TimeoutError: "
)


def _failing_dispatcher() -> AsyncMock:
    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(side_effect=_FAILOVER_EXC)
    return dispatcher


# ---------------------------------------------------------------------------
# Fail-open policy for WhatsApp (weight_fail_open threshold)
# ---------------------------------------------------------------------------


def test_whatsapp_fail_open_threshold_is_the_unknown_floor() -> None:
    """The WhatsApp threshold equals the ``unknown`` tier so EVERY WhatsApp
    sender (min weight = unknown) fails open on a discretion infra failure."""
    assert _WHATSAPP_DISCRETION_WEIGHT_FAIL_OPEN == WeightTier().unknown == 0.3


async def test_unknown_sender_fails_open_at_whatsapp_threshold() -> None:
    """With the WhatsApp threshold, an ``unknown`` (0.3) sender FORWARDS when the
    discretion model cannot render a verdict — the message is not silently lost."""
    evaluator = DiscretionEvaluator(
        source_name="wa:chat",
        dispatcher=_failing_dispatcher(),
        weight_fail_open=_WHATSAPP_DISCRETION_WEIGHT_FAIL_OPEN,
    )

    result = await evaluator.evaluate(text="Can you grab milk on the way home?", weight=0.3)

    assert result.verdict == "FORWARD"
    assert result.is_fail_open is True
    assert result.reason == "fail-open: failover_exhausted"


async def test_unknown_sender_fails_closed_at_default_threshold() -> None:
    """Contrast: the shared 0.5 default fail-CLOSES the same case — this is the
    exact behaviour (100% infra drop) the WhatsApp threshold change corrects."""
    evaluator = DiscretionEvaluator(
        source_name="wa:chat",
        dispatcher=_failing_dispatcher(),
        # no weight_fail_open override -> default 0.5
    )

    result = await evaluator.evaluate(text="Can you grab milk on the way home?", weight=0.3)

    assert result.verdict == "IGNORE"
    assert result.is_fail_open is False


async def test_genuine_llm_ignore_still_drops_under_whatsapp_threshold() -> None:
    """Fail-open only changes the error path: a genuine LLM IGNORE (the model ran
    and judged noise) still drops even with the permissive WhatsApp threshold."""
    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="wa:chat",
        dispatcher=dispatcher,
        weight_fail_open=_WHATSAPP_DISCRETION_WEIGHT_FAIL_OPEN,
    )

    result = await evaluator.evaluate(text="lol", weight=0.3)

    assert result.verdict == "IGNORE"
    assert result.is_fail_open is False


# ---------------------------------------------------------------------------
# Per-channel discretion-drop metric
# ---------------------------------------------------------------------------


def _counter_value(*, channel: str, kind: str) -> float:
    return discretion_ignore_total.labels(channel=channel, kind=kind)._value.get()


def test_record_discretion_ignore_increments_channel_kind() -> None:
    before = _counter_value(channel="whatsapp", kind="failover_exhausted")
    record_discretion_ignore(channel="whatsapp", kind="failover_exhausted")
    after = _counter_value(channel="whatsapp", kind="failover_exhausted")
    assert after == before + 1


def test_record_discretion_ignore_separates_kinds() -> None:
    """A genuine-noise drop and an infra drop land on distinct label sets, so the
    per-channel genuine-vs-infra split is queryable."""
    llm_before = _counter_value(channel="whatsapp", kind="llm_verdict")
    infra_before = _counter_value(channel="whatsapp", kind="failover_exhausted")

    record_discretion_ignore(channel="whatsapp", kind="llm_verdict")

    assert _counter_value(channel="whatsapp", kind="llm_verdict") == llm_before + 1
    # The infra bucket is untouched.
    assert _counter_value(channel="whatsapp", kind="failover_exhausted") == infra_before
