"""Tests for ``butlers.chronicler.editorial`` deterministic helpers.

DB-bound flows (``compose_briefing_payload``, ``_fetch_*``) are covered by
the briefing API integration tests. This file exercises the pure functions:
state classification, headline templates, templated voice paragraph,
day-window helper, and waking-gap detection.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

import butlers.chronicler.editorial as editorial
from butlers.chronicler.editorial import (
    AttentionItem,
    BriefingPayload,
    KpiSnapshot,
    LaneHours,
    Streaks,
    _compute_kpi,
    _compute_streaks,
    _detect_waking_gaps,
    _fetch_earliest_covered_date,
    _fetch_recent_days,
    _fetch_sleep_median_prior_week,
    _fetch_source_health_items,
    _is_local_day_covered,
    _target_is_recent,
    _utc_to_local_date,
    classify_state,
    day_window_utc,
    headline_for,
    record_coverage_witness,
    templated_voice_paragraph,
)

# ── classify_state ────────────────────────────────────────────────────────


def test_classify_state_quiet_when_no_items() -> None:
    assert classify_state([]) == "quiet"


def test_classify_state_urgent_on_any_high_severity() -> None:
    items = [AttentionItem(kind="anomaly", severity="high", title="x")]
    assert classify_state(items) == "urgent"


def test_classify_state_urgent_dominates_over_count() -> None:
    items = [
        AttentionItem(kind="anomaly", severity="high", title="x"),
        AttentionItem(kind="anomaly", severity="low", title="x"),
    ]
    assert classify_state(items) == "urgent"


def test_classify_state_busy_at_three_or_more() -> None:
    items = [AttentionItem(kind="anomaly", severity="low", title="x") for _ in range(3)]
    assert classify_state(items) == "busy"


def test_classify_state_mild_for_one_or_two() -> None:
    items = [AttentionItem(kind="anomaly", severity="low", title="x")]
    assert classify_state(items) == "mild"
    items.append(AttentionItem(kind="anomaly", severity="low", title="y"))
    assert classify_state(items) == "mild"


# ── headline_for ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "state,n,expected",
    [
        ("urgent", 1, "One thing needs attention."),
        ("urgent", 2, "2 things need attention."),
        ("urgent", 5, "5 things need attention."),
        ("busy", 3, "A full day, with 3 items waiting."),
        ("mild", 1, "Mostly quiet, with one note."),
        ("mild", 2, "Mostly quiet, with two notes."),
        ("quiet", 0, "Quiet day."),
    ],
)
def test_headline_templates(state: str, n: int, expected: str) -> None:
    assert headline_for(state, n) == expected


def test_headline_no_em_dashes_no_exclamation() -> None:
    """Voice rules: no em-dashes, no exclamation marks."""
    for state in ("urgent", "busy", "mild", "quiet"):
        for n in (0, 1, 2, 3, 5):
            text = headline_for(state, n)
            assert "—" not in text, f"em-dash leaked into {state}/{n}: {text!r}"
            assert "!" not in text, f"exclamation leaked into {state}/{n}: {text!r}"


# ── templated_voice_paragraph ────────────────────────────────────────────


def _payload(
    *,
    top_lanes: list[LaneHours] | None = None,
    sleep_minutes: int = 0,
    longest_gap: int = 0,
    attention: list[AttentionItem] | None = None,
) -> BriefingPayload:
    return BriefingPayload(
        state_class="quiet",
        headline="x",
        kpi=KpiSnapshot(
            hours_by_top_lanes=top_lanes or [],
            longest_episode_minutes=0,
            longest_episode_title=None,
            longest_gap_minutes=longest_gap,
            sleep_minutes=sleep_minutes,
            streaks=Streaks(),
        ),
        attention_items=attention or [],
        recent_days=[],
    )


def test_voice_paragraph_describes_top_lane() -> None:
    payload = _payload(top_lanes=[LaneHours(lane="conversations", hours=2.4)])
    text = templated_voice_paragraph(payload)
    assert "conversations" in text
    assert "2.4" in text


def test_voice_paragraph_mentions_two_lanes() -> None:
    payload = _payload(
        top_lanes=[
            LaneHours(lane="conversations", hours=2.4),
            LaneHours(lane="calendar", hours=1.1),
        ]
    )
    text = templated_voice_paragraph(payload)
    assert "conversations" in text
    assert "calendar" in text


def test_voice_paragraph_handles_empty_day() -> None:
    payload = _payload()
    text = templated_voice_paragraph(payload)
    assert "no projected episodes" in text
    assert "Nothing needs attention" in text


def test_voice_paragraph_includes_sleep_when_present() -> None:
    payload = _payload(sleep_minutes=432)
    text = templated_voice_paragraph(payload)
    assert "Sleep" in text
    assert "7h" in text


def test_voice_paragraph_no_em_dashes_no_please_no_exclamation() -> None:
    cases = [
        _payload(),
        _payload(top_lanes=[LaneHours("conversations", 1.0)]),
        _payload(
            top_lanes=[LaneHours("a", 1.0), LaneHours("b", 0.5)],
            sleep_minutes=300,
            longest_gap=480,
        ),
    ]
    banned = ("—", "!", "please")
    for payload in cases:
        text = templated_voice_paragraph(payload)
        for token in banned:
            assert token not in text, f"banned token {token!r} in voice paragraph: {text!r}"


# ── day_window_utc ───────────────────────────────────────────────────────


def test_day_window_utc_singapore() -> None:
    start, end = day_window_utc(date(2026, 5, 8), "Asia/Singapore")
    # Singapore is UTC+8: 2026-05-08 00:00 SGT == 2026-05-07 16:00 UTC.
    assert start == datetime(2026, 5, 7, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 5, 8, 16, 0, tzinfo=UTC)


def test_day_window_utc_invalid_tz_falls_back_to_utc() -> None:
    start, end = day_window_utc(date(2026, 5, 8), "Not/A/Real/Zone")
    assert start == datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 5, 9, 0, 0, tzinfo=UTC)


def test_day_window_utc_uses_next_local_midnight_across_dst_start() -> None:
    start, end = day_window_utc(date(2026, 3, 8), "America/New_York")
    assert start == datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)


def test_day_window_utc_uses_next_local_midnight_across_dst_end() -> None:
    start, end = day_window_utc(date(2026, 11, 1), "America/New_York")
    assert start == datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)


# ── _detect_waking_gaps ──────────────────────────────────────────────────


class _FakeRow(dict):
    """Lightweight asyncpg.Record stand-in for unit tests."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)


