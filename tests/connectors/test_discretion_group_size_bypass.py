"""Tests for the group-size discretion bypass.

Small groups (participant_count <= group_size_bypass_max) skip the LLM
discretion filter entirely and always FORWARD — the default system prompt
instructs the LLM to IGNORE "group banter", which is exactly the
low-content-but-frequent signal Dunbar interaction scoring needs to see for
family/close-circle-sized chats. Large groups (participant_count above the
threshold, or unknown) keep full LLM-gated filtering so token cost stays
bounded on high-volume/mass-membership chats — that's the whole point of the
threshold, so the bypass must never fire on missing data.

Covers:
- A small group bypasses discretion (LLM never called), regardless of weight.
- A large group still calls the LLM and honours its verdict.
- Unknown participant_count (None) never bypasses, even with a threshold set.
- A DM (chat_type="private" or omitted) never bypasses via this mechanism,
  even at participant_count<=threshold — DMs conventionally report
  participant_count=2 for unrelated Dunbar-eligibility bookkeeping, and that
  is not "small group banter."
- No threshold configured (default) preserves prior behaviour (full evaluation).
- A group at exactly the threshold bypasses; one over it does not (boundary).
- Channel bypass and weight bypass still take precedence / compose correctly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.connectors.discretion import DiscretionEvaluator

pytestmark = pytest.mark.unit


def _make_dispatcher(response: str = "IGNORE") -> AsyncMock:
    """A discretion LLM caller mock whose ``call`` returns *response*."""
    dispatcher = AsyncMock()
    dispatcher.call = AsyncMock(return_value=response)
    return dispatcher


async def test_small_group_bypasses_discretion() -> None:
    """A group at or under the threshold FORWARDs without calling the LLM."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="wa:family", dispatcher=dispatcher, group_size_bypass_max=20
    )

    # weight=0.1 would normally fail-closed on an LLM IGNORE; the group-size
    # bypass must override that regardless of sender weight.
    result = await evaluator.evaluate(
        text="lol nice one", weight=0.1, participant_count=8, chat_type="group"
    )

    assert result.verdict == "FORWARD"
    assert result.reason == "group-size-bypass"
    assert result.is_fail_open is False
    dispatcher.call.assert_not_called()


async def test_large_group_runs_full_discretion() -> None:
    """A group over the threshold still calls the LLM and honours its IGNORE."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="wa:mass", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(
        text="ambient chatter", weight=0.7, participant_count=300, chat_type="group"
    )

    assert result.verdict == "IGNORE"
    dispatcher.call.assert_awaited_once()


async def test_dm_participant_count_never_bypasses() -> None:
    """A DM (chat_type='private') must NOT bypass via group-size, even at a
    participant_count within the threshold.

    DMs conventionally report participant_count=2 for unrelated
    Dunbar-eligibility bookkeeping (a DM is always interaction-eligible).
    Without the chat_type guard, every DM would silently skip discretion
    regardless of sender trust — a real regression this test guards against
    (caught by tests/integration/test_whatsapp_pipeline.py failing when this
    guard was missing).
    """
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="wa:dm", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(
        text="ambient chatter", weight=0.7, participant_count=2, chat_type="private"
    )

    assert result.verdict == "IGNORE"
    dispatcher.call.assert_awaited_once()


async def test_small_broadcast_channel_never_bypasses() -> None:
    """A broadcast/newsletter channel (chat_type='channel') must NOT bypass via
    group-size, even at a participant_count within the threshold.

    A small niche channel can genuinely have a low subscriber count, but it's
    one-to-many announcement content, not organic group chatter — the same
    class of mismatch as the DM case (small participant_count that doesn't
    mean "small group banter"). This is an allow-list guard
    (chat_type in {"group", "supergroup"}), not a "!= private" deny-list —
    that distinction matters because Telegram's participant-count resolution
    genuinely queries channels too.
    """
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="tg:channel", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(
        text="announcement", weight=0.7, participant_count=5, chat_type="channel"
    )

    assert result.verdict == "IGNORE"
    dispatcher.call.assert_awaited_once()


async def test_supergroup_is_eligible_for_bypass() -> None:
    """chat_type='supergroup' (Telegram's large-group type) IS eligible —
    confirms the allow-list isn't accidentally narrower than the deny-list
    it replaced."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="tg:supergroup", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(
        text="lol nice", weight=0.1, participant_count=8, chat_type="supergroup"
    )

    assert result.verdict == "FORWARD"
    assert result.reason == "group-size-bypass"
    dispatcher.call.assert_not_called()


async def test_omitted_chat_type_never_bypasses() -> None:
    """chat_type omitted (None) must NOT bypass, even at a small participant_count.

    Mirrors participant_count=None's fail-safe direction: a caller that
    doesn't yet know the chat type must not accidentally get treated as a
    small group.
    """
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="wa:unknown-type", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(text="banter", weight=0.7, participant_count=8)

    assert result.verdict == "IGNORE"
    dispatcher.call.assert_awaited_once()


async def test_unknown_participant_count_never_bypasses() -> None:
    """participant_count=None must never bypass, even with a threshold set.

    This is the fail-safe direction: most WhatsApp groups report an unknown
    count today, and treating "unknown" as "small" would defeat the whole
    point of the threshold (mass groups must stay filtered for cost).
    """
    dispatcher = _make_dispatcher(response="FORWARD: looks like a request")
    evaluator = DiscretionEvaluator(
        source_name="wa:unknown", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(
        text="what's the weather?", weight=0.7, participant_count=None
    )

    assert result.verdict == "FORWARD"
    assert result.reason == "looks like a request"
    dispatcher.call.assert_awaited_once()


async def test_no_threshold_configured_preserves_full_discretion() -> None:
    """group_size_bypass_max unset (default) → unchanged behaviour."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(source_name="wa:default", dispatcher=dispatcher)

    result = await evaluator.evaluate(text="banter", weight=0.7, participant_count=3)

    assert result.verdict == "IGNORE"
    dispatcher.call.assert_awaited_once()


async def test_boundary_at_threshold_bypasses() -> None:
    """participant_count exactly equal to the threshold bypasses (inclusive)."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="wa:boundary", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(
        text="banter", weight=0.1, participant_count=20, chat_type="group"
    )

    assert result.verdict == "FORWARD"
    assert result.reason == "group-size-bypass"
    dispatcher.call.assert_not_called()


async def test_boundary_one_over_threshold_does_not_bypass() -> None:
    """participant_count one over the threshold still runs full discretion."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="wa:boundary", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(text="banter", weight=0.7, participant_count=21)

    assert result.verdict == "IGNORE"
    dispatcher.call.assert_awaited_once()


async def test_channel_bypass_still_takes_precedence() -> None:
    """A dashboard-channel message bypasses via channel-bypass, not group-size."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="dash", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(
        text="please pay rent", weight=0.1, channel="dashboard", participant_count=300
    )

    assert result.verdict == "FORWARD"
    assert result.reason == "channel-bypass"
    dispatcher.call.assert_not_called()


async def test_weight_bypass_still_works_alongside_group_size_bypass() -> None:
    """An owner-weight message still bypasses via weight-bypass on a large group."""
    dispatcher = _make_dispatcher(response="IGNORE")
    evaluator = DiscretionEvaluator(
        source_name="wa:mass", dispatcher=dispatcher, group_size_bypass_max=20
    )

    result = await evaluator.evaluate(text="owner message", weight=1.0, participant_count=300)

    assert result.verdict == "FORWARD"
    assert result.reason == "weight-bypass"
    dispatcher.call.assert_not_called()
