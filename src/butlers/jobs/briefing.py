"""Daily briefing contribution and aggregation jobs.

Specialist butlers call ``run_daily_briefing_contribution`` to write their
domain snapshot to the state store.  The general butler calls
``run_collect_briefing_contributions`` to aggregate all specialist
contributions into a combined payload.

See openspec/changes/cross-butler-daily-briefing/ for the full design.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg


async def run_daily_briefing_contribution(*, pool: asyncpg.Pool) -> dict[str, Any]:
    """Write today's domain snapshot as a briefing contribution.

    Queries butler-specific state tables, assembles the contribution envelope
    (butler, date, has_updates, highlights, summary), and persists it to the
    state store under ``briefing/daily/<YYYY-MM-DD>`` (SGT).

    Not yet implemented — registered in daemon._DETERMINISTIC_SCHEDULE_JOB_REGISTRY
    as a placeholder pending tasks 3.x of the cross-butler-daily-briefing spec.
    """
    raise NotImplementedError(
        "run_daily_briefing_contribution is not yet implemented; "
        "see openspec/changes/cross-butler-daily-briefing/tasks.md tasks 3.1-3.6"
    )


async def run_collect_briefing_contributions(*, pool: asyncpg.Pool) -> dict[str, Any]:
    """Aggregate specialist contributions into a combined briefing payload.

    Reads today's rows from ``general.v_briefing_contributions``, validates
    each contribution envelope, and writes the merged result to
    ``briefing/combined/<YYYY-MM-DD>`` in the general butler's state store.

    Not yet implemented — registered in daemon._DETERMINISTIC_SCHEDULE_JOB_REGISTRY
    as a placeholder pending task 5.1 of the cross-butler-daily-briefing spec.
    """
    raise NotImplementedError(
        "run_collect_briefing_contributions is not yet implemented; "
        "see openspec/changes/cross-butler-daily-briefing/tasks.md task 5.1"
    )