def test_waking_gap_under_threshold_not_flagged() -> None:
    start = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
    end = datetime(2026, 5, 8, 23, 59, tzinfo=UTC)
    episodes = [
        _FakeRow(
            s_at=datetime(2026, 5, 8, 9, 0, tzinfo=UTC),
            e_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
        ),
        _FakeRow(
            s_at=datetime(2026, 5, 8, 13, 0, tzinfo=UTC),  # 3h gap
            e_at=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
        ),
    ]
    assert _detect_waking_gaps(episodes, start, end, "UTC") == []


def test_waking_gap_over_threshold_flagged() -> None:
    start = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
    end = datetime(2026, 5, 8, 23, 59, tzinfo=UTC)
    episodes = [
        _FakeRow(
            s_at=datetime(2026, 5, 8, 9, 0, tzinfo=UTC),
            e_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
        ),
        _FakeRow(
            s_at=datetime(2026, 5, 8, 17, 0, tzinfo=UTC),  # 7h gap
            e_at=datetime(2026, 5, 8, 18, 0, tzinfo=UTC),
        ),
    ]
    gaps = _detect_waking_gaps(episodes, start, end, "UTC")
    assert len(gaps) == 1
    assert 6 * 60 <= gaps[0] <= 8 * 60


def test_waking_gap_no_episodes() -> None:
    start = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
    end = datetime(2026, 5, 8, 23, 59, tzinfo=UTC)
    assert _detect_waking_gaps([], start, end, "UTC") == []


def test_waking_gap_overlapping_episodes_merged() -> None:
    start = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
    end = datetime(2026, 5, 8, 23, 59, tzinfo=UTC)
    # Two overlapping episodes that together span 9-12; gap to next is 6h.
    episodes = [
        _FakeRow(
            s_at=datetime(2026, 5, 8, 9, 0, tzinfo=UTC),
            e_at=datetime(2026, 5, 8, 11, 0, tzinfo=UTC),
        ),
        _FakeRow(
            s_at=datetime(2026, 5, 8, 10, 30, tzinfo=UTC),
            e_at=datetime(2026, 5, 8, 12, 0, tzinfo=UTC),
        ),
        _FakeRow(
            s_at=datetime(2026, 5, 8, 18, 0, tzinfo=UTC),
            e_at=datetime(2026, 5, 8, 19, 0, tzinfo=UTC),
        ),
    ]
    gaps = _detect_waking_gaps(episodes, start, end, "UTC")
    assert len(gaps) == 1


