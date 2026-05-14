"""QA investigation journal event writes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

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
OPEN_PATROL_TICK_STATUSES: tuple[str, ...] = ("investigating", "pr_open", "dispatch_pending")
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


def _format_pr_detail(
    *,
    additions: int | None,
    deletions: int | None,
    file_count: int | None,
) -> str | None:
    if additions is None or deletions is None or file_count is None:
        return None
    files_label = "file" if file_count == 1 else "files"
    return f"+{additions} / −{deletions} · {file_count} {files_label}"


async def record_pr_drafted_event(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    pr_number: int | None,
    branch_name: str,
    additions: int | None = None,
    deletions: int | None = None,
    file_count: int | None = None,
    ts: datetime | None = None,
) -> uuid.UUID:
    """Insert the journal event for a drafted QA PR."""

    number_label = pr_number if pr_number is not None else "unknown"
    return await record_event(
        session,
        attempt_id=attempt_id,
        step="drafted",
        text=f"PR #{number_label} · {branch_name}",
        detail=_format_pr_detail(
            additions=additions,
            deletions=deletions,
            file_count=file_count,
        ),
        data={
            "pr_number": pr_number,
            "branch_name": branch_name,
            "additions": additions,
            "deletions": deletions,
            "file_count": file_count,
        },
        ts=ts,
    )


async def record_wait_event_once(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    patrol_started_at: datetime,
    pending_count: int,
    pending_check_names: list[str],
    ts: datetime | None = None,
) -> uuid.UUID | None:
    """Insert at most one CI-wait journal event for an attempt in a patrol cycle."""

    exists = await session.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM public.qa_investigation_events
            WHERE attempt_id = $1
              AND step = 'wait'
              AND ts >= $2
        )
        """,
        attempt_id,
        patrol_started_at,
    )
    if exists:
        return None

    detail = ", ".join(pending_check_names) if pending_check_names else None
    return await record_event(
        session,
        attempt_id=attempt_id,
        step="wait",
        text=f"CI · {pending_count} checks pending",
        detail=detail,
        data={"pending_count": pending_count, "pending_check_names": pending_check_names},
        ts=ts,
    )


async def record_pr_merged_event(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    detail: str | None = None,
    ts: datetime | None = None,
) -> uuid.UUID:
    """Insert the journal event for a merged QA PR."""

    return await record_event(
        session,
        attempt_id=attempt_id,
        step="merged",
        text="CI green · merged",
        detail=detail,
        ts=ts,
    )


async def record_escalated_event(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    text: str,
    detail: str | None = None,
    ts: datetime | None = None,
) -> uuid.UUID:
    """Insert the journal event for a QA case escalated to human attention."""

    return await record_event(
        session,
        attempt_id=attempt_id,
        step="escalated",
        text=text,
        detail=detail,
        ts=ts,
    )


def _format_case_age(created_at: datetime | None, now: datetime) -> str:
    if created_at is None:
        return "unknown age"

    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    elapsed = max(now - created_at, now - now)
    total_minutes = int(elapsed.total_seconds() // 60)
    if total_minutes < 1:
        return "less than 1m"
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours, minutes = divmod(total_minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, rem_hours = divmod(hours, 24)
    return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"


def _tick_detail(row: Any, now: datetime) -> str:
    age = _format_case_age(row["detected_at"], now)
    status = row["status"]
    if status == "dispatch_pending":
        return f"awaiting dispatch for {age}"
    if status == "pr_open":
        pr_number = row["pr_number"]
        review_state = row["review_state"]
        follow_up_status = row["last_follow_up_status"]
        if follow_up_status == "dispatched":
            return f"awaiting follow-up result for {age}"
        if review_state:
            return f"awaiting PR review follow-up for {age} (review_state={review_state})"
        if pr_number is not None:
            return f"awaiting PR #{pr_number} checks or review for {age}"
        return f"awaiting PR checks or review for {age}"

    current_phase = row["current_phase"]
    if current_phase:
        return f"investigation ongoing for {age}; current phase {current_phase}"
    return f"investigation ongoing for {age}"


async def record_patrol_tick_events(
    session: AsyncSession,
    *,
    patrol_id: uuid.UUID,
    patrol_started_at: datetime,
    ts: datetime | None = None,
) -> list[uuid.UUID]:
    """Insert tick events for open QA cases unchanged during a patrol cycle.

    Uses one query to find eligible attempts and one array-based insert for the
    journal rows. The caller owns transaction scope; this helper does not commit.
    """
    tick_ts = ts or datetime.now(UTC)
    rows = await session.fetch(
        """
        SELECT h.id,
               h.status,
               h.created_at,
               COALESCE(MIN(f.first_seen), h.created_at) AS detected_at,
               h.current_phase,
               h.pr_number,
               h.review_state,
               h.last_follow_up_status
        FROM public.healing_attempts h
        LEFT JOIN public.qa_findings f ON f.healing_attempt_id = h.id
        WHERE h.status = ANY($1::text[])
          AND h.closed_at IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM public.qa_investigation_events e
              WHERE e.attempt_id = h.id
                AND e.ts >= $2
          )
        GROUP BY h.id,
                 h.status,
                 h.created_at,
                 h.current_phase,
                 h.pr_number,
                 h.review_state,
                 h.last_follow_up_status
        ORDER BY h.created_at ASC, h.id ASC
        """,
        list(OPEN_PATROL_TICK_STATUSES),
        patrol_started_at,
    )
    if not rows:
        return []

    patrol_id_str = str(patrol_id)
    patrol_id_short = patrol_id_str.split("-", 1)[0]
    event_ids: list[uuid.UUID] = []
    attempt_ids: list[uuid.UUID] = []
    event_ts_values: list[datetime] = []
    steps: list[str] = []
    texts: list[str] = []
    details: list[str] = []
    data_values: list[str] = []

    for row in rows:
        age = _format_case_age(row["detected_at"], tick_ts)
        event_ids.append(uuid.UUID(generate_uuid7_string()))
        attempt_ids.append(row["id"])
        event_ts_values.append(tick_ts)
        steps.append("tick")
        texts.append(f"patrol cycle {patrol_id_short} - case still {row['status']}")
        details.append(_tick_detail(row, tick_ts))
        data_values.append(
            json.dumps(
                {
                    "patrol_id": patrol_id_str,
                    "status": row["status"],
                    "case_age": age,
                }
            )
        )

    await session.execute(
        """
        INSERT INTO public.qa_investigation_events (
            id, attempt_id, ts, step, text, detail, data
        )
        SELECT event_id, attempt_id, event_ts, step, text, detail, data::jsonb
        FROM unnest(
            $1::uuid[],
            $2::uuid[],
            $3::timestamptz[],
            $4::text[],
            $5::text[],
            $6::text[],
            $7::text[]
        ) AS rows(event_id, attempt_id, event_ts, step, text, detail, data)
        """,
        event_ids,
        attempt_ids,
        event_ts_values,
        steps,
        texts,
        details,
        data_values,
    )
    return event_ids
