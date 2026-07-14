"""Chronicler aggregation helpers.

This module contains pure, deterministic functions used by aggregate endpoints
and episode list responses. NO I/O. NO LLM. NO side effects.

See design.md §D1 for the full category taxonomy contract.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, tzinfo

# ── Category taxonomy ──────────────────────────────────────────────────────

# Stable source-category strings. These are the per-source classification a
# raw episode carries; ``lane_for_category`` folds them into the life-balance
# Activity lanes the dashboard renders. The backend never emits colours.
#
# core.sessions episodes are split by trigger_source:
#   "conversations" — trigger_source='route'  (user→butler interactions)
#   "tasks"         — trigger_source IN {'trigger','external','dashboard'}
#                     or any other / NULL value (scheduled & daemon-fired work)
#
# Calendar is deliberately ABSENT: calendar projections are the *intent* layer
# (planned blocks), never counted as lived time, so they have no source
# category here (they resolve to "other" and are dropped by the layer filter).
CATEGORIES: frozenset[str] = frozenset(
    {
        "conversations",
        "tasks",
        "music",
        "gaming",
        "travel",
        "sleep",
        "meal",
        "home",
        "workout",
        "social",
        "occupation",
        "ambient",
        "other",
    }
)

# Static mapping: (source_name, episode_type) → source category.
# Mirrors the SUPPORTED source declarations in contracts.py.
# core.sessions is handled separately in category_for() via trigger_source.
# Anything not in this table and not handled by trigger_source → "other".
_CATEGORY_MAP: dict[tuple[str, str], str] = {
    ("spotify.session_summary", "listening_episode"): "music",
    ("steam.play_history", "play_episode"): "gaming",
    ("owntracks.points", "movement_episode"): "travel",
    # GPS place-cluster dwells (bu-ac2pg) fold into 'home' -> 'rest', same as
    # HA person-domain presence, regardless of the cluster's derived label
    # (home/work/place_unknown) — deliberately NEVER 'occupation'/'work' (see
    # design doc §6.2 lane-discipline note); a future explicit corroborator
    # wiring in occupation.py is a separate, coordinated change.
    ("owntracks.place_cluster", "place_episode"): "home",
    ("google_health.measurements", "sleep_episode"): "sleep",
    ("google_health.measurements", "workout_episode"): "workout",
    ("health.meals", "eating_event"): "meal",
    ("home_assistant.history", "presence_episode"): "home",
    # Inferred chronicler-derived OWNER focus/reading (bu-i29ix). These are the
    # owner's own productive focus, not butler-session work, so they fold into
    # the owner-work 'occupation' category (→ Work lane) alongside the
    # occupation adapter. They were parked in 'tasks' only as a stopgap "closest
    # existing category" before a dedicated occupation category existed; bu-whhll.14
    # graduates them so 'tasks' becomes butler-session-only (→ Butler ops lane).
    # payload.signal still carries the focus-vs-reading kind (no granularity lost;
    # they were already indistinguishable at the 'tasks' category level).
    ("chronicler.focus_inferred", "focus_block"): "occupation",
    ("chronicler.reading_inferred", "reading_block"): "occupation",
    # Inferred exercise from HR+GPS corroboration (bu-1sj3zn) folds into the
    # 'workout' category (→ Exercise lane), matching explicit workout episodes.
    ("chronicler.exercise_inferred", "exercise_episode"): "workout",
    # Comms message bursts (bu-jc6htw.1) fold into the 'social' category
    # (-> Social lane).
    ("comms.message_bursts", "social_episode"): "social",
    # ActivityWatch desktop-activity screen episodes (bu-whhll.6) are direct
    # measurement of the OWNER working, so they fold into the owner-work
    # 'occupation' category (-> Work lane). Originally parked in 'tasks' as the
    # "closest existing category" before an occupation category existed
    # (bu-whhll.6 comment); bu-whhll.14 moves them to 'occupation' so 'tasks'
    # holds butler-session work only (-> Butler ops lane).
    ("activitywatch.window", "screen_episode"): "occupation",
    # Occupation-block inference from enabled routine windows (bu-whhll.10,
    # epic bu-whhll Tier 2). The 'occupation' category is the OWNER-work lane
    # (-> Work lane); as of bu-whhll.14 it is the sole set of Work-lane sources
    # (occupation blocks + owner focus/reading/screen above), cleanly split from
    # the butler-session sources that now form the Butler ops lane.
    ("chronicler.occupation_inferred", "occupation_block"): "occupation",
    # HA non-person sensor-activity ambient motion (bu-49fqa, telemetry-
    # distillation bead 1). Deliberately its own 'ambient' category (-> Rest
    # lane), never folded into 'tasks'/'occupation' — this is what keeps
    # ambient HA sensor evidence from re-opening the work/occupation
    # lane-conflation problem bu-whhll.14 is fixing (design §1.5).
    ("home_assistant.sensor_activity", "room_activity_episode"): "ambient",
}

# trigger_source values that represent user→butler conversations.
# Everything else (including None) is classified as "tasks".
_CONVERSATION_TRIGGER_SOURCES: frozenset[str] = frozenset({"route"})


def category_for(
    source_name: str,
    episode_type: str,
    *,
    trigger_source: str | None = None,
) -> str:
    """Return the stable category string for an episode.

    For ``core.sessions`` work episodes the category is resolved from
    ``trigger_source``:
    - ``'route'`` → ``'conversations'``  (user→butler interactions)
    - any other value or ``None`` → ``'tasks'``  (scheduled / daemon work)

    For all other sources the ``(source_name, episode_type)`` pair is looked up
    in the static ``_CATEGORY_MAP``.

    Returns one of the values in ``CATEGORIES``. Unknown pairs → ``"other"``.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if source_name == "core.sessions" and episode_type == "work":
        if trigger_source in _CONVERSATION_TRIGGER_SOURCES:
            return "conversations"
        return "tasks"
    return _CATEGORY_MAP.get((source_name, episode_type), "other")