def test_waking_gap_clips_to_waking_hours_before_threshold() -> None:
    start = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
    end = datetime(2026, 5, 8, 23, 59, tzinfo=UTC)
    episodes = [
        _FakeRow(
            s_at=datetime(2026, 5, 8, 4, 0, tzinfo=UTC),
            e_at=datetime(2026, 5, 8, 5, 0, tzinfo=UTC),
        ),
        _FakeRow(
            s_at=datetime(2026, 5, 8, 13, 0, tzinfo=UTC),
            e_at=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
        ),
    ]

    assert _detect_waking_gaps(episodes, start, end, "UTC") == [7 * 60]


def test_waking_gap_ignores_long_gap_with_short_waking_overlap() -> None:
    start = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
    end = datetime(2026, 5, 9, 23, 59, tzinfo=UTC)
    episodes = [
        _FakeRow(
            s_at=datetime(2026, 5, 8, 21, 0, tzinfo=UTC),
            e_at=datetime(2026, 5, 8, 23, 0, tzinfo=UTC),
        ),
        _FakeRow(
            s_at=datetime(2026, 5, 9, 8, 0, tzinfo=UTC),
            e_at=datetime(2026, 5, 9, 9, 0, tzinfo=UTC),
        ),
    ]

    assert _detect_waking_gaps(episodes, start, end, "UTC") == []


# ── Batched editorial reads ───────────────────────────────────────────────


class _FetchConn:
    def __init__(self, rows: list[_FakeRow] | None = None) -> None:
        self.rows = rows or []
        self.fetch_calls: list[tuple[object, ...]] = []
        self.fetchval_calls: list[tuple[object, ...]] = []
        self.execute_calls: list[tuple[object, ...]] = []

    async def fetch(self, *args: object) -> list[_FakeRow]:
        self.fetch_calls.append(args)
        return self.rows

    async def fetchval(self, *args: object) -> object:
        self.fetchval_calls.append(args)
        return False

    async def execute(self, sql: str, *args: object) -> str:
        self.execute_calls.append(args)
        return "INSERT 0 1"


