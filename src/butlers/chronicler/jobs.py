"""Deterministic scheduled job handlers for Chronicler projection adapters."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import asyncpg

from butlers.chronicler.adapters import (
    ActivityWatchWindowAdapter,
    CalendarCompletedAdapter,
    CommsSocialAdapter,
    CoreSessionsAdapter,
    ExerciseInferredAdapter,
    FocusInferredAdapter,
    GoogleHealthHeartRateAdapter,
    GoogleHealthSleepAdapter,
    GoogleHealthStepsAdapter,
    GoogleHealthWorkoutAdapter,
    HomeAssistantHistoryAdapter,
    HomeAssistantSensorActivityAdapter,
    MealsAdapter,
    OccupationInferredAdapter,
    OwnerOutboundMessageAdapter,
    OwnTracksPlaceClusterAdapter,
    OwnTracksPointAdapter,
    ReadingInferredAdapter,
    SpotifySessionAdapter,
    SteamPlayAdapter,
)
from butlers.chronicler.adapters.owntracks_place_cluster import parse_place_references
from butlers.chronicler.contracts import seed_source_registry
from butlers.config import list_butlers

if TYPE_CHECKING:
    from butlers.chronicler.adapters import ProjectionAdapter

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_SCHEMAS: tuple[str, ...] = (
    "chronicler",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)

_DEFAULT_CALENDAR_SCHEMAS: tuple[str, ...] = (
    "finance",
    "general",
    "health",
    "lifestyle",
    "messenger",
    "relationship",
    "travel",
)


def _normalize_positive_int(
    raw_value: Any,
    *,
    job_name: str,
    field_name: str,
) -> int:
    if not isinstance(raw_value, int) or isinstance(raw_value, bool) or raw_value <= 0:
        raise RuntimeError(f"{job_name} job_args.{field_name} must be a positive integer")
    return raw_value


def _parse_job_args(
    job_name: str,
    job_args: dict[str, Any] | None,
    *,
    supported_fields: tuple[str, ...],
) -> dict[str, int]:
    normalized: dict[str, int] = {}
    if job_args is None:
        return normalized

    unknown_fields = sorted(set(job_args) - set(supported_fields))
    if unknown_fields:
        raise RuntimeError(
            f"{job_name} job only supports {', '.join(supported_fields)}; "
            f"received unsupported keys: {unknown_fields}"
        )

    for field_name in supported_fields:
        if field_name in job_args:
            normalized[field_name] = _normalize_positive_int(
                job_args[field_name],
                job_name=job_name,
                field_name=field_name,
            )
    return normalized


def _dedupe_non_empty(values: list[str]) -> tuple[str, ...]:
    ordered_unique = dict.fromkeys(value for value in values if value)
    return tuple(ordered_unique)


def _adapter_result_to_dict(result: Any) -> dict[str, Any]:
    """Serialize an ``AdapterResult`` to a JSON-friendly job result dict.

    ``AdapterResult.watermark`` is a ``datetime`` (asyncpg-decoded
    ``TIMESTAMPTZ``); convert it to an ISO-8601 string so the result
    survives JSONB persistence by the scheduler without bespoke encoders.
    """
    payload = asdict(result)
    watermark = payload.get("watermark")
    if watermark is not None:
        payload["watermark"] = watermark.isoformat()
    return payload


async def _run_adapter(
    *,
    db_pool: asyncpg.Pool,
    adapter: ProjectionAdapter,
) -> dict[str, Any]:
    """Seed Chronicler source contracts, run one adapter, and surface failures."""
    await seed_source_registry(db_pool)
    result = await adapter.run(pool=db_pool, chronicler_pool=db_pool)
    if result.error is not None:
        raise RuntimeError(f"{result.source_name} projection failed: {result.error}")
    return _adapter_result_to_dict(result)


def _discover_session_schemas() -> tuple[str, ...]:
    try:
        configs = list_butlers()
    except Exception:  # pragma: no cover - exercised via patched tests
        logger.exception(
            "Failed to discover butler configs for Chronicler sessions projection; "
            "using fallback schema set"
        )
        return _DEFAULT_SESSION_SCHEMAS

    schemas = _dedupe_non_empty([cfg.db_schema or "" for cfg in configs])
    return schemas or _DEFAULT_SESSION_SCHEMAS


def _discover_calendar_schemas() -> tuple[str, ...]:
    try:
        configs = list_butlers()
    except Exception:  # pragma: no cover - exercised via patched tests
        logger.exception(
            "Failed to discover butler configs for Chronicler calendar projection; "
            "using fallback schema set"
        )
        return _DEFAULT_CALENDAR_SCHEMAS

    schemas = _dedupe_non_empty(
        [cfg.db_schema or "" for cfg in configs if "calendar" in cfg.modules]
    )
    return schemas or _DEFAULT_CALENDAR_SCHEMAS


async def run_project_sessions(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project cross-butler session records into Chronicler."""
    options = _parse_job_args(
        "chronicler_project_sessions",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = CoreSessionsAdapter(
        butler_schemas=_discover_session_schemas(),
        **options,
    )
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_calendar(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project completed calendar instances into Chronicler."""
    options = _parse_job_args(
        "chronicler_project_calendar",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = CalendarCompletedAdapter(
        butler_schemas=_discover_calendar_schemas(),
        **options,
    )
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_owntracks(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project OwnTracks location points into Chronicler."""
    options = _parse_job_args(
        "chronicler_project_owntracks",
        job_args,
        supported_fields=("batch_limit", "movement_gap_minutes"),
    )
    adapter = OwnTracksPointAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_owntracks_place_cluster(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project OwnTracks GPS place clusters into Chronicler (bu-ac2pg).

    Owner-declared reference points (e.g. home/work lat-lon) are configured
    via the ``OWNTRACKS_PLACE_REFERENCES`` environment variable — a JSON list
    of ``{"label", "lat", "lon", "radius_m"?}`` objects, mirroring the
    ``HA_WELLNESS_RULES_EXTRA`` owner-extensibility pattern. Unset/empty means
    every recurring cluster surfaces honestly as ``place_unknown``.

    Unlike ``HA_WELLNESS_RULES_EXTRA`` (validated once at daemon startup, so a
    fail-fast raise is immediately visible to the operator), this env var is
    re-read on every ``*/30`` scheduled tick. A malformed value must not wedge
    the job on every run until an operator notices and fixes it — so parsing
    degrades gracefully here: log and fall back to no reference points (every
    cluster then honestly surfaces as ``place_unknown``, never crashes).
    """
    options = _parse_job_args(
        "chronicler_project_owntracks_place_cluster",
        job_args,
        supported_fields=("batch_limit", "min_dwell_minutes", "max_gap_minutes"),
    )
    raw_references = os.environ.get("OWNTRACKS_PLACE_REFERENCES", "")
    try:
        reference_points = parse_place_references(raw_references)
    except ValueError:
        logger.warning(
            "Malformed OWNTRACKS_PLACE_REFERENCES; falling back to no reference points "
            "(every cluster will surface as place_unknown)",
            exc_info=True,
        )
        reference_points = ()
    adapter = OwnTracksPlaceClusterAdapter(reference_points=reference_points, **options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_activitywatch(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project ActivityWatch desktop-activity window-focus events into Chronicler."""
    options = _parse_job_args(
        "chronicler_project_activitywatch",
        job_args,
        supported_fields=("batch_limit", "screen_gap_minutes"),
    )
    adapter = ActivityWatchWindowAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_owner_outbound(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project owner-outbound-message point events into Chronicler (bu-whhll.8)."""
    options = _parse_job_args(
        "chronicler_project_owner_outbound",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = OwnerOutboundMessageAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_steam(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project Steam play-history rows into Chronicler."""
    options = _parse_job_args(
        "chronicler_project_steam",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = SteamPlayAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_meals(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project health meals into Chronicler as eating_event point events."""
    options = _parse_job_args(
        "chronicler_project_meals",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = MealsAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_home_assistant(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project Home Assistant state-change history into Chronicler."""
    options = _parse_job_args(
        "chronicler_project_home_assistant",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = HomeAssistantHistoryAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_home_assistant_sensor_activity(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project non-person HA binary_sensor activity into Chronicler (bu-49fqa)."""
    options = _parse_job_args(
        "chronicler_project_home_assistant_sensor_activity",
        job_args,
        supported_fields=("batch_limit", "room_activity_gap_minutes"),
    )
    adapter = HomeAssistantSensorActivityAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_google_health_sleep(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project Google Health sleep-session facts into Chronicler sleep episodes."""
    options = _parse_job_args(
        "chronicler_project_google_health_sleep",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = GoogleHealthSleepAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_spotify(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project Spotify listening sessions into Chronicler listening episodes."""
    options = _parse_job_args(
        "chronicler_project_spotify",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = SpotifySessionAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_google_health_workout(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project Google Health workout-session facts into Chronicler workout episodes."""
    options = _parse_job_args(
        "chronicler_project_google_health_workout",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = GoogleHealthWorkoutAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_google_health_steps(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project Google Health step-count facts into Chronicler point events."""
    options = _parse_job_args(
        "chronicler_project_google_health_steps",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = GoogleHealthStepsAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_google_health_heart_rate(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project Google Health heart-rate facts into Chronicler point events."""
    options = _parse_job_args(
        "chronicler_project_google_health_heart_rate",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = GoogleHealthHeartRateAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_focus_inferred(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive focus_block episodes from already-projected chronicler data."""
    options = _parse_job_args(
        "chronicler_project_focus_inferred",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = FocusInferredAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_reading_inferred(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive reading_block episodes from calendar titles and reading facts."""
    options = _parse_job_args(
        "chronicler_project_reading_inferred",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = ReadingInferredAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_exercise_inferred(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive exercise_episode candidates from HR+GPS corroboration."""
    options = _parse_job_args(
        "chronicler_project_exercise_inferred",
        job_args,
        supported_fields=("batch_limit", "elevated_hr_bpm"),
    )
    adapter = ExerciseInferredAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_occupation_inferred(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive occupation_block episodes from enabled routine windows."""
    options = _parse_job_args(
        "chronicler_project_occupation_inferred",
        job_args,
        supported_fields=("lookback_days",),
    )
    adapter = OccupationInferredAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_project_comms(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project comms message bursts (gmail/telegram/whatsapp/discord) into social_episode."""
    options = _parse_job_args(
        "chronicler_project_comms",
        job_args,
        supported_fields=("batch_limit",),
    )
    adapter = CommsSocialAdapter(**options)
    return await _run_adapter(db_pool=db_pool, adapter=adapter)


async def run_routines_mine(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Mine N weeks of chronicler activity episodes for stable weekday routines.

    Deterministic, no LLM (bu-whhll.9). Unlike the ``chronicler_project_*``
    adapters, this job is not watermark-incremental — it re-scans the full
    ``weeks``-wide window on every run and upserts into
    ``chronicler.routines``, which is a summary table, not an episode stream.
    """
    from butlers.chronicler.routines import DEFAULT_TIMEZONE, DEFAULT_WEEKS, mine_routines

    supported_fields = ("weeks", "timezone")
    weeks = DEFAULT_WEEKS
    timezone = DEFAULT_TIMEZONE
    if job_args:
        unknown_fields = sorted(set(job_args) - set(supported_fields))
        if unknown_fields:
            raise RuntimeError(
                f"chronicler_routines_mine job only supports {', '.join(supported_fields)}; "
                f"received unsupported keys: {unknown_fields}"
            )
        if "weeks" in job_args:
            weeks = _normalize_positive_int(
                job_args["weeks"], job_name="chronicler_routines_mine", field_name="weeks"
            )
        if "timezone" in job_args:
            raw_timezone = job_args["timezone"]
            if not isinstance(raw_timezone, str) or not raw_timezone:
                raise RuntimeError(
                    "chronicler_routines_mine job_args.timezone must be a non-empty string"
                )
            timezone = raw_timezone

    return await mine_routines(db_pool, weeks=weeks, timezone=timezone)


async def run_rollup_daily(
    db_pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Materialize per-lane daily rollups for every fully-elapsed local day
    in the trailing lookback window (bu-u30as, telemetry-distillation bead 3).

    Deterministic, no LLM — reuses ``aggregations.lane_for_activity``/
    ``union_seconds`` exactly as ``GET /aggregate/by-category`` does. Not
    watermark-incremental, same convention as ``run_routines_mine``: each run
    re-processes the trailing ``lookback_days`` window and upserts
    idempotently on ``(local_date, lane)``.
    """
    from butlers.chronicler.rollups import (
        DEFAULT_LOOKBACK_DAYS,
        DEFAULT_TIMEZONE,
        materialize_daily_rollups,
    )

    supported_fields = ("lookback_days", "timezone")
    lookback_days = DEFAULT_LOOKBACK_DAYS
    timezone = DEFAULT_TIMEZONE
    if job_args:
        unknown_fields = sorted(set(job_args) - set(supported_fields))
        if unknown_fields:
            raise RuntimeError(
                f"chronicler_rollup_daily job only supports {', '.join(supported_fields)}; "
                f"received unsupported keys: {unknown_fields}"
            )
        if "lookback_days" in job_args:
            lookback_days = _normalize_positive_int(
                job_args["lookback_days"],
                job_name="chronicler_rollup_daily",
                field_name="lookback_days",
            )
        if "timezone" in job_args:
            raw_timezone = job_args["timezone"]
            if not isinstance(raw_timezone, str) or not raw_timezone:
                raise RuntimeError(
                    "chronicler_rollup_daily job_args.timezone must be a non-empty string"
                )
            timezone = raw_timezone

    return await materialize_daily_rollups(db_pool, timezone=timezone, lookback_days=lookback_days)


__all__ = [
    "_DEFAULT_CALENDAR_SCHEMAS",
    "_DEFAULT_SESSION_SCHEMAS",
    "run_project_activitywatch",
    "run_project_calendar",
    "run_project_comms",
    "run_project_exercise_inferred",
    "run_project_focus_inferred",
    "run_project_google_health_heart_rate",
    "run_project_google_health_sleep",
    "run_project_google_health_steps",
    "run_project_google_health_workout",
    "run_project_home_assistant",
    "run_project_home_assistant_sensor_activity",
    "run_project_meals",
    "run_project_occupation_inferred",
    "run_project_owner_outbound",
    "run_project_owntracks",
    "run_project_owntracks_place_cluster",
    "run_project_reading_inferred",
    "run_project_sessions",
    "run_project_spotify",
    "run_project_steam",
    "run_rollup_daily",
    "run_routines_mine",
]