# ── Activity lane taxonomy (IEA, tasks.md §4) ───────────────────────────────

# Top-level life-balance lanes. The frontend LANE_TAXONOMY maps these to
# display labels/colours/icons. These — not data sources — are what every
# time/balance aggregate buckets by. ``music``/``gaming`` fold into Play;
# ``calendar`` is intent and never reaches a lane. See design.md §"Activity
# lane taxonomy".
LANES: frozenset[str] = frozenset(
    {
        "sleep",
        "exercise",
        "work",
        "butler_ops",
        "play",
        "social",
        "travel",
        "eat",
        "rest",
    }
)

# Source category → Activity lane. The left-hand side is a ``category_for``
# output. ``social`` is emitted by ``comms.message_bursts`` (tasks.md §6.2,
# bu-jc6htw.1); ``idle-presence`` remains forward-compat, pending the
# co-presence half of tasks.md §6. Categories with no lane (``other``, and
# the absent ``calendar``) resolve to ``None`` and are not counted.
_CATEGORY_TO_LANE: dict[str, str] = {
    # Butler LLM sessions (bu-whhll.14): the butlers' own conversations/tasks are
    # the Butler ops lane, NOT the owner's Work lane. Only 'occupation' (owner
    # occupation + focus/reading/screen) counts as Work.
    "conversations": "butler_ops",
    "tasks": "butler_ops",
    "music": "play",
    "gaming": "play",
    "meal": "eat",
    "home": "rest",
    "idle-presence": "rest",
    "workout": "exercise",
    "movement": "travel",
    "travel": "travel",
    "sleep": "sleep",
    "social": "social",
    "occupation": "work",
    "ambient": "rest",
}


def lane_for_category(category: str) -> str | None:
    """Map a source category onto a life-balance Activity lane.

    Returns one of ``LANES`` or ``None`` when the category has no lane (e.g.
    ``other`` or a calendar/intent category). Pure deterministic function.
    """
    return _CATEGORY_TO_LANE.get(category)