class _FetchAcquire:
    def __init__(self, conn: _FetchConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _FetchConn:
        return self.conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _FetchPool:
    def __init__(self, conn: _FetchConn) -> None:
        self.conn = conn

    def acquire(self) -> _FetchAcquire:
        return _FetchAcquire(self.conn)


@pytest.mark.asyncio
async def test_fetch_recent_days_batches_episode_query() -> None:
    conn = _FetchConn()

    days = await _fetch_recent_days(
        _FetchPool(conn),
        datetime(2026, 5, 9, 0, 0, tzinfo=UTC),
        days=7,
        tz_name="UTC",
    )

    assert [d.date for d in days] == [
        "2026-05-08",
        "2026-05-07",
        "2026-05-06",
        "2026-05-05",
        "2026-05-04",
        "2026-05-03",
        "2026-05-02",
    ]
    assert len(conn.fetch_calls) == 1


# ── _fetch_recent_days top_lane (bu-m4e3d: intent-leak regression) ─────────
#
# Same counting seam as _compute_kpi (bu-whhll.1 / PR #2918): top_lane must be
# picked from activity-layer episodes only (lane_for_activity drops intent/
# calendar and evidence rows), with overlapping same-lane intervals unioned
# via union_seconds rather than summed. total_minutes/episode_count are
# unaffected (they still cover every episode touching the day).


@pytest.mark.asyncio
async def test_fetch_recent_days_all_day_calendar_block_never_becomes_top_lane() -> None:
    """An all-day intent-layer (calendar) episode must not win top_lane."""
    conn = _FetchConn(
        rows=[
            _FakeRow(
                s_at=datetime(2026, 5, 8, 0, 0, tzinfo=UTC),
                e_at=datetime(2026, 5, 9, 0, 0, tzinfo=UTC),
                source_name="google_calendar.completed",
                episode_type="scheduled_block",
                layer="intent",
                trigger_source=None,
            ),
        ]
    )

    days = await _fetch_recent_days(
        _FetchPool(conn),
        datetime(2026, 5, 9, 0, 0, tzinfo=UTC),
        days=1,
        tz_name="UTC",
    )

    assert len(days) == 1
    assert days[0].top_lane is None
    assert days[0].episode_count == 1
    assert days[0].total_minutes == 24 * 60


@pytest.mark.asyncio
async def test_fetch_recent_days_overlapping_same_lane_episodes_counted_once() -> None:
    """Overlapping same-lane episodes must union, not sum, when ranking lanes.

    Naive summation would let the 'work' lane's overlapping episodes (2h + 3h
    raw = 5h) outrank the single non-overlapping 'exercise' episode (4.5h),
    even though the *unioned* work total is only 4h (09:00-13:00 counted
    once). Correct behaviour: 'exercise' (4.5h) wins top_lane.
    """
    conn = _FetchConn(
        rows=[
            _FakeRow(
                s_at=datetime(2026, 5, 8, 9, 0, tzinfo=UTC),
                e_at=datetime(2026, 5, 8, 11, 0, tzinfo=UTC),
                source_name="chronicler.focus_inferred",
                episode_type="focus_block",
                layer="activity",
                trigger_source=None,
            ),
            _FakeRow(
                s_at=datetime(2026, 5, 8, 10, 0, tzinfo=UTC),
                e_at=datetime(2026, 5, 8, 13, 0, tzinfo=UTC),
                source_name="core.sessions",
                episode_type="work",
                layer="activity",
                trigger_source="trigger",
            ),
            _FakeRow(
                s_at=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
                e_at=datetime(2026, 5, 8, 18, 30, tzinfo=UTC),
                source_name="google_health.measurements",
                episode_type="workout_episode",
                layer="activity",
                trigger_source=None,
            ),
        ]
    )

    days = await _fetch_recent_days(
        _FetchPool(conn),
        datetime(2026, 5, 9, 0, 0, tzinfo=UTC),
        days=1,
        tz_name="UTC",
    )

    assert len(days) == 1
    assert days[0].top_lane == "exercise"
    assert days[0].episode_count == 3


@pytest.mark.asyncio
async def test_compute_streaks_batches_presence_query() -> None:
    conn = _FetchConn()

    streaks = await _compute_streaks(
        _FetchPool(conn),
        datetime(2026, 5, 9, 0, 0, tzinfo=UTC),
        "UTC",
    )

    assert streaks == Streaks(sleep=0, exercise=0)
    assert len(conn.fetch_calls) == 1
    assert conn.fetchval_calls == []


@pytest.mark.asyncio
async def test_fetch_sleep_median_prior_week_batches_day_query() -> None:
    conn = _FetchConn()

    median = await _fetch_sleep_median_prior_week(
        _FetchPool(conn),
        datetime(2026, 5, 9, 0, 0, tzinfo=UTC),
        "UTC",
    )

    assert median == 0
    assert len(conn.fetch_calls) == 1
    assert conn.fetchval_calls == []


@pytest.mark.asyncio
async def test_source_health_items_link_to_connectors_tab() -> None:
    conn = _FetchConn(
        [
            _FakeRow(
                source_name="spotify.session_summary",
                active=True,
                inactive_reason=None,
                last_run_at=datetime.now(UTC),
                last_error="oauth failed",
            )
        ]
    )

    items = await _fetch_source_health_items(_FetchPool(conn))

    assert items[0].kind == "source_health"
    assert items[0].action_href == "/ingestion?tab=connectors"


# ── Date-scoped source health + earliest_date (bu archive nav) ─────────────


class _ValConn(_FetchConn):
    """Like ``_FetchConn`` but ``fetchval`` returns a configured value."""

    def __init__(self, rows: list[_FakeRow] | None = None, fetchval_result: object = None) -> None:
        super().__init__(rows)
        self._fetchval_result = fetchval_result

    async def fetchval(self, *args: object) -> object:
        self.fetchval_calls.append(args)
        return self._fetchval_result


def test_utc_to_local_date_uses_owner_tz() -> None:
    dt = datetime(2026, 1, 1, 18, 0, tzinfo=UTC)  # 02:00 next day in SGT (UTC+8)
    assert _utc_to_local_date(dt, "Asia/Singapore") == date(2026, 1, 2)
    assert _utc_to_local_date(dt, "UTC") == date(2026, 1, 1)


def test_target_is_recent_includes_yesterday_and_today_only() -> None:
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)  # today (UTC) is 2026-05-09
    assert _target_is_recent(date(2026, 5, 9), "UTC", now) is True  # today
    assert _target_is_recent(date(2026, 5, 8), "UTC", now) is True  # yesterday
    assert _target_is_recent(date(2026, 5, 7), "UTC", now) is False  # older
    assert _target_is_recent(date(2026, 5, 1), "UTC", now) is False


