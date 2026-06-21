"""Editorial briefing computation for the Chronicles dashboard.

Pure-deterministic helpers that compute the briefing payload (attention
items, KPI snapshot, recent-days index, headline, and templated voice
paragraph) for a single day window. Reads only from the chronicler
schema. NEVER invokes an LLM. The voice paragraph that this module
produces is the templated fallback; the live ``llm·cached`` path reads
from ``chronicler.tier2_cache`` and is wired in the API router itself.

Voice rules (per ``about/heart-and-soul/design-language.md``):
- Sentence case.
- No exclamation marks.
- No em-dashes (use comma, colon, parentheses).
- No "please".
- Past tense for events, present tense for state.
"""

from __future__ import annotations

import asyncio
import zoneinfo
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

import asyncpg

# ── Tunables ────────────────────────────────────────────────────────────────

# Sleep anomaly threshold: today's sleep falls below this fraction of the
# 7-day median to be flagged.
SLEEP_ANOMALY_FRACTION = 0.7

# Waking-gap anomaly threshold (minutes).
WAKING_GAP_ANOMALY_MINUTES = 6 * 60

# Waking hours used for gap detection (owner-tz local time).
WAKING_HOUR_START = 6
WAKING_HOUR_END = 22

# Source-health: how recently a last_error counts as "live".
SOURCE_LAST_ERROR_WINDOW_HOURS = 24

# Sleep streak: a day "counts" toward the streak if any sleep_episode
# overlaps it. The day window is computed in owner-tz.
STREAK_LOOKBACK_DAYS = 30


# ── Public dataclass shapes ────────────────────────────────────────────────


@dataclass
class AttentionItem:
    kind: str
    severity: str
    title: str
    detail: str | None = None
    action_href: str | None = None


@dataclass
class LaneHours:
    lane: str
    hours: float


@dataclass
class Streaks:
    sleep: int = 0
    exercise: int = 0


@dataclass
class KpiSnapshot:
    hours_by_top_lanes: list[LaneHours]
    longest_episode_minutes: int
    longest_episode_title: str | None
    longest_gap_minutes: int
    sleep_minutes: int
    streaks: Streaks


@dataclass
class RecentDay:
    date: str
    total_minutes: int
    top_lane: str | None
    episode_count: int


@dataclass
class BriefingPayload:
    """Everything a briefing endpoint composes minus the voice paragraph."""

    state_class: str
    headline: str
    kpi: KpiSnapshot
    attention_items: list[AttentionItem]
    recent_days: list[RecentDay]
    earliest_date: str | None = None


# ── State classification ───────────────────────────────────────────────────


def classify_state(items: Sequence[AttentionItem]) -> str:
    """Deterministic state classification.

    `urgent` if any high-severity item; otherwise count buckets:
    `busy` for >= 3, `mild` for 1-2, `quiet` for 0.
    """
    if not items:
        return "quiet"
    if any(it.severity == "high" for it in items):
        return "urgent"
    if len(items) >= 3:
        return "busy"
    return "mild"


def headline_for(state_class: str, n_items: int) -> str:
    """Templated headline. Sentence case, no exclamation, no em-dash."""
    if state_class == "urgent":
        if n_items == 1:
            return "One thing needs attention."
        return f"{n_items} things need attention."
    if state_class == "busy":
        return f"A full day, with {n_items} items waiting."
    if state_class == "mild":
        return "Mostly quiet, with one note." if n_items == 1 else "Mostly quiet, with two notes."
    return "Quiet day."


def templated_voice_paragraph(payload: BriefingPayload) -> str:
    """Produce a deterministic Voice paragraph when no LLM cache exists.

    Past tense for events; present tense for state. Sentence case. No
    em-dashes, no exclamation, no "please".
    """
    parts: list[str] = []
    top = payload.kpi.hours_by_top_lanes[:2]
    if top:
        if len(top) == 1:
            parts.append(f"The day was led by {top[0].lane} at {top[0].hours:.1f} hours.")
        else:
            parts.append(
                f"The day was led by {top[0].lane} ({top[0].hours:.1f}h) and "
                f"{top[1].lane} ({top[1].hours:.1f}h)."
            )
    else:
        parts.append("The day produced no projected episodes.")

    if payload.kpi.sleep_minutes > 0:
        h = payload.kpi.sleep_minutes // 60
        m = payload.kpi.sleep_minutes % 60
        parts.append(f"Sleep was logged at {h}h {m:02d}m.")

    if payload.kpi.longest_gap_minutes >= WAKING_GAP_ANOMALY_MINUTES:
        gap_h = payload.kpi.longest_gap_minutes // 60
        parts.append(f"The longest waking gap reached {gap_h} hours.")

    if not payload.attention_items:
        parts.append("Nothing needs attention.")
    return " ".join(parts).strip()


