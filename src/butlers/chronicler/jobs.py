"""Deterministic scheduled job handlers for Chronicler projection adapters."""

from __future__ import annotations

from dataclasses import asdict
import logging
from typing import Any

import asyncpg

from butlers.chronicler.adapters import (
    CalendarCompletedAdapter,
    CoreSessionsAdapter,
    OwnTracksPointAdapter,
    SteamPlayAdapter,
)
from butlers.config import list_butlers

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
        [
            cfg.db_schema or ""
            for cfg in configs
            if isinstance(cfg.modules, dict) and "calendar" in cfg.modules
        ]
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
    result = await adapter.run(pool=db_pool, chronicler_pool=db_pool)
    return asdict(result)


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
    result = await adapter.run(pool=db_pool, chronicler_pool=db_pool)
    return asdict(result)


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
    result = await adapter.run(pool=db_pool, chronicler_pool=db_pool)
    return asdict(result)


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
    result = await adapter.run(pool=db_pool, chronicler_pool=db_pool)
    return asdict(result)


__all__ = [
    "_DEFAULT_CALENDAR_SCHEMAS",
    "_DEFAULT_SESSION_SCHEMAS",
    "run_project_calendar",
    "run_project_owntracks",
    "run_project_sessions",
    "run_project_steam",
]