@pytest.mark.asyncio
async def test_fetch_earliest_covered_date_returns_min_local_date() -> None:
    conn = _ValConn(fetchval_result=date(2026, 1, 2))
    earliest = await _fetch_earliest_covered_date(_FetchPool(conn), "Asia/Singapore")
    assert earliest == date(2026, 1, 2)


@pytest.mark.asyncio
async def test_fetch_earliest_covered_date_is_none_when_empty() -> None:
    conn = _ValConn(fetchval_result=None)
    assert await _fetch_earliest_covered_date(_FetchPool(conn), "UTC") is None


@pytest.mark.asyncio
async def test_is_local_day_covered_true_when_witness_exists() -> None:
    conn = _ValConn(fetchval_result=1)
    assert await _is_local_day_covered(_FetchPool(conn), date(2026, 1, 2), "UTC") is True


@pytest.mark.asyncio
async def test_is_local_day_covered_false_when_no_witness() -> None:
    conn = _ValConn(fetchval_result=None)
    assert await _is_local_day_covered(_FetchPool(conn), date(2026, 1, 2), "UTC") is False


@pytest.mark.asyncio
async def test_record_coverage_witness_executes_upsert() -> None:
    conn = _FetchConn()
    await record_coverage_witness(_FetchPool(conn), date(2026, 1, 2), "UTC")
    assert conn.execute_calls == [(date(2026, 1, 2), "UTC")]


@pytest.mark.asyncio
async def test_compose_excludes_source_health_for_old_archive_date(monkeypatch) -> None:
    async def _fake_health(pool: object, *, now: object = None) -> list[AttentionItem]:
        return [AttentionItem(kind="source_health", severity="high", title="spotify down")]

    async def _fake_earliest(pool: object, tz_name: str) -> date:
        return date(2026, 1, 1)

    async def _fake_covered(pool: object, local_date: date, tz_name: str) -> bool:
        return True

    monkeypatch.setattr(editorial, "_fetch_source_health_items", _fake_health)
    monkeypatch.setattr(editorial, "_fetch_earliest_covered_date", _fake_earliest)
    monkeypatch.setattr(editorial, "_is_local_day_covered", _fake_covered)
    pool = _FetchPool(_FetchConn())
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)

    old = await editorial.compose_briefing_payload(pool, date(2026, 5, 6), "UTC", now=now)
    assert all(i.kind != "source_health" for i in old.attention_items)
    assert old.state_class == "quiet"
    assert old.earliest_date == "2026-01-01"
    assert old.covered_and_available is True

    recent = await editorial.compose_briefing_payload(pool, date(2026, 5, 8), "UTC", now=now)
    assert any(i.kind == "source_health" for i in recent.attention_items)
    assert recent.state_class == "urgent"


# ── Coverage/availability precedence (bu-ep4ks.1) ───────────────────────────


@pytest.mark.asyncio
async def test_compose_no_data_when_target_before_coverage_floor(monkeypatch) -> None:
    async def _fake_earliest(pool: object, tz_name: str) -> date:
        return date(2026, 5, 1)

    monkeypatch.setattr(editorial, "_fetch_earliest_covered_date", _fake_earliest)
    pool = _FetchPool(_FetchConn())
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)

    payload = await editorial.compose_briefing_payload(pool, date(2026, 4, 20), "UTC", now=now)

    assert payload.state_class == "no_data"
    assert payload.covered_and_available is False
    assert payload.earliest_date == "2026-05-01"
    assert payload.attention_items == []
    assert payload.headline == headline_for("no_data", 0)