# ── Time-window helpers ────────────────────────────────────────────────────


def day_window_utc(target: date, tz_name: str) -> tuple[datetime, datetime]:
    """Return [start, end) in UTC for ``target`` interpreted in ``tz_name``."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    start_local = datetime.combine(target, time.min, tzinfo=tz)
    end_local = datetime.combine(target + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _utc_to_local_date(dt: datetime, tz_name: str) -> date:
    """Return the owner-tz calendar date for a UTC instant."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    return dt.astimezone(tz).date()


def _target_is_recent(target: date, tz_name: str, now: datetime) -> bool:
    """True when ``target`` is the most recent settled day (yesterday) or today.

    Source-health attention reflects live connector state ("a source is
    erroring now"), so it is only meaningful for the freshest reconstructable
    day. For older archive dates it would surface today's connector state as if
    it belonged to that day, so callers exclude it.
    """
    today_local = _utc_to_local_date(now, tz_name)
    return target >= today_local - timedelta(days=1)


# ── Database queries (single round-trip per concern) ───────────────────────


_LANE_TOTALS_SQL = """
WITH winsel AS (
    SELECT
        e.id,
        e.title,
        COALESCE(o.corrected_start_at, e.start_at) AS s_at,
        COALESCE(o.corrected_end_at, e.end_at)     AS e_at,
        e.source_name,
        e.episode_type,
        e.payload->>'trigger_source' AS trigger_source
    FROM episodes e
    LEFT JOIN LATERAL (
        SELECT corrected_start_at, corrected_end_at, corrected_tombstone_at
        FROM overrides
        WHERE target_kind = 'episode'
          AND target_id   = e.id
        ORDER BY created_at DESC
        LIMIT 1
    ) o ON TRUE
    WHERE e.tombstone_at IS NULL
      AND COALESCE(o.corrected_tombstone_at, NULL) IS NULL
      AND COALESCE(o.corrected_start_at, e.start_at) < $2
      AND (
          COALESCE(o.corrected_end_at, e.end_at) IS NULL
          OR COALESCE(o.corrected_end_at, e.end_at) > $1
      )
)
SELECT
    id,
    title,
    s_at,
    e_at,
    source_name,
    episode_type,
    trigger_source
FROM winsel
ORDER BY s_at
"""


