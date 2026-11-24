"""Block-targeted gap interview (bu-r3fmja, REQ-butler-chronicler-017).

Covers the question for a day with no occupation block (the longest uncovered
waking block and its location context), the lane buttons, and the resolver
writing one owner-attested activity block per lane answer.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from butlers.chronicler.adapters.occupation import EPISODE_TYPE_OCCUPATION
from butlers.chronicler.adapters.occupation import SOURCE_NAME as OCCUPATION_SOURCE_NAME
from butlers.chronicler.aggregations import lane_for_activity
from butlers.chronicler.anchors import KIND_HOME, KIND_WORK, SOURCE_GRAPH, AnchorPlace
from butlers.chronicler.gap_interview import (
    BLOCK_OPTIONS,
    OWNER_ANSWER_SOURCE,
    GapInterview,
    _resolve_on_conn,
    build_callback_data,
    describe_location,
    evaluate_gap_interview,
)
from butlers.chronicler.gap_interview_telegram import TelegramInlineButtonTransport
from butlers.chronicler.models import Confidence, Layer, Precision
from butlers.chronicler.situation import Fix

_TZ = ZoneInfo("Asia/Singapore")
_DATE = "2026-11-02"
_START = datetime(2026, 11, 2, tzinfo=_TZ).astimezone(UTC)
_END = datetime(2026, 11, 3, tzinfo=_TZ).astimezone(UTC)
_HOME = (1.2760, 103.8460)
_WORK = (1.2840, 103.8510)
_ANCHORS = (
    AnchorPlace(KIND_HOME, "home", *_HOME, 150, SOURCE_GRAPH),
    AnchorPlace(KIND_WORK, "Acme", *_WORK, 200, SOURCE_GRAPH),
)


def _t(hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 11, 2, hh, mm, tzinfo=_TZ).astimezone(UTC)


def _activity(start: datetime, end: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "layer": "activity",
        "source_name": "google_health.measurements",
        "episode_type": "sleep_episode",
        "start_at": start,
        "end_at": end,
        **extra,
    }


def _evaluate(episodes, fixes=(), anchors=_ANCHORS):
    return evaluate_gap_interview(
        episodes,
        local_date=_DATE,
        day_start_utc=_START,
        day_end_utc=_END,
        tz=_TZ,
        fixes=fixes,
        anchors=anchors,
    )


# A day tracked except 07:55-10:40 and a 20-minute sliver.
_DAY = [
    _activity(_t(0), _t(7, 55)),
    _activity(_t(10, 40), _t(15)),
    _activity(_t(15, 20), _t(23, 59)),
]


# ── Scenario: Question names the block and its location context ─────────────


def test_question_names_the_longest_block_with_no_fixes() -> None:
    decision = _evaluate(_DAY)

    assert decision is not None
    assert decision.question == (
        "Yesterday 07:55–10:40 isn't accounted for (no location fixes). What were you doing?"
    )
    assert (decision.block_start_utc, decision.block_end_utc) == (_t(7, 55), _t(10, 40))
    assert decision.options == BLOCK_OPTIONS
    assert "work day" not in decision.question


def test_question_names_the_anchor_the_fixes_lie_in() -> None:
    decision = _evaluate(_DAY, fixes=[Fix(_t(8), *_WORK), Fix(_t(9), _WORK[0] + 0.0003, _WORK[1])])

    assert decision is not None
    assert "(at Acme)" in decision.question


def test_question_gives_a_distance_from_home_elsewhere() -> None:
    far = (1.3500, 103.9900)  # ~17.8 km
    decision = _evaluate(_DAY, fixes=[Fix(_t(9), *far)])

    assert decision is not None
    assert "(about 18 km from home)" in decision.question


@pytest.mark.parametrize(
    ("lat_lon", "expected"),
    [
        ((1.2900, 103.8460), "about 1.6 km from home"),
        ((3.1400, 101.6900), "about 317 km from home"),
    ],
)
def test_distance_is_shown_coarsely(lat_lon: tuple[float, float], expected: str) -> None:
    assert describe_location([Fix(_t(9), *lat_lon)], _ANCHORS, _t(8), _t(10)) == expected


def test_no_home_anchor_never_invents_a_distance() -> None:
    context = describe_location([Fix(_t(9), 1.35, 103.99)], (), _t(8), _t(10))

    assert context == "away from any registered place"


def test_only_slivers_of_unexplained_time_ask_nothing() -> None:
    # Five 25-minute gaps exceed the 2 h trigger, but none reaches 30 minutes:
    # there is no concrete block to ask about, so no question.
    episodes = [
        _activity(_t(0), _t(7)),
        _activity(_t(7, 25), _t(11)),
        _activity(_t(11, 25), _t(15)),
        _activity(_t(15, 25), _t(19)),
        _activity(_t(19, 25), _t(21)),
        _activity(_t(21, 25), _t(23, 59)),
    ]

    decision = _evaluate(episodes)

    assert decision is None


# ── Scenario: Occupation confirmation is unchanged ───────────────────────────


def test_occupation_block_keeps_the_work_day_question() -> None:
    occupation = {
        "id": uuid4(),
        "layer": "activity",
        "source_name": OCCUPATION_SOURCE_NAME,
        "episode_type": EPISODE_TYPE_OCCUPATION,
        "confidence": "low",
        "start_at": _t(9, 30),
        "end_at": _t(19, 30),
    }

    decision = _evaluate([occupation])

    assert decision is not None
    assert decision.question == "Yesterday 09:30-19:30 looks like a work day — confirm?"
    assert decision.options == ("confirm", "correct", "dismiss")
    assert decision.block_start_utc is None


# ── Buttons ──────────────────────────────────────────────────────────────────


def test_block_interview_shows_lane_buttons_in_rows_of_three() -> None:
    decision = _evaluate(_DAY)
    assert decision is not None
    transport = TelegramInlineButtonTransport(http_client=None, api_base="x", chat_id="1")

    markup = transport._reply_markup(GapInterview(interview_id=_DATE, decision=decision))

    rows = markup["inline_keyboard"]
    assert [len(r) for r in rows] == [3, 3]
    data = [b["callback_data"] for r in rows for b in r]
    assert data == [build_callback_data(_DATE, o) for o in BLOCK_OPTIONS]
    assert all(len(d.encode()) <= 64 for d in data)


# ── Scenario: Lane answer is counted / Dismiss counts nothing ───────────────


class _Conn:
    def __init__(self, pending: dict[str, Any]) -> None:
        self.pending = pending

    async def fetchval(self, _query: str, *_args: Any) -> dict[str, Any]:
        # The pool's JSONB codec decodes the state value to a dict.
        return json.loads(json.dumps(self.pending))

    async def fetchrow(self, _query: str, *_args: Any) -> None:
        return None  # no owner entity bootstrapped

    def transaction(self) -> contextlib.AbstractAsyncContextManager[None]:
        return contextlib.nullcontext()


def _pending(**extra: Any) -> dict[str, Any]:
    return {
        "interview_id": _DATE,
        "local_date": _DATE,
        "occupation_episode_id": None,
        "routine_id": None,
        "block_start_at": _t(7, 55).isoformat(),
        "block_end_at": _t(10, 40).isoformat(),
        "options": list(BLOCK_OPTIONS),
        "answered": False,
        **extra,
    }


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"episodes": [], "states": [], "overrides": []}

    async def fake_upsert(_conn: Any, episode: Any) -> Any:
        captured["episodes"].append(episode)
        episode.id = uuid4()
        return episode

    async def fake_state_set(_conn: Any, key: str, value: Any) -> None:
        captured["states"].append((key, dict(value)))

    async def fake_override(_conn: Any, override: Any) -> Any:
        captured["overrides"].append(override)
        override.id = uuid4()
        return override

    monkeypatch.setattr("butlers.chronicler.storage.upsert_episode", fake_upsert)
    monkeypatch.setattr("butlers.chronicler.storage.insert_override", fake_override)
    monkeypatch.setattr("butlers.core.state.state_set", fake_state_set)
    monkeypatch.setattr(
        "butlers.chronicler.adapters._owner_entity.upsert_owner_episode_entity", AsyncMock()
    )
    return captured


@pytest.mark.parametrize("answer", ["work", "social", "play", "eat", "rest"])
async def test_lane_answer_writes_one_counted_owner_block(
    writes: dict[str, Any], answer: str
) -> None:
    result = await _resolve_on_conn(_Conn(_pending()), _DATE, answer, _t(9))

    assert result["status"] == "applied"
    (episode,) = writes["episodes"]
    assert (episode.source_name, episode.episode_type) == (
        OWNER_ANSWER_SOURCE,
        f"owner_{answer}_block",
    )
    assert (episode.start_at, episode.end_at) == (_t(7, 55), _t(10, 40))
    assert (episode.layer, episode.confidence, episode.precision) == (
        Layer.ACTIVITY,
        Confidence.HIGH,
        Precision.MINUTE,
    )
    assert episode.source_ref == f"gap_interview:{_DATE}"
    expected_lane = {"work": "work", "social": "social", "play": "play", "eat": "eat"}.get(
        answer, "rest"
    )
    assert lane_for_activity("activity", episode.source_name, episode.episode_type) == (
        expected_lane
    )
    assert writes["states"][-1][1]["answered"] is True


async def test_a_duplicate_tap_writes_nothing(writes: dict[str, Any]) -> None:
    result = await _resolve_on_conn(_Conn(_pending(answered=True)), _DATE, "work", _t(9))

    assert result["status"] == "already_answered"
    assert writes["episodes"] == []


async def test_dismiss_counts_nothing(writes: dict[str, Any]) -> None:
    result = await _resolve_on_conn(_Conn(_pending()), _DATE, "dismiss", _t(9))

    assert result["status"] == "applied"
    assert writes["episodes"] == [] and writes["overrides"] == []
    assert writes["states"][-1][1]["answered"] is True


async def test_a_lane_answer_to_an_occupation_interview_is_refused(
    writes: dict[str, Any],
) -> None:
    # A pending row from before bu-r3fmja: no options, so occupation answers only.
    pending = _pending(block_start_at=None, block_end_at=None, occupation_episode_id=str(uuid4()))
    del pending["options"]

    result = await _resolve_on_conn(_Conn(pending), _DATE, "play", _t(9))

    assert result["status"] == "error"
    assert "not offered" in result["error"]
    assert writes["episodes"] == [] and writes["states"] == []


@pytest.mark.parametrize("answer", ["confirm", "correct"])
async def test_an_occupation_answer_to_a_block_interview_is_refused(
    writes: dict[str, Any], answer: str
) -> None:
    result = await _resolve_on_conn(_Conn(_pending()), _DATE, answer, _t(9))

    assert result["status"] == "error"
    assert "not offered" in result["error"]
    assert writes["states"] == []  # the real lane answer can still be given