def sources_for_lane(lane: str) -> frozenset[str]:
    """Every ``source_name`` whose episodes can resolve into ``lane``.

    Derived once from ``_CATEGORY_MAP``/``_CATEGORY_TO_LANE`` — the same
    static tables ``category_for``/``lane_for_category`` already use — so
    there is no second source of truth to keep in sync by hand. Includes
    ``core.sessions`` for ``lane == "butler_ops"`` (bu-whhll.14), since that
    source resolves via ``category_for``'s ``trigger_source`` special-case
    (conversations/tasks) rather than an entry in ``_CATEGORY_MAP``.

    Used by the anomaly-flag rules (bu-v76a7) to decide which sources must
    be healthy before a lane-level behavioral flag (e.g.
    ``lane_share_outlier``) may fire — classify-before-flagging (design doc
    §2.5): a lane whose only contributing source is a known outage must not
    produce a fabricated behavioral verdict either way.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    sources = {
        source_name
        for (source_name, _episode_type), category in _CATEGORY_MAP.items()
        if _CATEGORY_TO_LANE.get(category) == lane
    }
    # core.sessions resolves to conversations/tasks via category_for's
    # trigger_source special-case (not _CATEGORY_MAP), and those categories are
    # the Butler ops lane as of bu-whhll.14 — so it is the (only) source that
    # feeds butler_ops, not work.
    if lane == "butler_ops":
        sources.add("core.sessions")
    return frozenset(sources)


def lane_for_activity(
    layer: str,
    source_name: str,
    episode_type: str,
    *,
    trigger_source: str | None = None,
) -> str | None:
    """Return the Activity lane an episode counts toward, or ``None``.

    This is the single counting seam (tasks.md §4): an episode is counted only
    when it is on the ``activity`` layer. ``intent`` (calendar) and ``evidence``
    (raw signals) layers return ``None`` — this is what drops an uncorroborated
    5 h calendar block to 0 s in every lane. An overlapping ``activity`` episode
    (e.g. a GPS-dwell projection) is the thing that actually counts; calendar is
    never attributed to a lane on its own.

    For ``activity``-layer rows the source category (see ``category_for``) is
    folded onto a lane via ``lane_for_category``; an activity row whose category
    has no lane (e.g. an unmapped source) also returns ``None``.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if str(layer) != "activity":
        return None
    return lane_for_category(category_for(source_name, episode_type, trigger_source=trigger_source))


# ── Duration aggregation helpers ───────────────────────────────────────────


def union_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total seconds covered by the union of half-open ``[start, end)`` intervals.

    Overlapping intervals are merged so two concurrent episodes within the same
    bucket are counted once rather than summed (which is what let a category
    exceed the window length). Returns ``0.0`` for an empty list.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if not intervals:
        return 0.0
    ordered = sorted(intervals, key=lambda iv: iv[0])
    total = 0.0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start > cur_end:
            total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    total += (cur_end - cur_start).total_seconds()
    return total


# ── Waking-window gap math ──────────────────────────────────────────────────
#
# Shared by editorial.py's waking-gap anomaly detector (sized to a single
# inter-episode gap; ``_waking_overlap_minutes`` there now delegates to
# ``waking_overlap_seconds`` below) and the aggregate/by-category
# ``untracked_seconds`` slice (bu-whhll.13, sized to a whole query window).
# Pure, deterministic, no I/O — callers own their "waking hour" tunables
# (see editorial.WAKING_HOUR_START/WAKING_HOUR_END) and pass them in
# explicitly so this module has no opinion about what "awake" means.


