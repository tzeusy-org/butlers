"""Unit tests for `butlers.chronicler.narration` (bu-v9y18, telemetry-
distillation bead 6).

Covers the pure functions directly (no I/O) and the
`narrate_daily_rollup` async orchestrator against monkeypatched storage
calls and a fake `DiscretionDispatcher`. Pure-unit tests — no Docker /
PostgreSQL, no network, no real LLM call required.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler.flags import FLAG_FEEDER_DARK, FLAG_SLEEP_MISSING
from butlers.chronicler.models import DailyRollup, DailyRollupFlag
from butlers.chronicler.narration import (
    _SYSTEM_PROMPT,
    build_narration_prompt,
    narrate_daily_rollup,
    narration_enabled,
    parse_narration_response,
    select_top_episode_titles,
    should_skip_narration,
)

pytestmark = pytest.mark.unit

_LOCAL_DATE = date(2026, 7, 5)
_DAY_START = datetime(2026, 6, 30, 17, 0, tzinfo=UTC)  # 2026-07-01 00:00 SGT-equivalent shape
_DAY_END = datetime(2026, 7, 1, 17, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# should_skip_narration
# ---------------------------------------------------------------------------


_EATING_DARK = [{"lane": "eat", "state": "not_captured", "since": "2026-08-13"}]


def test_skips_when_no_rollup_rows() -> None:
    skip, reason = should_skip_narration([], [], uncaptured=[])
    assert skip is True
    assert reason == "no_rollup_data"


def test_feeder_dark_flag_alone_no_longer_skips_the_whole_day() -> None:
    rollups = [
        DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=100),
        DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=900),
    ]
    flags = [DailyRollupFlag(local_date=_LOCAL_DATE, flag_type=FLAG_FEEDER_DARK)]
    assert should_skip_narration(rollups, flags, uncaptured=_EATING_DARK) == (False, None)


def test_skips_only_when_every_rolled_up_lane_is_uncaptured() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="eat", seconds=0)]
    skip, reason = should_skip_narration(rollups, [], uncaptured=_EATING_DARK)
    assert (skip, reason) == (True, "all_lanes_dark")


def test_unreadable_availability_skips_as_unknown() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=500)]
    assert should_skip_narration(rollups, [], uncaptured=None) == (True, "availability_unknown")


def test_does_not_skip_healthy_day_with_other_flags() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=0)]
    flags = [DailyRollupFlag(local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING)]
    assert should_skip_narration(rollups, flags, uncaptured=[]) == (False, None)


def test_does_not_skip_healthy_day_with_no_flags() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=500)]
    assert should_skip_narration(rollups, [], uncaptured=[]) == (False, None)


# ---------------------------------------------------------------------------
# select_top_episode_titles
# ---------------------------------------------------------------------------


def _episode(
    *, source_name: str, episode_type: str, layer: str, title: str, start: datetime, end: datetime
) -> dict[str, Any]:
    return {
        "source_name": source_name,
        "episode_type": episode_type,
        "layer": layer,
        "title": title,
        "start_at": start,
        "end_at": end,
        "trigger_source": None,
    }


def test_selects_top_n_titles_by_duration_per_lane() -> None:
    episodes = [
        _episode(
            source_name="chronicler.situation_inferred",
            episode_type="play_block",
            layer="activity",
            title="short session",
            start=_DAY_START,
            end=_DAY_START.replace(hour=18),
        ),
        _episode(
            source_name="chronicler.situation_inferred",
            episode_type="play_block",
            layer="activity",
            title="long session",
            start=_DAY_START.replace(hour=19),
            end=_DAY_START.replace(hour=23),
        ),
        _episode(
            source_name="chronicler.situation_inferred",
            episode_type="play_block",
            layer="activity",
            title="third session",
            start=_DAY_START.replace(hour=23),
            end=_DAY_END.replace(hour=1),
        ),
    ]

    result = select_top_episode_titles(
        episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END, limit=2
    )

    assert "play" in result
    assert len(result["play"]) == 2
    assert "long session" in result["play"]
    assert "short session" not in result["play"]


def test_untitled_episodes_are_excluded() -> None:
    episodes = [
        _episode(
            source_name="chronicler.situation_inferred",
            episode_type="play_block",
            layer="activity",
            title="",
            start=_DAY_START,
            end=_DAY_START.replace(hour=18),
        )
    ]
    result = select_top_episode_titles(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result == {}
    titled = [{**episodes[0], "title": "counted Play"}]
    assert select_top_episode_titles(titled, day_start_utc=_DAY_START, day_end_utc=_DAY_END) == {
        "play": ["counted Play"]
    }


def test_episodes_with_no_lane_are_excluded() -> None:
    episodes = [
        _episode(
            source_name="unmapped.source",
            episode_type="whatever",
            layer="evidence",
            title="untracked thing",
            start=_DAY_START,
            end=_DAY_START.replace(hour=18),
        )
    ]
    result = select_top_episode_titles(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result == {}


def test_episodes_outside_window_are_excluded() -> None:
    outside_start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    outside_end = datetime(2026, 6, 1, 1, 0, tzinfo=UTC)
    episodes = [
        _episode(
            source_name="chronicler.situation_inferred",
            episode_type="play_block",
            layer="activity",
            title="way before the window",
            start=outside_start,
            end=outside_end,
        )
    ]
    result = select_top_episode_titles(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result == {}
    assert select_top_episode_titles(
        episodes, day_start_utc=outside_start, day_end_utc=outside_end
    ) == {"play": ["way before the window"]}


# ---------------------------------------------------------------------------
# build_narration_prompt
# ---------------------------------------------------------------------------


def test_build_narration_prompt_includes_lanes_flags_and_titles() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=0, episode_count=0)]
    flags = [
        DailyRollupFlag(local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING, severity="warning")
    ]

    prompt = build_narration_prompt(
        local_date=_LOCAL_DATE,
        rollup_rows=rollups,
        flag_rows=flags,
        top_episode_titles={"sleep": ["a nap"]},
    )

    assert "2026-07-05" in prompt
    assert "sleep_missing" in prompt
    assert "a nap" in prompt


# ---------------------------------------------------------------------------
# parse_narration_response
# ---------------------------------------------------------------------------


def test_parse_narration_response_happy_path() -> None:
    raw = '{"day_summary": "A quiet day.", "flag_labels": {"sleep_missing": "No sleep recorded."}}'
    result = parse_narration_response(raw, known_flag_types=["sleep_missing"])
    assert result == ("A quiet day.", {"sleep_missing": "No sleep recorded."})


def test_parse_narration_response_strips_code_fences() -> None:
    raw = '```json\n{"day_summary": "Fenced.", "flag_labels": {}}\n```'
    result = parse_narration_response(raw, known_flag_types=[])
    assert result == ("Fenced.", {})


def test_parse_narration_response_drops_unknown_flag_type() -> None:
    raw = '{"day_summary": "", "flag_labels": {"made_up_flag": "should be dropped"}}'
    result = parse_narration_response(raw, known_flag_types=["sleep_missing"])
    assert result is None  # empty summary + no surviving labels -> nothing usable


def test_parse_narration_response_invalid_json_returns_none() -> None:
    assert parse_narration_response("not json at all", known_flag_types=[]) is None


def test_parse_narration_response_non_object_json_returns_none() -> None:
    assert parse_narration_response("[1, 2, 3]", known_flag_types=[]) is None


def test_parse_narration_response_empty_summary_and_labels_returns_none() -> None:
    raw = '{"day_summary": "", "flag_labels": {}}'
    assert parse_narration_response(raw, known_flag_types=[]) is None


def test_parse_narration_response_drops_empty_label_values() -> None:
    raw = '{"day_summary": "fine", "flag_labels": {"sleep_missing": "  "}}'
    result = parse_narration_response(raw, known_flag_types=["sleep_missing"])
    assert result == ("fine", {})


def test_prompt_lists_uncaptured_lanes_and_omits_them_when_none() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=500)]
    kwargs = dict(local_date=_LOCAL_DATE, rollup_rows=rollups, flag_rows=[], top_episode_titles={})
    with_lanes = json.loads(build_narration_prompt(**kwargs, uncaptured=_EATING_DARK))
    assert with_lanes["uncaptured_lanes"] == _EATING_DARK
    assert json.loads(build_narration_prompt(**kwargs, uncaptured=[]))["uncaptured_lanes"] == []


def test_system_prompt_forbids_describing_uncaptured_lanes() -> None:
    assert "uncaptured_lanes" in _SYSTEM_PROMPT
    assert "absence" in _SYSTEM_PROMPT


@pytest.mark.parametrize(
    "summary",
    [
        "The owner skipped lunch.",
        "No meals were logged.",
        "The day went without dinner.",
        "Breakfast didn't happen.",
    ],
)
def test_parse_rejects_absence_claim_about_uncaptured_lane(summary: str) -> None:
    raw = json.dumps({"day_summary": summary, "flag_labels": {}})
    assert parse_narration_response(raw, known_flag_types=[], uncaptured=_EATING_DARK) is None


def test_parse_rejects_absence_claim_inside_a_flag_label() -> None:
    raw = json.dumps({"day_summary": "Fine.", "flag_labels": {"sleep_missing": "No meal data."}})
    assert (
        parse_narration_response(raw, known_flag_types=["sleep_missing"], uncaptured=_EATING_DARK)
        is None
    )


def test_parse_allows_other_lane_negations_and_positive_mentions() -> None:
    raw = json.dumps(
        {"day_summary": "No exercise was recorded; work dominated.", "flag_labels": {}}
    )
    assert parse_narration_response(raw, known_flag_types=[], uncaptured=_EATING_DARK) == (
        "No exercise was recorded; work dominated.",
        {},
    )
    positive = json.dumps({"day_summary": "A long lunch meeting ran on.", "flag_labels": {}})
    assert parse_narration_response(positive, known_flag_types=[], uncaptured=_EATING_DARK)


def test_parse_without_uncaptured_lanes_is_unchanged() -> None:
    raw = json.dumps({"day_summary": "No meals were logged.", "flag_labels": {}})
    assert parse_narration_response(raw, known_flag_types=[]) is not None


# ---------------------------------------------------------------------------
# narration_enabled (owner toggle)
# ---------------------------------------------------------------------------


def test_narration_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    assert narration_enabled() is True


@pytest.mark.parametrize("value", ["false", "False", "0", "no", "off"])
def test_narration_disabled_via_env(monkeypatch, value: str) -> None:
    monkeypatch.setenv("CHRONICLER_NARRATION_ENABLED", value)
    assert narration_enabled() is False


# ---------------------------------------------------------------------------
# narrate_daily_rollup orchestrator
# ---------------------------------------------------------------------------


class _FakeDispatcher:
    def __init__(self, *, response: str | None = None, raises: Exception | None = None) -> None:
        self._response = response
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def call(self, prompt: str, system_prompt: str = "") -> str:
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        if self._raises is not None:
            raise self._raises
        assert self._response is not None
        return self._response


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class _NarrationConnection:
    def transaction(self):
        return _AsyncContext(self)


class _NarrationPool:
    def acquire(self):
        return _AsyncContext(_NarrationConnection())


def _patch_common(
    monkeypatch, *, rollups, flags, top_episodes=None, uncaptured=()
) -> dict[str, Any]:
    written: dict[str, Any] = {"day_narrative": None, "flag_narratives": {}}

    async def _fake_list_daily_rollups(pool, *, local_date):
        return rollups

    async def _fake_list_daily_rollup_flags(pool, *, local_date):
        return flags

    async def _fake_fetch_top_episode_titles(pool, *, day_start_utc, day_end_utc):
        return top_episodes or {}

    async def _fake_set_day_narrative(pool, *, local_date, narrative):
        written["day_narrative"] = narrative
        return 1

    async def _fake_set_flag_narrative(pool, *, local_date, flag_type, narrative):
        written["flag_narratives"][flag_type] = narrative
        return DailyRollupFlag(local_date=local_date, flag_type=flag_type, narrative=narrative)

    async def _fake_capture_fact_write_token(_pool, **_kwargs):
        return object()

    async def _allow_fact_write(_conn):
        return None

    async def _fake_load_uncaptured(pool, *, local_date, timezone):
        return None if uncaptured is None else list(uncaptured)

    monkeypatch.setattr("butlers.chronicler.narration._load_uncaptured", _fake_load_uncaptured)
    monkeypatch.setattr("butlers.chronicler.narration.list_daily_rollups", _fake_list_daily_rollups)
    monkeypatch.setattr(
        "butlers.chronicler.narration.list_daily_rollup_flags", _fake_list_daily_rollup_flags
    )
    monkeypatch.setattr(
        "butlers.chronicler.narration._fetch_top_episode_titles", _fake_fetch_top_episode_titles
    )
    monkeypatch.setattr(
        "butlers.chronicler.narration.set_daily_rollup_day_narrative", _fake_set_day_narrative
    )
    monkeypatch.setattr(
        "butlers.chronicler.narration.set_daily_rollup_flag_narrative", _fake_set_flag_narrative
    )
    monkeypatch.setattr(
        "butlers.chronicler.narration.capture_fact_write_token", _fake_capture_fact_write_token
    )
    monkeypatch.setattr(
        "butlers.chronicler.narration.make_fact_write_guard", lambda _token: _allow_fact_write
    )
    return written


async def test_narrate_daily_rollup_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CHRONICLER_NARRATION_ENABLED", "false")
    result = await narrate_daily_rollup(_NarrationPool(), local_date=_LOCAL_DATE)
    assert result == {"local_date": "2026-07-05", "status": "disabled"}


async def test_narrate_daily_rollup_skips_no_rollup_data(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    _patch_common(monkeypatch, rollups=[], flags=[])

    result = await narrate_daily_rollup(_NarrationPool(), local_date=_LOCAL_DATE)

    assert result == {"local_date": "2026-07-05", "status": "skipped_no_rollup_data"}


async def test_narrate_feeder_dark_day_with_available_lanes_is_narrated(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [
        DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=900),
        DailyRollup(local_date=_LOCAL_DATE, lane="eat", seconds=0),
    ]
    flags = [DailyRollupFlag(local_date=_LOCAL_DATE, flag_type=FLAG_FEEDER_DARK)]
    written = _patch_common(monkeypatch, rollups=rollups, flags=flags, uncaptured=_EATING_DARK)
    prompts: list[str] = []

    class _Capturing(_FakeDispatcher):
        async def call(self, prompt: str, system_prompt: str = "") -> str:
            prompts.append(prompt)
            return await super().call(prompt, system_prompt)

    monkeypatch.setattr(
        "butlers.chronicler.narration.DiscretionDispatcher",
        lambda *a, **kw: _Capturing(
            response='{"day_summary": "Work dominated.", "flag_labels": {}}'
        ),
    )

    result = await narrate_daily_rollup(_NarrationPool(), local_date=_LOCAL_DATE)

    assert result["status"] == "labeled"
    assert written["day_narrative"] == "Work dominated."
    assert json.loads(prompts[0])["uncaptured_lanes"] == _EATING_DARK


async def test_narrate_skips_when_every_lane_uncaptured(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="eat", seconds=0)]
    _patch_common(monkeypatch, rollups=rollups, flags=[], uncaptured=_EATING_DARK)
    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)
    assert result == {"local_date": "2026-07-05", "status": "skipped_all_lanes_dark"}


async def test_narrate_skips_when_availability_snapshot_unreadable(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=500)]
    _patch_common(monkeypatch, rollups=rollups, flags=[], uncaptured=None)
    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)
    assert result == {"local_date": "2026-07-05", "status": "skipped_availability_unknown"}


async def test_narrate_rejects_absence_claim_about_uncaptured_lane(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [
        DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=900),
        DailyRollup(local_date=_LOCAL_DATE, lane="eat", seconds=0),
    ]
    written = _patch_common(monkeypatch, rollups=rollups, flags=[], uncaptured=_EATING_DARK)
    monkeypatch.setattr(
        "butlers.chronicler.narration.DiscretionDispatcher",
        lambda *a, **kw: _FakeDispatcher(
            response='{"day_summary": "No meals were logged.", "flag_labels": {}}'
        ),
    )
    result = await narrate_daily_rollup(_NarrationPool(), local_date=_LOCAL_DATE)
    assert result["status"] == "llm_output_invalid"
    assert written["day_narrative"] is None


async def test_narrate_daily_rollup_llm_unavailable_on_exception(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=100)]
    _patch_common(monkeypatch, rollups=rollups, flags=[])
    monkeypatch.setattr(
        "butlers.chronicler.narration.DiscretionDispatcher",
        lambda *a, **kw: _FakeDispatcher(raises=RuntimeError("no model configured")),
    )

    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)

    assert result == {"local_date": "2026-07-05", "status": "llm_unavailable"}


async def test_narrate_daily_rollup_llm_output_invalid(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=100)]
    written = _patch_common(monkeypatch, rollups=rollups, flags=[])
    monkeypatch.setattr(
        "butlers.chronicler.narration.DiscretionDispatcher",
        lambda *a, **kw: _FakeDispatcher(response="not json"),
    )

    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)

    assert result == {"local_date": "2026-07-05", "status": "llm_output_invalid"}
    assert written["day_narrative"] is None
    assert written["flag_narratives"] == {}


async def test_narrate_daily_rollup_labels_successfully(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=0)]
    flags = [DailyRollupFlag(local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING)]
    written = _patch_common(monkeypatch, rollups=rollups, flags=flags)
    response = (
        '{"day_summary": "Sleep was not recorded.", '
        '"flag_labels": {"sleep_missing": "No sleep data logged."}}'
    )
    monkeypatch.setattr(
        "butlers.chronicler.narration.DiscretionDispatcher",
        lambda *a, **kw: _FakeDispatcher(response=response),
    )

    result = await narrate_daily_rollup(_NarrationPool(), local_date=_LOCAL_DATE)

    assert result["status"] == "labeled"
    assert result["day_summary_written"] is True
    assert result["flags_labeled"] == [FLAG_SLEEP_MISSING]
    assert written["day_narrative"] == "Sleep was not recorded."
    assert written["flag_narratives"] == {FLAG_SLEEP_MISSING: "No sleep data logged."}


async def test_narrate_daily_rollup_never_writes_unknown_flag_label(monkeypatch) -> None:
    """Even if the LLM invents a flag_type not present today, nothing gets
    written for it (parse_narration_response already filters this, but this
    covers the orchestrator end-to-end)."""
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=500)]
    written = _patch_common(monkeypatch, rollups=rollups, flags=[])
    response = '{"day_summary": "Busy day.", "flag_labels": {"invented_flag": "nope"}}'
    monkeypatch.setattr(
        "butlers.chronicler.narration.DiscretionDispatcher",
        lambda *a, **kw: _FakeDispatcher(response=response),
    )

    result = await narrate_daily_rollup(_NarrationPool(), local_date=_LOCAL_DATE)

    assert result["status"] == "labeled"
    assert result["flags_labeled"] == []
    assert written["flag_narratives"] == {}
    assert written["day_narrative"] == "Busy day."


async def test_narration_refuses_to_publish_after_source_generation_changes(monkeypatch) -> None:
    from butlers.chronicler.history_repair import RepairAdmissionError

    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    events: list[str] = []
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=100)]
    written = _patch_common(monkeypatch, rollups=rollups, flags=[])

    async def capture(_pool, **_kwargs):
        events.append("capture")
        return object()

    async def guard(_conn):
        events.append("guard")
        raise RepairAdmissionError("daily repair state changed")

    async def list_rollups(_pool, *, local_date):
        events.append("rollups")
        return rollups

    async def list_flags(_pool, *, local_date):
        events.append("flags")
        return []

    class Context:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return None

    class Connection:
        def transaction(self):
            return Context(self)

    class Pool:
        def acquire(self):
            return Context(Connection())

    async def call(_prompt, system_prompt=""):
        events.append("llm")
        return '{"day_summary":"A quiet day.","flag_labels":{}}'

    monkeypatch.setattr(
        "butlers.chronicler.narration.capture_fact_write_token", capture, raising=False
    )
    monkeypatch.setattr(
        "butlers.chronicler.narration.make_fact_write_guard", lambda _token: guard, raising=False
    )
    monkeypatch.setattr("butlers.chronicler.narration.list_daily_rollups", list_rollups)
    monkeypatch.setattr("butlers.chronicler.narration.list_daily_rollup_flags", list_flags)
    monkeypatch.setattr(
        "butlers.chronicler.narration._fetch_top_episode_titles", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        "butlers.chronicler.narration.DiscretionDispatcher",
        lambda *args, **kwargs: type("Dispatcher", (), {"call": staticmethod(call)})(),
    )

    result = await narrate_daily_rollup(Pool(), local_date=_LOCAL_DATE)

    assert result == {
        "local_date": _LOCAL_DATE.isoformat(),
        "status": "waiting_repair",
        "reason": "daily repair state changed",
    }
    assert (
        events.index("capture")
        < events.index("rollups")
        < events.index("llm")
        < events.index("guard")
    )
    assert written["day_narrative"] is None
    assert written["flag_narratives"] == {}