async def _fetch_window_episodes(
    pool: asyncpg.Pool, start_utc: datetime, end_utc: datetime
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return list(await conn.fetch(_LANE_TOTALS_SQL, start_utc, end_utc))


async def _fetch_sleep_minutes_for_day(
    pool: asyncpg.Pool, start_utc: datetime, end_utc: datetime
) -> int:
    sql = """
    SELECT COALESCE(SUM(
        EXTRACT(EPOCH FROM (
            LEAST(COALESCE(end_at, $2), $2)
            - GREATEST(start_at, $1)
        ))
    ), 0)::bigint AS seconds
    FROM episodes
    WHERE tombstone_at IS NULL
      AND source_name = 'google_health.measurements'
      AND episode_type = 'sleep_episode'
      AND start_at < $2
      AND (end_at IS NULL OR end_at > $1)
    """
    async with pool.acquire() as conn:
        seconds = await conn.fetchval(sql, start_utc, end_utc) or 0
    return int(seconds) // 60


async def _fetch_recent_days(
    pool: asyncpg.Pool, end_utc: datetime, days: int, tz_name: str
) -> list[RecentDay]:
    """Return up to ``days`` recent calendar days ending at ``end_utc``."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    end_local_date = end_utc.astimezone(tz).date()
    local_dates = [
        end_local_date - timedelta(days=offset + 1) for offset in range(days)
    ]  # skip "today"; recent_days = prior days
    if not local_dates:
        return []

    range_start_utc, _ = day_window_utc(local_dates[-1], tz_name)
    _, range_end_utc = day_window_utc(local_dates[0], tz_name)
    episodes = await _fetch_window_episodes(pool, range_start_utc, range_end_utc)

    from butlers.chronicler.aggregations import category_for

    out: list[RecentDay] = []
    for d in local_dates:
        d_start_utc, d_end_utc = day_window_utc(d, tz_name)
        day_episodes = [
            r
            for r in episodes
            if r["s_at"] < d_end_utc and (r["e_at"] is None or r["e_at"] > d_start_utc)
        ]
        if not day_episodes:
            out.append(
                RecentDay(date=d.isoformat(), total_minutes=0, top_lane=None, episode_count=0)
            )
            continue
        # Compute totals per lane and overall episode count.
        lane_seconds: dict[str, float] = {}
        total_seconds = 0.0
        for r in day_episodes:
            cat = category_for(
                r["source_name"],
                r["episode_type"],
                trigger_source=r["trigger_source"],
            )
            s_at = r["s_at"]
            e_at = r["e_at"] or d_end_utc
            clipped_start = max(s_at, d_start_utc)
            clipped_end = min(e_at, d_end_utc)
            secs = max(0.0, (clipped_end - clipped_start).total_seconds())
            lane_seconds[cat] = lane_seconds.get(cat, 0.0) + secs
            total_seconds += secs
        top_lane = max(lane_seconds.items(), key=lambda kv: kv[1])[0] if lane_seconds else None
        out.append(
            RecentDay(
                date=d.isoformat(),
                total_minutes=int(total_seconds // 60),
                top_lane=top_lane,
                episode_count=len(day_episodes),
            )
        )
    return out


async def _fetch_health_episode_day_seconds(
    pool: asyncpg.Pool,
    end_utc: datetime,
    days: int,
    tz_name: str,
    episode_types: Sequence[str],
) -> dict[tuple[date, str], float]:
    """Return seconds per local day and health episode type in one DB query."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    end_local_date = end_utc.astimezone(tz).date()
    local_dates = [end_local_date - timedelta(days=offset + 1) for offset in range(days)]
    if not local_dates:
        return {}

    range_start_utc, _ = day_window_utc(local_dates[-1], tz_name)
    _, range_end_utc = day_window_utc(local_dates[0], tz_name)
    sql = """
    SELECT episode_type, start_at, end_at
    FROM episodes
    WHERE tombstone_at IS NULL
      AND source_name = 'google_health.measurements'
      AND episode_type = ANY($3::text[])
      AND start_at < $2
      AND (end_at IS NULL OR end_at > $1)
    """
    async with pool.acquire() as conn:
        rows = list(await conn.fetch(sql, range_start_utc, range_end_utc, list(episode_types)))

    seconds_by_day: dict[tuple[date, str], float] = {}
    for d in local_dates:
        d_start_utc, d_end_utc = day_window_utc(d, tz_name)
        for r in rows:
            episode_type = r["episode_type"]
            clipped_start = max(r["start_at"], d_start_utc)
            clipped_end = min(r["end_at"] or range_end_utc, d_end_utc)
            seconds = max(0.0, (clipped_end - clipped_start).total_seconds())
            if seconds > 0:
                key = (d, episode_type)
                seconds_by_day[key] = seconds_by_day.get(key, 0.0) + seconds
    return seconds_by_day


def _streaks_from_day_seconds(
    seconds_by_day: dict[tuple[date, str], float], end_utc: datetime, tz_name: str
) -> Streaks:
    """Derive sleep/workout streaks from a precomputed per-day seconds map.

    Pure: no I/O. The map must cover at least ``STREAK_LOOKBACK_DAYS`` days
    of ``sleep_episode`` and ``workout_episode`` totals (see
    ``_fetch_health_episode_day_seconds``).
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    today_local_date = end_utc.astimezone(tz).date()
    sleep_streak = 0
    workout_streak = 0
    sleep_broken = False
    workout_broken = False
    for offset in range(STREAK_LOOKBACK_DAYS):
        d = today_local_date - timedelta(days=offset + 1)
        if not sleep_broken:
            if seconds_by_day.get((d, "sleep_episode"), 0.0) > 0:
                sleep_streak += 1
            else:
                sleep_broken = True
        if not workout_broken:
            if seconds_by_day.get((d, "workout_episode"), 0.0) > 0:
                workout_streak += 1
            else:
                workout_broken = True
        if sleep_broken and workout_broken:
            break
    return Streaks(sleep=sleep_streak, exercise=workout_streak)


async def _compute_streaks(pool: asyncpg.Pool, end_utc: datetime, tz_name: str) -> Streaks:
    """Compute consecutive-day streaks for sleep and workout episodes."""
    seconds_by_day = await _fetch_health_episode_day_seconds(
        pool,
        end_utc,
        STREAK_LOOKBACK_DAYS,
        tz_name,
        ("sleep_episode", "workout_episode"),
    )
    return _streaks_from_day_seconds(seconds_by_day, end_utc, tz_name)


async def _fetch_source_health_items(
    pool: asyncpg.Pool, *, now: datetime | None = None
) -> list[AttentionItem]:
    """Read source-state and produce one attention item per degraded row.

    Source state is split across two tables in the chronicler schema:
    ``source_adapter_state`` (active flag + inactive_reason) and
    ``projection_checkpoints`` (last_run_at + last_error). We join them to
    produce one attention row per source where either the adapter is
    inactive or the most recent run errored within the cutoff window.

    ``now`` is injectable so the 24h ``last_error`` cutoff is deterministic
    under test; it defaults to the current instant.
    """
    items: list[AttentionItem] = []
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=SOURCE_LAST_ERROR_WINDOW_HOURS)
    sql = """
    SELECT
        sas.source_name,
        sas.active,
        sas.inactive_reason,
        cp.last_run_at,
        cp.last_error
    FROM source_adapter_state sas
    LEFT JOIN LATERAL (
        SELECT last_run_at, last_error
        FROM projection_checkpoints pc
        WHERE pc.source_name = sas.source_name
        ORDER BY pc.last_run_at DESC NULLS LAST
        LIMIT 1
    ) cp ON TRUE
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
        return items
    for r in rows:
        last_error = r["last_error"]
        last_run_at = r["last_run_at"]
        inactive_reason = r["inactive_reason"]
        if last_error and last_run_at and last_run_at >= cutoff:
            items.append(
                AttentionItem(
                    kind="source_health",
                    severity="high",
                    title=f"{r['source_name']} projection error",
                    detail=str(last_error)[:140],
                    action_href="/ingestion?tab=connectors",
                )
            )
        elif inactive_reason:
            items.append(
                AttentionItem(
                    kind="source_health",
                    severity="medium",
                    title=f"{r['source_name']} inactive",
                    detail=str(inactive_reason)[:140],
                    action_href="/ingestion?tab=connectors",
                )
            )
    return items


async def _fetch_open_corrections(
    pool: asyncpg.Pool, start_utc: datetime, end_utc: datetime
) -> int:
    sql = """
    SELECT COUNT(*) AS n
    FROM overrides o
    JOIN episodes e ON e.id = o.target_id AND o.target_kind = 'episode'
    WHERE o.corrected_tombstone_at IS NULL
      AND e.tombstone_at IS NULL
      AND e.start_at < $2
      AND (e.end_at IS NULL OR e.end_at > $1)
    """
    async with pool.acquire() as conn:
        return int(await conn.fetchval(sql, start_utc, end_utc) or 0)


async def _fetch_earliest_episode_date(pool: asyncpg.Pool, tz_name: str) -> str | None:
    """Return the earliest chronicled calendar day (owner-tz) as an ISO string.

    Bounds backward archive navigation so the date stepper cannot step before
    the first day with data. Returns ``None`` when no episodes exist.
    """
    sql = """
    SELECT MIN(start_at) AS min_start
    FROM episodes
    WHERE tombstone_at IS NULL
    """
    try:
        async with pool.acquire() as conn:
            min_start = await conn.fetchval(sql)
    except (asyncpg.UndefinedTableError, asyncpg.PostgresError):
        return None
    if min_start is None:
        return None
    return _utc_to_local_date(min_start, tz_name).isoformat()


# ── Anomaly detection helpers ──────────────────────────────────────────────


def _sleep_median_from_day_seconds(
    seconds_by_day: dict[tuple[date, str], float], end_utc: datetime, tz_name: str
) -> int:
    """Median sleep_minutes across the seven preceding days, from a seconds map.

    Pure: no I/O. The map must cover ``sleep_episode`` totals for the prior
    seven days (a 30-day streak fetch is a superset).
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    today_local = end_utc.astimezone(tz).date()
    minutes = [
        int(seconds_by_day.get((today_local - timedelta(days=offset), "sleep_episode"), 0) // 60)
        for offset in range(1, 8)
    ]
    s = sorted(minutes)
    return s[len(s) // 2]


async def _fetch_sleep_median_prior_week(
    pool: asyncpg.Pool, end_utc: datetime, tz_name: str
) -> int:
    """Return median sleep_minutes across the seven calendar days preceding."""
    seconds_by_day = await _fetch_health_episode_day_seconds(
        pool,
        end_utc,
        7,
        tz_name,
        ("sleep_episode",),
    )
    return _sleep_median_from_day_seconds(seconds_by_day, end_utc, tz_name)


def _waking_overlap_minutes(gap_start_utc: datetime, gap_end_utc: datetime, tz: tzinfo) -> int:
    """Return minutes of a UTC gap that overlap local waking windows."""
    if gap_start_utc >= gap_end_utc:
        return 0
    local_start = gap_start_utc.astimezone(tz)
    local_end = gap_end_utc.astimezone(tz)
    cursor = local_start.date()
    total_seconds = 0.0
    while cursor <= local_end.date():
        waking_start = datetime.combine(cursor, time(WAKING_HOUR_START), tzinfo=tz).astimezone(UTC)
        waking_end = datetime.combine(cursor, time(WAKING_HOUR_END), tzinfo=tz).astimezone(UTC)
        clipped_start = max(gap_start_utc, waking_start)
        clipped_end = min(gap_end_utc, waking_end)
        if clipped_start < clipped_end:
            total_seconds += (clipped_end - clipped_start).total_seconds()
        cursor += timedelta(days=1)
    return int(total_seconds // 60)


def _detect_waking_gaps(
    episodes: Iterable[asyncpg.Record],
    start_utc: datetime,
    end_utc: datetime,
    tz_name: str,
) -> list[int]:
    """Return list of gap minutes that exceed the waking-gap threshold."""
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    spans: list[tuple[datetime, datetime]] = []
    for r in episodes:
        s = r["s_at"]
        e = r["e_at"] or end_utc
        if s < e:
            spans.append((s, e))
    if not spans:
        return []
    spans.sort()
    # Merge overlapping spans.
    merged: list[tuple[datetime, datetime]] = [spans[0]]
    for s, e in spans[1:]:
        ls, le = merged[-1]
        if s <= le:
            merged[-1] = (ls, max(le, e))
        else:
            merged.append((s, e))
    flagged: list[int] = []
    for i in range(1, len(merged)):
        prev_end = merged[i - 1][1]
        curr_start = merged[i][0]
        waking_minutes = _waking_overlap_minutes(prev_end, curr_start, tz)
        if waking_minutes >= WAKING_GAP_ANOMALY_MINUTES:
            flagged.append(waking_minutes)
    return flagged


# ── KPI computation ───────────────────────────────────────────────────────


def _compute_kpi(
    episodes: Sequence[asyncpg.Record],
    start_utc: datetime,
    end_utc: datetime,
    sleep_minutes: int,
    streaks: Streaks,
    waking_gaps: Sequence[int],
) -> KpiSnapshot:
    from butlers.chronicler.aggregations import category_for

    lane_seconds: dict[str, float] = {}
    longest_minutes = 0
    longest_title: str | None = None
    for r in episodes:
        s = max(r["s_at"], start_utc)
        e = min(r["e_at"] or end_utc, end_utc)
        secs = max(0.0, (e - s).total_seconds())
        cat = category_for(
            r["source_name"],
            r["episode_type"],
            trigger_source=r["trigger_source"],
        )
        lane_seconds[cat] = lane_seconds.get(cat, 0.0) + secs
        mins = int(secs // 60)
        if mins > longest_minutes:
            longest_minutes = mins
            longest_title = r["title"]

    top = sorted(lane_seconds.items(), key=lambda kv: kv[1], reverse=True)[:3]
    longest_gap = max(waking_gaps) if waking_gaps else 0
    return KpiSnapshot(
        hours_by_top_lanes=[
            LaneHours(lane=lane, hours=round(secs / 3600.0, 1)) for lane, secs in top
        ],
        longest_episode_minutes=longest_minutes,
        longest_episode_title=longest_title,
        longest_gap_minutes=longest_gap,
        sleep_minutes=sleep_minutes,
        streaks=streaks,
    )


# ── Top-level orchestrator ────────────────────────────────────────────────


async def _no_attention_items() -> list[AttentionItem]:
    """Awaitable yielding no attention items (for conditional gather slots)."""
    return []


async def compose_briefing_payload(
    pool: asyncpg.Pool,
    target: date,
    tz_name: str,
    *,
    now: datetime | None = None,
) -> BriefingPayload:
    """Compose the full editorial briefing payload (without voice paragraph).

    Reads only from the chronicler schema. NEVER invokes an LLM. ``now`` is
    injectable so date-relative classification (source-health scoping) is
    deterministic under test; it defaults to the current instant.
    """
    now = now or datetime.now(UTC)
    start_utc, end_utc = day_window_utc(target, tz_name)

    # All briefing reads are independent (they share only the window/tz inputs),
    # so fan them out concurrently instead of awaiting serially. The 30-day
    # health seconds map is fetched once and feeds both streaks and the sleep
    # median (previously two separate 30-day / 7-day queries). Source health is
    # a live-now connector signal: only meaningful for the most recent settled
    # day, so skip the query entirely on older archive dates.
    target_is_recent = _target_is_recent(target, tz_name, now)
    source_health_coro = (
        _fetch_source_health_items(pool, now=now) if target_is_recent else _no_attention_items()
    )
    (
        episodes,
        sleep_minutes,
        health_seconds_by_day,
        open_corrections,
        recent_days,
        earliest_date,
        source_health_items,
    ) = await asyncio.gather(
        _fetch_window_episodes(pool, start_utc, end_utc),
        _fetch_sleep_minutes_for_day(pool, start_utc, end_utc),
        _fetch_health_episode_day_seconds(
            pool, end_utc, STREAK_LOOKBACK_DAYS, tz_name, ("sleep_episode", "workout_episode")
        ),
        _fetch_open_corrections(pool, start_utc, end_utc),
        _fetch_recent_days(pool, end_utc, days=7, tz_name=tz_name),
        _fetch_earliest_episode_date(pool, tz_name),
        source_health_coro,
    )

    streaks = _streaks_from_day_seconds(health_seconds_by_day, end_utc, tz_name)
    sleep_median = _sleep_median_from_day_seconds(health_seconds_by_day, end_utc, tz_name)
    waking_gaps = _detect_waking_gaps(episodes, start_utc, end_utc, tz_name)

    attention_items: list[AttentionItem] = []
    if (
        sleep_median > 0
        and sleep_minutes > 0
        and sleep_minutes < int(sleep_median * SLEEP_ANOMALY_FRACTION)
    ):
        attention_items.append(
            AttentionItem(
                kind="anomaly",
                severity="medium",
                title="Short sleep",
                detail=(
                    f"{sleep_minutes // 60}h {sleep_minutes % 60:02d}m, "
                    f"below 7-day median ({sleep_median // 60}h)"
                ),
            )
        )
    for gap_minutes in waking_gaps:
        gap_h = gap_minutes // 60
        attention_items.append(
            AttentionItem(
                kind="anomaly",
                severity="low",
                title="Long waking gap",
                detail=f"{gap_h} hours without a recorded episode",
            )
        )
    # Source health (live-now connector signal, recent days only) was fetched
    # concurrently above; on older archive dates the coroutine returns [].
    attention_items.extend(source_health_items)
    if open_corrections > 0:
        attention_items.append(
            AttentionItem(
                kind="open_correction",
                severity="low",
                title=(
                    "1 unresolved correction"
                    if open_corrections == 1
                    else f"{open_corrections} unresolved corrections"
                ),
            )
        )

    kpi = _compute_kpi(episodes, start_utc, end_utc, sleep_minutes, streaks, waking_gaps)
    state_class = classify_state(attention_items)
    headline = headline_for(state_class, len(attention_items))

    return BriefingPayload(
        state_class=state_class,
        headline=headline,
        kpi=kpi,
        attention_items=attention_items,
        recent_days=recent_days,
        earliest_date=earliest_date,
    )


__all__ = [
    "AttentionItem",
    "BriefingPayload",
    "KpiSnapshot",
    "LaneHours",
    "RecentDay",
    "STREAK_LOOKBACK_DAYS",
    "SLEEP_ANOMALY_FRACTION",
    "SOURCE_LAST_ERROR_WINDOW_HOURS",
    "Streaks",
    "WAKING_GAP_ANOMALY_MINUTES",
    "WAKING_HOUR_END",
    "WAKING_HOUR_START",
    "classify_state",
    "compose_briefing_payload",
    "day_window_utc",
    "headline_for",
    "templated_voice_paragraph",
]
