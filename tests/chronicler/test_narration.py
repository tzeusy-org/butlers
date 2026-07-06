"""Unit tests for `butlers.chronicler.narration` (bu-v9y18, telemetry-
distillation bead 6).

Covers the pure functions directly (no I/O) and the
`narrate_daily_rollup` async orchestrator against monkeypatched storage
calls and a fake `DiscretionDispatcher`. Pure-unit tests — no Docker /
PostgreSQL, no network, no real LLM call required.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler.flags import FLAG_FEEDER_DARK, FLAG_SLEEP_MISSING
from butlers.chronicler.models import DailyRollup, DailyRollupFlag
from butlers.chronicler.narration import (
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


def test_skips_when_no_rollup_rows() -> None:
    skip, reason = should_skip_narration([], [])
    assert skip is True
    assert reason == "no_rollup_data"


def test_skips_when_feeder_dark_flag_present() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=100)]
    flags = [DailyRollupFlag(local_date=_LOCAL_DATE, flag_type=FLAG_FEEDER_DARK)]
    skip, reason = should_skip_narration(rollups, flags)
    assert skip is True
    assert reason == "feeder_dark"


def test_does_not_skip_healthy_day_with_other_flags() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=0)]
    flags = [DailyRollupFlag(local_date=_LOCAL_DATE, flag_type=FLAG_SLEEP_MISSING)]
    skip, reason = should_skip_narration(rollups, flags)
    assert skip is False
    assert reason is None


def test_does_not_skip_healthy_day_with_no_flags() -> None:
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="work", seconds=500)]
    skip, reason = should_skip_narration(rollups, [])
    assert skip is False
    assert reason is None


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
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            layer="activity",
            title="short session",
            start=_DAY_START,
            end=_DAY_START.replace(hour=18),
        ),
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            layer="activity",
            title="long session",
            start=_DAY_START.replace(hour=19),
            end=_DAY_START.replace(hour=23),
        ),
        _episode(
            source_name="spotify.session_summary",
            episode_type="listening_episode",
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
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            layer="activity",
            title="",
            start=_DAY_START,
            end=_DAY_START.replace(hour=18),
        )
    ]
    result = select_top_episode_titles(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result == {}


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
            source_name="spotify.session_summary",
            episode_type="listening_episode",
            layer="activity",
            title="way before the window",
            start=outside_start,
            end=outside_end,
        )
    ]
    result = select_top_episode_titles(episodes, day_start_utc=_DAY_START, day_end_utc=_DAY_END)
    assert result == {}


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


def _patch_common(monkeypatch, *, rollups, flags, top_episodes=None) -> dict[str, Any]:
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
    return written


async def test_narrate_daily_rollup_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CHRONICLER_NARRATION_ENABLED", "false")
    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)
    assert result == {"local_date": "2026-07-05", "status": "disabled"}


async def test_narrate_daily_rollup_skips_no_rollup_data(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    _patch_common(monkeypatch, rollups=[], flags=[])

    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)

    assert result == {"local_date": "2026-07-05", "status": "skipped_no_rollup_data"}


async def test_narrate_daily_rollup_skips_feeder_dark(monkeypatch) -> None:
    monkeypatch.delenv("CHRONICLER_NARRATION_ENABLED", raising=False)
    rollups = [DailyRollup(local_date=_LOCAL_DATE, lane="sleep", seconds=0)]
    flags = [DailyRollupFlag(local_date=_LOCAL_DATE, flag_type=FLAG_FEEDER_DARK)]
    _patch_common(monkeypatch, rollups=rollups, flags=flags)

    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)

    assert result == {"local_date": "2026-07-05", "status": "skipped_feeder_dark"}


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

    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)

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

    result = await narrate_daily_rollup(AsyncMock(), local_date=_LOCAL_DATE)

    assert result["status"] == "labeled"
    assert result["flags_labeled"] == []
    assert written["flag_narratives"] == {}
    assert written["day_narrative"] == "Busy day."