@pytest.mark.asyncio
async def test_compose_unavailable_when_no_coverage_floor_established(monkeypatch) -> None:
    async def _fake_earliest(pool: object, tz_name: str) -> None:
        return None

    monkeypatch.setattr(editorial, "_fetch_earliest_covered_date", _fake_earliest)
    pool = _FetchPool(_FetchConn())
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)

    payload = await editorial.compose_briefing_payload(pool, date(2026, 5, 6), "UTC", now=now)

    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False
    assert payload.earliest_date is None


@pytest.mark.asyncio
async def test_compose_unavailable_on_coverage_evidence_gap(monkeypatch) -> None:
    """target is on/after the floor but has no exact-date witness of its own."""

    async def _fake_earliest(pool: object, tz_name: str) -> date:
        return date(2026, 5, 1)

    async def _fake_covered(pool: object, local_date: date, tz_name: str) -> bool:
        return False

    monkeypatch.setattr(editorial, "_fetch_earliest_covered_date", _fake_earliest)
    monkeypatch.setattr(editorial, "_is_local_day_covered", _fake_covered)
    pool = _FetchPool(_FetchConn())
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)

    payload = await editorial.compose_briefing_payload(pool, date(2026, 5, 6), "UTC", now=now)

    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False


@pytest.mark.asyncio
async def test_compose_unavailable_on_owned_query_failure(monkeypatch) -> None:
    async def _raise(pool: object, tz_name: str) -> date:
        raise editorial.asyncpg.PostgresError("connection lost")

    monkeypatch.setattr(editorial, "_fetch_earliest_covered_date", _raise)
    pool = _FetchPool(_FetchConn())
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)

    payload = await editorial.compose_briefing_payload(pool, date(2026, 5, 6), "UTC", now=now)

    assert payload.state_class == "unavailable"
    assert payload.covered_and_available is False


@pytest.mark.asyncio
async def test_compose_availability_input_overrides_coverage(monkeypatch) -> None:
    """An externally-supplied availability signal takes precedence over a
    positive coverage verdict (design decision 3: never no_data/quiet)."""

    async def _fake_earliest(pool: object, tz_name: str) -> date:
        return date(2026, 5, 1)

    async def _fake_covered(pool: object, local_date: date, tz_name: str) -> bool:
        return True

    monkeypatch.setattr(editorial, "_fetch_earliest_covered_date", _fake_earliest)
    monkeypatch.setattr(editorial, "_is_local_day_covered", _fake_covered)
    pool = _FetchPool(_FetchConn())
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)

    degraded = await editorial.compose_briefing_payload(
        pool, date(2026, 5, 6), "UTC", now=now, availability="degraded"
    )
    assert degraded.state_class == "degraded"
    assert degraded.covered_and_available is False

    unavailable = await editorial.compose_briefing_payload(
        pool, date(2026, 5, 6), "UTC", now=now, availability="unavailable"
    )
    assert unavailable.state_class == "unavailable"


@pytest.mark.asyncio
async def test_compose_today_and_future_skip_coverage_gating(monkeypatch) -> None:
    """Today/future dates keep the prior unconditional classification: no
    coverage witness is recorded for a day that has not closed yet."""

    async def _fake_earliest(pool: object, tz_name: str) -> date:
        return date(2026, 5, 1)

    called = {"covered": False}

    async def _fake_covered(pool: object, local_date: date, tz_name: str) -> bool:
        called["covered"] = True
        return False

    monkeypatch.setattr(editorial, "_fetch_earliest_covered_date", _fake_earliest)
    monkeypatch.setattr(editorial, "_is_local_day_covered", _fake_covered)
    pool = _FetchPool(_FetchConn())
    now = datetime(2026, 5, 9, 12, 0, tzinfo=UTC)

    payload = await editorial.compose_briefing_payload(pool, date(2026, 5, 9), "UTC", now=now)

    assert payload.state_class == "quiet"
    assert payload.covered_and_available is True
    assert called["covered"] is False


# ── _compute_kpi (bu-whhll.1: intent-leak regression) ───────────────────────
#
# The aggregate endpoint (roster/chronicler/api/router.py) only counts
# `activity`-layer episodes (via `lane_for_activity`) and unions overlapping
# same-lane intervals (`union_seconds`) instead of summing them. `_compute_kpi`
# must apply the same rules or an all-day calendar block (intent layer, e.g.
# "Singapore Armed Forces Day") leaks into the "other" KPI lane, and
# overlapping same-lane episodes double-count.