def _local_waking_windows_utc(
    window_start_utc: datetime,
    window_end_utc: datetime,
    tz: tzinfo,
    waking_hour_start: int,
    waking_hour_end: int,
) -> list[tuple[datetime, datetime]]:
    """Return the local waking-hour sub-intervals of ``[start, end)`` in UTC.

    One sub-interval per local calendar day the window touches, each clipped
    to the window itself — e.g. a 3-day window returns up to 3 disjoint
    ``[waking_hour_start, waking_hour_end)`` local-time spans (expressed in
    UTC), clipped to the window boundary on its first/last day.
    """
    if window_start_utc >= window_end_utc:
        return []
    local_start = window_start_utc.astimezone(tz)
    local_end = window_end_utc.astimezone(tz)
    cursor = local_start.date()
    windows: list[tuple[datetime, datetime]] = []
    while cursor <= local_end.date():
        waking_start = datetime.combine(cursor, time(waking_hour_start), tzinfo=tz).astimezone(UTC)
        waking_end = datetime.combine(cursor, time(waking_hour_end), tzinfo=tz).astimezone(UTC)
        clipped_start = max(window_start_utc, waking_start)
        clipped_end = min(window_end_utc, waking_end)
        if clipped_start < clipped_end:
            windows.append((clipped_start, clipped_end))
        cursor += timedelta(days=1)
    return windows


def waking_overlap_seconds(
    window_start_utc: datetime,
    window_end_utc: datetime,
    tz: tzinfo,
    *,
    waking_hour_start: int,
    waking_hour_end: int,
) -> float:
    """Return seconds of ``[window_start_utc, window_end_utc)`` that fall
    inside the local waking window on each calendar day the interval touches.

    Generalizes what began as a single-gap helper in editorial.py
    (``_waking_overlap_minutes``) to any UTC interval, including a whole
    aggregate query window — which is what ``untracked_seconds_for_window``
    below needs to size the "waking universe" a day's tracked/untracked split
    is measured against.
    """
    return union_seconds(
        _local_waking_windows_utc(
            window_start_utc, window_end_utc, tz, waking_hour_start, waking_hour_end
        )
    )


def untracked_seconds_for_window(
    activity_intervals: list[tuple[datetime, datetime]],
    window_start_utc: datetime,
    window_end_utc: datetime,
    tz: tzinfo,
    *,
    waking_hour_start: int,
    waking_hour_end: int,
) -> float:
    """Return waking-window seconds not covered by any activity interval.

    ``activity_intervals`` should be every ``activity``-layer episode's
    half-open span, already clipped to ``[window_start_utc, window_end_utc)``
    — pass every activity-layer episode regardless of whether its source
    category resolves to a lane (``lane_for_activity`` returning ``None`` for
    an unmapped source is a "we don't know how to bucket this" problem, not a
    "nothing happened" one, so it should not inflate untracked time). Sleep
    episodes are activity-layer too, so a nap inside the waking window is
    treated as tracked without special-casing "minus sleep": once every
    activity-layer span counts, there is nothing left to subtract.

    This is the pie-chart honesty fix (bu-whhll.13): the aggregate pie
    previously renormalised over tracked evidence only, so a 4h-evidence day
    rendered as a full (waking-window) day. Pure, deterministic, no I/O.
    """
    waking_windows = _local_waking_windows_utc(
        window_start_utc, window_end_utc, tz, waking_hour_start, waking_hour_end
    )
    waking_total = union_seconds(waking_windows)
    if waking_total <= 0.0:
        return 0.0
    clipped_activity: list[tuple[datetime, datetime]] = []
    for a_start, a_end in activity_intervals:
        for w_start, w_end in waking_windows:
            cs = max(a_start, w_start)
            ce = min(a_end, w_end)
            if cs < ce:
                clipped_activity.append((cs, ce))
    tracked = union_seconds(clipped_activity)
    return max(0.0, waking_total - tracked)


__all__ = [
    "CATEGORIES",
    "LANES",
    "category_for",
    "lane_for_activity",
    "lane_for_category",
    "sources_for_lane",
    "union_seconds",
    "untracked_seconds_for_window",
    "waking_overlap_seconds",
]
