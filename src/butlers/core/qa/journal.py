"""QA investigation journal event writes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

import asyncpg

from butlers.core.utils import generate_uuid7_string

JournalStep = Literal[
    "flagged",
    "sampled",
    "cross-checked",
    "considered",
    "concluded",
    "drafted",
    "wait",
    "merged",
    "tick",
    "escalated",
]

VALID_JOURNAL_STEPS: frozenset[str] = frozenset(JournalStep.__args__)
type AsyncSession = asyncpg.Pool | asyncpg.Connection


async def record_event(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    step: JournalStep,
    text: str,
    detail: str | None = None,
    data: dict | None = None,
    finding_id: uuid.UUID | None = None,
    ts: datetime | None = None,
) -> uuid.UUID:
    """Insert one QA investigation journal event and return its UUIDv7 id.

    The caller owns transaction scope; this helper does not commit.
    """
    if step not in VALID_JOURNAL_STEPS:
        raise ValueError(f"Unknown QA journal step: {step!r}")

    event_id = uuid.UUID(generate_uuid7_string())
    event_ts = ts or datetime.now(UTC)
    await session.fetchval(
        """
        INSERT INTO public.qa_investigation_events (
            id, attempt_id, finding_id, ts, step, text, detail, data
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        RETURNING id
        """,
        event_id,
        str(attempt_id),
        str(finding_id) if finding_id is not None else None,
        event_ts,
        step,
        text,
        detail,
        json.dumps(data or {}),
    )
    return event_id
