"""Unit tests for the day-close gap interview (bu-whhll.12).

Covers the pure evaluator's trigger conditions (>2h unaccounted gap,
low-confidence occupation block, neither), the question formatting, the
occupation-anchor/routine extraction, and the transport-agnostic
one-message-per-day orchestration (dedupe, quiet-hours gate, deliver-then-mark).

The DB-heavy answer application (override write shape + routine reinforce/decay)
is exercised in ``tests/integration/test_gap_interview_integration.py`` against
a real Postgres container.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.adapters.occupation import (
    EPISODE_TYPE_OCCUPATION,
)
from butlers.chronicler.adapters.occupation import (
    SOURCE_NAME as OCCUPATION_SOURCE_NAME,
)
from butlers.chronicler.gap_interview import (
    DEFAULT_UNACCOUNTED_THRESHOLD_SECONDS,
    GapInterview,
    GapInterviewAnswer,
    GapInterviewDecision,
    TransportResult,
    evaluate_gap_interview,
    run_gap_interview,
)

_TZ = ZoneInfo("Asia/Singapore")
_DATE = "2026-07-02"


def _utc(hour: int, minute: int = 0, day: int = 2) -> datetime:
    """A UTC instant for the given local (SGT) wall-clock time on 2026-07-02."""
    return datetime(2026, 7, day, hour, minute, tzinfo=_TZ).astimezone(UTC)


def _day_bounds() -> tuple[datetime, datetime]:
    start = datetime(2026, 7, 2, tzinfo=_TZ).astimezone(UTC)
    end = datetime(2026, 7, 3, tzinfo=_TZ).astimezone(UTC)
    return start, end


def _activity(start_h: int, end_h: int, *, layer: str = "activity", **extra) -> dict:
    ep = {
        "layer": layer,
        "source_name": "spotify.session_summary",
        "episode_type": "listening_episode",
        "confidence": "medium",
        "start_at": _utc(start_h),
        "end_at": _utc(end_h),
    }
    ep.update(extra)
    return ep


def _occupation_block(
    start_h: int, end_h: int, *, routine_id: UUID | None = None, ep_id: UUID | None = None
) -> dict:
    return {
        "id": ep_id or uuid4(),
        "layer": "activity",
        "source_name": OCCUPATION_SOURCE_NAME,
        "episode_type": EPISODE_TYPE_OCCUPATION,
        "confidence": "low",
        "start_at": _utc(start_h),
        "end_at": _utc(end_h),
        "payload": {"routine_id": str(routine_id)} if routine_id else {},
    }


# ── evaluate_gap_interview: trigger conditions ──────────────────────────────


def test_large_unaccounted_gap_triggers_prompt():
    """A near-empty day (only a 1h blip) leaves >2h waking unaccounted → prompt."""
    start, end = _day_bounds()
    episodes = [_activity(9, 10)]  # 1h tracked in a 06:00-22:00 waking window
    decision = evaluate_gap_interview(
        episodes, local_date=_DATE, day_start_utc=start, day_end_utc=end, tz=_TZ
    )
    assert decision is not None
    assert "unaccounted_gap" in decision.reasons
    assert decision.unaccounted_seconds > DEFAULT_UNACCOUNTED_THRESHOLD_SECONDS
    # No occupation block → no target episode, generic question.
    assert decision.occupation_episode_id is None
    assert "unaccounted waking time" in decision.question


def test_low_confidence_occupation_block_triggers_prompt():
    """A fully-tracked day with a low-confidence occupation block still prompts."""
    start, end = _day_bounds()
    routine_id = uuid4()
    ep_id = uuid4()
    episodes = [
        # Fill the whole 06:00-22:00 waking window so there is NO unaccounted gap.
        _activity(6, 22),
        _occupation_block(9, 18, routine_id=routine_id, ep_id=ep_id),
    ]
    decision = evaluate_gap_interview(
        episodes, local_date=_DATE, day_start_utc=start, day_end_utc=end, tz=_TZ
    )
    assert decision is not None
    assert decision.reasons == ("low_confidence_occupation",)
    assert decision.unaccounted_seconds <= DEFAULT_UNACCOUNTED_THRESHOLD_SECONDS
    assert decision.occupation_episode_id == ep_id
    assert decision.routine_id == routine_id
    assert decision.window_start_local == "09:00"
    assert decision.window_end_local == "18:00"
    assert decision.question == "Yesterday 09:00-18:00 looks like a work day — confirm?"


def test_neither_condition_no_prompt():
    """A fully-tracked day with no occupation inference does not prompt."""
    start, end = _day_bounds()
    episodes = [_activity(6, 22)]  # whole waking window tracked, no occupation
    decision = evaluate_gap_interview(
        episodes, local_date=_DATE, day_start_utc=start, day_end_utc=end, tz=_TZ
    )
    assert decision is None


def test_empty_day_no_activity_still_prompts_on_gap():
    start, end = _day_bounds()
    decision = evaluate_gap_interview(
        [], local_date=_DATE, day_start_utc=start, day_end_utc=end, tz=_TZ
    )
    assert decision is not None
    assert "unaccounted_gap" in decision.reasons


def test_both_conditions_reported():
    """A sparse day AND a low-confidence occupation block → both reasons."""
    start, end = _day_bounds()
    episodes = [
        _activity(9, 10),
        _occupation_block(9, 12),
    ]
    decision = evaluate_gap_interview(
        episodes, local_date=_DATE, day_start_utc=start, day_end_utc=end, tz=_TZ
    )
    assert decision is not None
    assert set(decision.reasons) == {"unaccounted_gap", "low_confidence_occupation"}
    # Occupation present → its window anchors the question, not the generic one.
    assert decision.question == "Yesterday 09:00-12:00 looks like a work day — confirm?"


def test_intent_layer_episodes_do_not_count_as_tracked():
    """Calendar intent blocks never count toward tracked time (would hide gaps)."""
    start, end = _day_bounds()
    episodes = [
        _activity(9, 10),
        # A full-day calendar 'intent' block must NOT suppress the gap.
        _activity(6, 22, layer="intent", source_name="google_calendar.completed"),
    ]
    decision = evaluate_gap_interview(
        episodes, local_date=_DATE, day_start_utc=start, day_end_utc=end, tz=_TZ
    )
    assert decision is not None
    assert "unaccounted_gap" in decision.reasons


def test_enum_and_string_confidence_shapes_both_detected():
    """Occupation detection works for both Enum-member and plain-string rows."""
    from butlers.chronicler.models import Confidence, Layer

    start, end = _day_bounds()
    block = _occupation_block(9, 18)
    block["confidence"] = Confidence.LOW
    block["layer"] = Layer.ACTIVITY
    episodes = [_activity(6, 22), block]
    decision = evaluate_gap_interview(
        episodes, local_date=_DATE, day_start_utc=start, day_end_utc=end, tz=_TZ
    )
    assert decision is not None
    assert "low_confidence_occupation" in decision.reasons


def test_v_episodes_corrected_string_date_shape_is_coerced():
    """SQL-view rows arrive with ISO-string ``canonical_*`` dates, not datetimes.

    The module documents it tolerates both ``asdict(Episode)`` (datetime) and
    ``v_episodes_corrected`` (plain-string) shapes; a string-dated occupation
    block must still anchor the question window without crashing.
    """
    start, end = _day_bounds()
    routine_id = uuid4()
    ep_id = uuid4()
    episodes = [
        {
            "layer": "activity",
            "source_name": "spotify.session_summary",
            "episode_type": "listening_episode",
            "confidence": "medium",
            "canonical_start_at": _utc(6).isoformat().replace("+00:00", "Z"),
            "canonical_end_at": _utc(22).isoformat(),
        },
        {
            "id": str(ep_id),
            "layer": "activity",
            "source_name": OCCUPATION_SOURCE_NAME,
            "episode_type": EPISODE_TYPE_OCCUPATION,
            "confidence": "low",
            "canonical_start_at": _utc(9).isoformat(),
            "canonical_end_at": _utc(18).isoformat(),
            "payload": {"routine_id": str(routine_id)},
        },
    ]
    decision = evaluate_gap_interview(
        episodes, local_date=_DATE, day_start_utc=start, day_end_utc=end, tz=_TZ
    )
    assert decision is not None
    assert decision.reasons == ("low_confidence_occupation",)
    assert decision.occupation_episode_id == ep_id
    assert decision.routine_id == routine_id
    assert decision.window_start_local == "09:00"
    assert decision.window_end_local == "18:00"


# ── run_gap_interview: one-message-per-day orchestration ────────────────────


class _FakeTransport:
    def __init__(self, delivered: bool = True):
        self.delivered = delivered
        self.calls: list[GapInterview] = []

    async def deliver_interview(self, interview: GapInterview) -> TransportResult:
        self.calls.append(interview)
        return TransportResult(delivered=self.delivered, reference="tg:42")


def _decision_episodes():
    return [_activity(9, 10)]  # qualifies via unaccounted gap


async def _run(*, already: bool, allowed: bool = True, delivered: bool = True, transport=None):
    start, end = _day_bounds()
    transport = transport or _FakeTransport(delivered=delivered)
    marked: list[tuple] = []

    async def already_asked() -> bool:
        return already

    async def mark_asked(decision, interview_id) -> None:
        marked.append((decision, interview_id))

    async def delivery_allowed() -> bool:
        return allowed

    result = await run_gap_interview(
        _decision_episodes(),
        local_date=_DATE,
        day_start_utc=start,
        day_end_utc=end,
        tz=_TZ,
        interview_id="iv-1",
        transport=transport,
        already_asked=already_asked,
        mark_asked=mark_asked,
        delivery_allowed=delivery_allowed,
    )
    return result, transport, marked


async def test_dedupe_skips_second_prompt_same_day():
    result, transport, marked = await _run(already=True)
    assert result["status"] == "already_asked"
    assert transport.calls == []  # never even evaluated/delivered
    assert marked == []


async def test_asks_once_and_marks_when_qualifying():
    result, transport, marked = await _run(already=False)
    assert result["status"] == "asked"
    assert result["interview_id"] == "iv-1"
    assert len(transport.calls) == 1
    assert len(marked) == 1  # marked asked only after delivery


async def test_quiet_hours_defers_without_marking():
    result, transport, marked = await _run(already=False, allowed=False)
    assert result["status"] == "deferred_quiet_hours"
    assert transport.calls == []
    assert marked == []  # NOT marked → retried next run


async def test_delivery_failure_not_marked():
    result, transport, marked = await _run(already=False, delivered=False)
    assert result["status"] == "delivery_failed"
    assert len(transport.calls) == 1
    assert marked == []  # failed send does not burn the daily prompt


async def test_no_gap_returns_without_delivering():
    start, end = _day_bounds()
    transport = _FakeTransport()

    async def already_asked() -> bool:
        return False

    async def mark_asked(decision, interview_id) -> None:  # pragma: no cover
        raise AssertionError("must not mark when nothing qualifies")

    async def delivery_allowed() -> bool:  # pragma: no cover
        raise AssertionError("must not reach the gate when nothing qualifies")

    result = await run_gap_interview(
        [_activity(6, 22)],  # fully tracked, no occupation → no gap
        local_date=_DATE,
        day_start_utc=start,
        day_end_utc=end,
        tz=_TZ,
        interview_id="iv-1",
        transport=transport,
        already_asked=already_asked,
        mark_asked=mark_asked,
        delivery_allowed=delivery_allowed,
    )
    assert result["status"] == "no_gap"
    assert transport.calls == []


def test_decision_is_frozen_dataclass():
    d = GapInterviewDecision(
        local_date=_DATE, question="?", reasons=("unaccounted_gap",), unaccounted_seconds=1.0
    )
    with pytest.raises((AttributeError, Exception)):
        d.local_date = "x"  # type: ignore[misc]
    assert d.options == ("confirm", "correct", "dismiss")


def test_answer_enum_roundtrip():
    assert GapInterviewAnswer("confirm") is GapInterviewAnswer.CONFIRM
    assert GapInterviewAnswer("correct") is GapInterviewAnswer.CORRECT
    assert GapInterviewAnswer("dismiss") is GapInterviewAnswer.DISMISS


# ── callback_data encode/decode (cgi: prefix) ───────────────────────────────


def test_build_and_parse_callback_roundtrip():
    from butlers.chronicler.gap_interview import build_callback_data, parse_gap_interview_callback

    data = build_callback_data("2026-07-02", GapInterviewAnswer.CONFIRM)
    assert data == "cgi:2026-07-02:confirm"
    assert parse_gap_interview_callback(data) == ("2026-07-02", "confirm")
    # A telegram callback_data must stay within the 64-byte budget.
    assert len(build_callback_data("2026-07-02", "dismiss").encode()) <= 64


def test_parse_callback_rejects_foreign_and_malformed():
    from butlers.chronicler.gap_interview import parse_gap_interview_callback

    assert parse_gap_interview_callback(None) is None
    assert parse_gap_interview_callback("") is None
    assert parse_gap_interview_callback("other:2026-07-02:confirm") is None  # wrong prefix
    assert parse_gap_interview_callback("cgi:") is None  # empty body
    assert parse_gap_interview_callback("cgi:2026-07-02") is None  # no answer segment


def test_parse_callback_interview_id_with_colons_survives():
    from butlers.chronicler.gap_interview import parse_gap_interview_callback

    # rpartition splits on the LAST colon, so a colon-bearing id round-trips.
    assert parse_gap_interview_callback("cgi:2026-07-02:abc:confirm") == (
        "2026-07-02:abc",
        "confirm",
    )
