"""Deterministic scheduled job handlers for Chronicler projection adapters."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import asyncpg

from butlers.chronicler.adapters import (
    CalendarCompletedAdapter,
    CoreSessionsAdapter,
    ExerciseInferredAdapter,
    FocusInferredAdapter,
    GoogleHealthHeartRateAdapter,
    GoogleHealthSleepAdapter,
    GoogleHealthStepsAdapter,
    GoogleHealthWorkoutAdapter,
    HomeAssistantHistoryAdapter,
    MealsAdapter,
    OwnTracksPointAdapter,
    ReadingInferredAdapter,
    SpotifySessionAdapter,
    SteamPlayAdapter,
)
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


__all__ = [
    "_DEFAULT_CALENDAR_SCHEMAS",
    "_DEFAULT_SESSION_SCHEMAS",
    "run_project_calendar",
    "run_project_exercise_inferred",
    "run_project_focus_inferred",
    "run_project_google_health_heart_rate",
    "run_project_google_health_sleep",
    "run_project_google_health_steps",
    "run_project_google_health_workout",
    "run_project_home_assistant",
    "run_project_meals",
    "run_project_owntracks",
    "run_project_reading_inferred",
    "run_project_sessions",
    "run_project_spotify",
    "run_project_steam",
]
