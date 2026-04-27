"""Deterministic scheduled jobs for the Chronicler butler."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import asyncpg

from butlers.chronicler.adapters import CalendarCompletedAdapter, CoreSessionsAdapter
from butlers.chronicler.adapters.base import ProjectionAdapter
from butlers.chronicler.contracts import seed_source_registry
from butlers.config import CONSOLIDATED_DB_NAME, ButlerType, list_butlers


def _discover_chronicler_source_schemas() -> tuple[str, ...]:
    """Return roster-backed source schemas Chronicler can project from.

    Chronicler's adapters fan out across schemas in the shared one-DB topology.
    Restrict the source list to butler-typed agents on the consolidated DB so
    staffers and standalone/split-DB configs do not leak into projection reads.
    """

    schemas: list[str] = []
    for config in list_butlers():
        if config.type != ButlerType.BUTLER:
            continue
        if config.db_name != CONSOLIDATED_DB_NAME or not config.db_schema:
            continue
        schemas.append(config.db_schema)
    return tuple(sorted(set(schemas)))


async def _run_adapters(
    *,
    pool: asyncpg.Pool,
    source_schemas: tuple[str, ...],
    adapters: Sequence[ProjectionAdapter],
) -> dict[str, Any]:
    await seed_source_registry(pool)

    results: list[dict[str, Any]] = []
    total_rows_projected = 0
    total_point_events = 0
    total_episodes_opened = 0
    total_episodes_closed = 0

    for adapter in adapters:
        result = await adapter.run(pool=pool, chronicler_pool=pool)
        if result.error is not None:
            raise RuntimeError(f"{adapter.source_name} projection failed: {result.error}")
        total_rows_projected += result.rows_projected
        total_point_events += result.point_events
        total_episodes_opened += result.episodes_opened
        total_episodes_closed += result.episodes_closed
        results.append(
            {
                "source_name": result.source_name,
                "rows_projected": result.rows_projected,
                "point_events": result.point_events,
                "episodes_opened": result.episodes_opened,
                "episodes_closed": result.episodes_closed,
                "warnings": list(result.warnings),
            }
        )

    return {
        "source_schemas": list(source_schemas),
        "rows_projected": total_rows_projected,
        "point_events": total_point_events,
        "episodes_opened": total_episodes_opened,
        "episodes_closed": total_episodes_closed,
        "results": results,
    }


async def run_project_sessions(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project canonical session rows into Chronicler events and episodes."""
    del job_args
    source_schemas = _discover_chronicler_source_schemas()
    return await _run_adapters(
        pool=pool,
        source_schemas=source_schemas,
        adapters=(CoreSessionsAdapter(butler_schemas=source_schemas),),
    )


async def run_project_calendar(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Project completed calendar instances into Chronicler episodes."""
    del job_args
    source_schemas = _discover_chronicler_source_schemas()
    return await _run_adapters(
        pool=pool,
        source_schemas=source_schemas,
        adapters=(CalendarCompletedAdapter(butler_schemas=source_schemas),),
    )


__all__ = [
    "run_project_calendar",
    "run_project_sessions",
]