def test_compute_kpi_all_day_calendar_block_contributes_zero() -> None:
    """An intent-layer (calendar) episode must not appear in any KPI lane."""
    start = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)
    end = datetime(2026, 7, 3, 0, 0, tzinfo=UTC)
    episodes = [
        _FakeRow(
            title="Singapore Armed Forces Day",
            s_at=start,
            e_at=end,
            source_name="google_calendar.completed",
            episode_type="scheduled_block",
            layer="intent",
            trigger_source=None,
        ),
    ]

    kpi = _compute_kpi(episodes, start, end, sleep_minutes=0, streaks=Streaks(), waking_gaps=[])

    assert kpi.hours_by_top_lanes == []
    assert kpi.longest_episode_minutes == 0
    assert kpi.longest_episode_title is None


def test_compute_kpi_overlapping_same_lane_episodes_counted_once() -> None:
    """Two overlapping activity-layer episodes in the same lane union, not sum."""
    start = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)
    end = datetime(2026, 7, 3, 0, 0, tzinfo=UTC)
    episodes = [
        # Two overlapping owner-Work-lane episodes (bu-whhll.14: focus + screen
        # both fold into 'occupation' -> Work), 09:00-11:00 and 10:00-13:00:
        # overlap union is 09:00-13:00 == 4h, not the naive 5h sum.
        _FakeRow(
            title="Focus block",
            s_at=datetime(2026, 7, 2, 9, 0, tzinfo=UTC),
            e_at=datetime(2026, 7, 2, 11, 0, tzinfo=UTC),
            source_name="chronicler.focus_inferred",
            episode_type="focus_block",
            layer="activity",
            trigger_source=None,
        ),
        _FakeRow(
            title="Scheduled work",
            s_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
            e_at=datetime(2026, 7, 2, 13, 0, tzinfo=UTC),
            source_name="activitywatch.window",
            episode_type="screen_episode",
            layer="activity",
            trigger_source=None,
        ),
    ]

    kpi = _compute_kpi(episodes, start, end, sleep_minutes=0, streaks=Streaks(), waking_gaps=[])

    assert kpi.hours_by_top_lanes == [LaneHours(lane="work", hours=4.0)]
    # Longest single episode remains per-row (3h), not the unioned lane total.
    assert kpi.longest_episode_minutes == 180
    assert kpi.longest_episode_title == "Scheduled work"


def test_compute_kpi_evidence_layer_episode_excluded() -> None:
    """Raw evidence-layer rows (e.g. GPS points) never count toward a lane."""
    start = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)
    end = datetime(2026, 7, 3, 0, 0, tzinfo=UTC)
    episodes = [
        _FakeRow(
            title="GPS ping",
            s_at=datetime(2026, 7, 2, 9, 0, tzinfo=UTC),
            e_at=datetime(2026, 7, 2, 9, 5, tzinfo=UTC),
            source_name="owntracks.points",
            episode_type="movement_episode",
            layer="evidence",
            trigger_source=None,
        ),
    ]

    kpi = _compute_kpi(episodes, start, end, sleep_minutes=0, streaks=Streaks(), waking_gaps=[])

    assert kpi.hours_by_top_lanes == []
    assert kpi.longest_episode_minutes == 0


def test_compute_kpi_activity_layer_episode_counts() -> None:
    """Sanity check: a normal activity-layer episode still counts as before."""
    start = datetime(2026, 7, 2, 0, 0, tzinfo=UTC)
    end = datetime(2026, 7, 3, 0, 0, tzinfo=UTC)
    episodes = [
        _FakeRow(
            title="Deep sleep",
            s_at=datetime(2026, 7, 2, 1, 0, tzinfo=UTC),
            e_at=datetime(2026, 7, 2, 8, 0, tzinfo=UTC),
            source_name="google_health.measurements",
            episode_type="sleep_episode",
            layer="activity",
            trigger_source=None,
        ),
    ]

    kpi = _compute_kpi(episodes, start, end, sleep_minutes=420, streaks=Streaks(), waking_gaps=[])

    assert kpi.hours_by_top_lanes == [LaneHours(lane="sleep", hours=7.0)]
    assert kpi.longest_episode_minutes == 420
    assert kpi.longest_episode_title == "Deep sleep"
