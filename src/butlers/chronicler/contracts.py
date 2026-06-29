"""Source compatibility contract registry for Chronicler adapters.

Per RFC 0014 §D2 every source adapter MUST declare its
``chronicler_compatibility`` before projection runs against it. This
module defines the initial declarations baked into the Chronicler boot
path, plus helpers that seed the runtime table and enforce the
lint/check that future timestamped OpenSpec source specs declare
compatibility (or explicitly mark themselves ``not_time_bearing``).

The baked-in declarations are the authoritative initial state. Adapters
may refine ``active``/``inactive_reason`` at runtime; they MUST NOT
overwrite the compatibility label at runtime.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

import asyncpg

from butlers.chronicler.models import Compatibility, SourceAdapterState
from butlers.chronicler.storage import register_source

# ── Initial source declarations (RFC 0014 §D2) ─────────────────────────────

INITIAL_SOURCES: tuple[SourceAdapterState, ...] = (
    # Supported — projection adapters ship in v1.
    SourceAdapterState(
        source_name="core.sessions",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="<butler_schema>.sessions (per-schema; fan-out read)",
        boundary_semantics=(
            "started_at → session_started point event; "
            "completed_at → session_completed point event; "
            "(started_at, completed_at) → work episode when both present"
        ),
        optional_schema=False,
    ),
    SourceAdapterState(
        source_name="google_calendar.completed",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="calendar module completed-instance view",
        boundary_semantics=(
            "completed non-cancelled instances → scheduled-block episode; "
            "(instance_start, instance_end) bound the episode; "
            "provider dedup semantics apply"
        ),
        optional_schema=True,
    ),
    # Spotify listening sessions — evidence surface landed in PR #1115 (bu-e5jmh).
    SourceAdapterState(
        source_name="spotify.session_summary",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="connectors.spotify_listening_sessions",
        boundary_semantics=(
            "one listening episode per session row; "
            "(started_at, ended_at) bound the episode; "
            "per-track events deferred (bu-pa4e0.10)"
        ),
        optional_schema=True,
    ),
    # Google Health sleep/workout projection — sleep landed in bu-yhs2c,
    # workout promotion in bu-i29ix.
    SourceAdapterState(
        source_name="google_health.measurements",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="health.facts (predicate=sleep_session|workout_session)",
        boundary_semantics=(
            "one sleep_episode per sleep_session fact; "
            "one workout_episode per workout_session fact; "
            "(valid_at, end_time or valid_at+duration_ms) bound the episode; "
            "precision=minute (wearable device clock); "
            "workouts carrying heart-rate metadata are privacy=sensitive"
        ),
        optional_schema=True,
    ),
    SourceAdapterState(
        source_name="health.steps",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="health.facts (predicate=measurement_steps|daily_steps)",
        boundary_semantics=(
            "daily_steps point event per step-count fact; "
            "valid_at is the day/window anchor; precision=day"
        ),
        optional_schema=True,
    ),
    SourceAdapterState(
        source_name="health.heart_rate",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface=(
            "health.facts "
            "(predicate=measurement_resting_hr|heart_rate_summary|measurement_heart_rate)"
        ),
        boundary_semantics=(
            "heart_rate_summary point event per heart-rate fact; "
            "daily summaries use precision=day, manual point measurements use precision=minute"
        ),
        optional_schema=True,
    ),
    # Steam daily playtime aggregates — adapter landed in bu-x8trk.
    SourceAdapterState(
        source_name="steam.play_history",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="connectors.steam_play_history",
        boundary_semantics=(
            "one play_episode per daily aggregate row; "
            "(date midnight UTC, date midnight UTC + playtime_minutes) bound the episode; "
            "precision=day (daily aggregates, not exact session timestamps)"
        ),
        optional_schema=True,
    ),
    SourceAdapterState(
        source_name="owntracks.points",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="connectors.owntracks_points",
        boundary_semantics=(
            "one location point event per GPS fix; "
            "contiguous fixes within 30 min → movement_episode rollup"
        ),
        optional_schema=True,
    ),
    SourceAdapterState(
        source_name="home_assistant.history",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="connectors.home_assistant_history",
        boundary_semantics=(
            "person.* state changes → presence_episode rollups; "
            "contiguous home-state runs collapse into a single episode per entity"
        ),
        optional_schema=True,
    ),
    # Meals — projection adapter landed in bu-qclzp (sibling epic bu-a512n).
    SourceAdapterState(
        source_name="health.meals",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="health.meals",
        boundary_semantics=(
            "eating_event point events; one row per logged meal; "
            "eaten_at only (no end_at) — point events, not episodes"
        ),
        optional_schema=True,
    ),
    # Inferred chronicler-derived sources (bu-i29ix). Both read from
    # chronicler's own episodes table to derive higher-level shapes
    # (focus / reading), keeping inference deterministic and
    # self-contained. They produce the focus_block and reading_block
    # episode types that fold into the existing 'tasks' lane.
    SourceAdapterState(
        source_name="chronicler.focus_inferred",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="chronicler.episodes (own-schema)",
        boundary_semantics=(
            "focus_block episode per qualifying source episode; "
            "reuses the source episode (start_at, end_at); "
            "signal: long task session OR calendar-titled focus block"
        ),
        optional_schema=False,
    ),
    SourceAdapterState(
        source_name="chronicler.reading_inferred",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="chronicler.episodes + health.facts (predicate=reading_session)",
        boundary_semantics=(
            "reading_block episode per qualifying calendar-titled or "
            "fact-derived row; (start_at, end_at) per source"
        ),
        optional_schema=True,
    ),
    # Inferred exercise from independent HR+GPS corroboration (bu-1sj3zn).
    # Emits exercise_episode candidates over movement windows that carry an
    # elevated heart rate and are NOT already covered by an explicit workout.
    SourceAdapterState(
        source_name="chronicler.exercise_inferred",
        chronicler_compatibility=Compatibility.SUPPORTED,
        read_surface="chronicler.episodes (movement) + chronicler.point_events (heart_rate)",
        boundary_semantics=(
            "exercise_episode per movement window with elevated HR and no "
            "overlapping workout_episode; reuses the movement (start_at, end_at)"
        ),
        optional_schema=False,
    ),
    # Explicitly not time-bearing.
    SourceAdapterState(
        source_name="core.session_process_logs",
        chronicler_compatibility=Compatibility.NOT_TIME_BEARING,
        read_surface=None,
        boundary_semantics="TTL diagnostic logs; not authoritative retrospective time",
        optional_schema=False,
    ),
)


def supported_source_names() -> tuple[str, ...]:
    return tuple(
        s.source_name
        for s in INITIAL_SOURCES
        if s.chronicler_compatibility == Compatibility.SUPPORTED
    )


def deferred_source_names() -> tuple[str, ...]:
    return tuple(
        s.source_name
        for s in INITIAL_SOURCES
        if s.chronicler_compatibility == Compatibility.DEFERRED
    )


def planned_source_names() -> tuple[str, ...]:
    return tuple(
        s.source_name
        for s in INITIAL_SOURCES
        if s.chronicler_compatibility == Compatibility.PLANNED
    )


def find_source(source_name: str) -> SourceAdapterState | None:
    for s in INITIAL_SOURCES:
        if s.source_name == source_name:
            return replace(s)
    return None


async def seed_source_registry(
    conn: asyncpg.Connection | asyncpg.Pool,
    sources: Iterable[SourceAdapterState] | None = None,
) -> int:
    """Upsert every initial source declaration into ``source_adapter_state``.

    Idempotent. Returns the number of registrations applied.
    """
    count = 0
    for state in sources if sources is not None else INITIAL_SOURCES:
        await register_source(conn, state)
        count += 1
    return count


__all__ = [
    "INITIAL_SOURCES",
    "deferred_source_names",
    "find_source",
    "planned_source_names",
    "seed_source_registry",
    "supported_source_names",
]
