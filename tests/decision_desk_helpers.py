"""Shared fixtures for the Decision Desk write-bridge tests (bu-ckkpz.3).

Builders for the decision digest the runtime validates against, and a real
core + switchboard migrated database so the intent and prompt invariants are
exercised by PostgreSQL itself rather than by a mocked pool.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg

from butlers.jobs.decision_review import DecisionBead, DecisionDigest, EscalationHit
from butlers.testing.migration import create_migrated_test_db, migration_db_name

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def make_bead(
    bead_id: str = "bu-test1",
    *,
    options: tuple[str, ...] | None = ("Ship it", "Hold"),
    default: str | None = "Hold",
    age: timedelta = timedelta(days=2),
    title: str = "Pick a rollout",
    available: bool = True,
) -> DecisionBead:
    return DecisionBead(
        id=bead_id,
        title=title,
        priority=1,
        created_at=NOW - age,
        age=age,
        description="Context.",
        options=options if available else None,
        default=default if available else None,
        due_at=None,
        structured_details_available=available,
        structured_details_unavailable_reason=None if available else "missing_options",
    )


def make_digest(
    *beads: DecisionBead,
    escalated: tuple[str, ...] = (),
    available: bool = True,
) -> DecisionDigest:
    return DecisionDigest(
        checked_at=NOW,
        available=available,
        unavailable_reason=None if available else "export_missing",
        open_decisions=tuple(beads) if available else (),
        escalations=tuple(
            EscalationHit(
                decision_id=bead_id,
                decision_title="t",
                blocked_id="bu-blocked",
                blocked_title="b",
                blocked_kind="p1_bug",
                blocked_since=NOW - timedelta(days=3),
                block_age=timedelta(days=3),
            )
            for bead_id in escalated
        ),
        export_as_of=NOW if available else None,
    )


def migrated_switchboard_db(postgres_container: object) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


async def switchboard_pool(db_url: str) -> asyncpg.Pool:
    """A pool over *db_url* with the desk tables emptied."""
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=4)
    await pool.execute(
        "TRUNCATE switchboard.decision_intents, switchboard.decision_prompts, "
        "public.attention_ledger"
    )
    return pool
