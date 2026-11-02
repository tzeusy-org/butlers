"""Document deadlines: template-pinned, prompt-free deadlines taken from read documents.

A due, renewal, cancel-by, expiry or return-by date read from a document becomes a
``scheduled_tasks`` deadline in **job** mode. It carries no prompt, so a firing threshold
runs the fixed ``paperwork_deadline_notify`` job (``butlers.jobs.document_deadlines``),
which sends the owner a templated reminder and starts no session or goal. Because no
document text can schedule work, the capability is effect class ``record`` (RFC 0038 D4);
prompt-bearing deadlines stay ``administer``.

One deadline exists per ``(document_id, kind)``: setting it again with the same date and
preset changes nothing, and a new date supersedes in place and re-arms its thresholds
without re-enabling a deadline the owner disabled (an expired one, disabled by the
scheduler, is re-enabled). A later-dated deadline from the same issuer and kind replaces
those of other documents dated on or before it and keeps an owner's disable from them; an
earlier date never drops a reminder. Per-issuer and per-butler caps bound how many active
deadlines document text can create.
The row's ``job_args`` carries the D6 taint marker, since the date came from untrusted
document text.

See: openspec/changes/document-deadlines/specs/deadline-tracking/spec.md
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any

import asyncpg

from butlers.core.temporal.deadlines import validate_deadline_input

NOTICE_JOB_NAME = "paperwork_deadline_notify"
TAINT_MARKER = "untrusted_document"

DOCUMENT_DEADLINE_KINDS = ("due", "renewal", "cancel_by", "expiry", "return_by")


def _threshold(days_before: int, severity: str) -> dict[str, Any]:
    return {"days_before": days_before, "severity": severity}


# preset -> (lead_time_days, thresholds, most distant first)
DOCUMENT_DEADLINE_PRESETS = MappingProxyType(
    {
        "two_weeks": (
            14,
            (_threshold(14, "info"), _threshold(3, "warning"), _threshold(1, "critical")),
        ),
        "one_month": (
            30,
            (_threshold(30, "info"), _threshold(7, "warning"), _threshold(1, "critical")),
        ),
        "three_days": (3, (_threshold(3, "warning"), _threshold(1, "critical"))),
    }
)
DEFAULT_PRESET = "two_weeks"

MAX_DOCUMENT_ID_CHARS = 512
MAX_ISSUER_CHARS = 60
# About ten years: a passport expiry fits, a mis-read year does not.
MAX_HORIZON_DAYS = 3660
# Safety bounds on reminders created from untrusted document text: a tainted goal cannot
# fill the owner's chat by varying document ids or issuer names. Active means not
# ``expired`` or ``completed``; documents without an issuer share one bucket.
MAX_ACTIVE_PER_ISSUER = 10
MAX_ACTIVE_DOCUMENT_DEADLINES = 200
_WRITE_LOCK = "butlers.paperwork_deadline_set"

# Issuer text is printed to the owner as inert text: letters, digits, spaces and a few
# name punctuation marks only, so no markup, link or URL survives.
_ISSUER_DROP = re.compile(r"[^\w &',\-]|_")
_SPACES = re.compile(r"\s+")


class DocumentDeadlineRefused(ValueError):
    """A typed refusal; ``reason`` is the stable machine-readable code."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class DocumentDeadlinePlan:
    """A validated document deadline, ready to upsert. It has no prompt by construction."""

    name: str
    target_date: date
    preset: str
    lead_time_days: int
    alert_thresholds: list[dict[str, Any]]
    job_args: dict[str, Any]
    job_name: str = NOTICE_JOB_NAME


def sanitize_issuer(value: Any) -> str | None:
    """Reduce an issuer name to inert text, or ``None`` when nothing usable is left."""
    if not isinstance(value, str):
        return None
    cleaned = _SPACES.sub(" ", _ISSUER_DROP.sub("", _SPACES.sub(" ", value))).strip()
    return cleaned[:MAX_ISSUER_CHARS].strip() or None


def _today() -> date:
    return datetime.now(UTC).date()


def _deadline_name(document_id: str, kind: str) -> str:
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:24]
    return f"paperwork:{kind}:{digest}"


def _thresholds_from(preset_thresholds: tuple[dict[str, Any], ...], days_remaining: int):
    """Keep future thresholds plus the single most urgent one already due.

    A deadline set late (a bill read two days before it is due) sends one notice now,
    not one per threshold it has already passed.
    """
    future = [dict(t) for t in preset_thresholds if t["days_before"] < days_remaining]
    passed = [t for t in preset_thresholds if t["days_before"] >= days_remaining]
    if passed:
        future.append(dict(min(passed, key=lambda t: t["days_before"])))
    return sorted(future, key=lambda t: t["days_before"], reverse=True)


def plan_document_deadline(
    *,
    document_id: Any,
    kind: Any,
    target_date: Any,
    preset: Any = DEFAULT_PRESET,
    issuer: Any = None,
) -> DocumentDeadlinePlan:
    """Validate input and build the deadline plan, or raise :class:`DocumentDeadlineRefused`."""
    if (
        not isinstance(document_id, str)
        or not document_id.strip()
        or len(document_id) > MAX_DOCUMENT_ID_CHARS
        or not document_id.isprintable()
    ):
        raise DocumentDeadlineRefused(
            "invalid_document_id",
            f"document_id must be printable text of 1-{MAX_DOCUMENT_ID_CHARS} characters",
        )
    if kind not in DOCUMENT_DEADLINE_KINDS:
        raise DocumentDeadlineRefused(
            "invalid_kind", f"kind must be one of {', '.join(DOCUMENT_DEADLINE_KINDS)}"
        )
    if preset not in DOCUMENT_DEADLINE_PRESETS:
        raise DocumentDeadlineRefused(
            "invalid_preset", f"preset must be one of {', '.join(DOCUMENT_DEADLINE_PRESETS)}"
        )
    try:
        parsed = date.fromisoformat(target_date) if isinstance(target_date, str) else None
    except ValueError:
        parsed = None
    if parsed is None:
        raise DocumentDeadlineRefused("invalid_date", "target_date must be YYYY-MM-DD")
    days_remaining = (parsed - _today()).days
    if days_remaining <= 0:
        raise DocumentDeadlineRefused("past_date", "target_date must be after today")
    if days_remaining > MAX_HORIZON_DAYS:
        raise DocumentDeadlineRefused(
            "invalid_date", f"target_date must be within {MAX_HORIZON_DAYS} days"
        )

    lead_time_days, preset_thresholds = DOCUMENT_DEADLINE_PRESETS[preset]
    thresholds = _thresholds_from(preset_thresholds, days_remaining)
    validate_deadline_input(
        target_date=parsed, lead_time_days=lead_time_days, alert_thresholds=thresholds
    )
    document_id = document_id.strip()
    return DocumentDeadlinePlan(
        name=_deadline_name(document_id, kind),
        target_date=parsed,
        preset=preset,
        lead_time_days=lead_time_days,
        alert_thresholds=thresholds,
        job_args={
            "document_id": document_id,
            "kind": kind,
            "issuer": sanitize_issuer(issuer),
            "preset": preset,
            "taint": TAINT_MARKER,
        },
    )


async def _supersede_siblings(
    conn: asyncpg.Connection, plan: DocumentDeadlinePlan
) -> tuple[list[str], bool]:
    """Delete same-issuer, same-kind deadlines of other documents dated on or before the plan.

    Return the deleted ids and whether any was disabled by the owner (not ``expired`` or
    ``completed``), whose disable the replacement then keeps. A deadline without an issuer
    cannot be matched to another document, and an earlier date never drops a later reminder.
    """
    issuer = plan.job_args.get("issuer")
    if issuer is None:
        return [], False
    deleted = await conn.fetch(
        """
        DELETE FROM scheduled_tasks
        WHERE task_type = 'deadline' AND dispatch_mode = 'job' AND job_name = $1
          AND name <> $2
          AND job_args->>'kind' = $3
          AND lower(job_args->>'issuer') = lower($4::text)
          AND target_date <= $5
        RETURNING id,
            (NOT enabled AND deadline_status NOT IN ('expired', 'completed')) AS owner_disabled
        """,
        plan.job_name,
        plan.name,
        plan.job_args["kind"],
        issuer,
        plan.target_date,
    )
    return [str(r["id"]) for r in deleted], any(r["owner_disabled"] for r in deleted)


async def _check_caps(conn: asyncpg.Connection, plan: DocumentDeadlinePlan) -> None:
    """Refuse a new row once the issuer's or the butler's active document deadlines are full."""
    counts = await conn.fetchrow(
        """
        SELECT count(*) AS total,
               count(*) FILTER (
                   WHERE lower(job_args->>'issuer') IS NOT DISTINCT FROM lower($2::text)
               ) AS issuer
        FROM scheduled_tasks
        WHERE task_type = 'deadline' AND dispatch_mode = 'job' AND job_name = $1
          AND deadline_status NOT IN ('expired', 'completed')
        """,
        plan.job_name,
        plan.job_args.get("issuer"),
    )
    if counts["issuer"] >= MAX_ACTIVE_PER_ISSUER:
        raise DocumentDeadlineRefused(
            "issuer_cap_reached",
            f"at most {MAX_ACTIVE_PER_ISSUER} active document deadlines per issuer",
        )
    if counts["total"] >= MAX_ACTIVE_DOCUMENT_DEADLINES:
        raise DocumentDeadlineRefused(
            "total_cap_reached",
            f"at most {MAX_ACTIVE_DOCUMENT_DEADLINES} active document deadlines",
        )


async def document_deadline_set(pool: asyncpg.Pool, plan: DocumentDeadlinePlan) -> dict[str, Any]:
    """Upsert the plan's deadline; return ``{"id", "status", "replaced"}``.

    ``status`` is ``created``, ``unchanged`` (same date and preset; only the issuer text
    may be refreshed, fired thresholds and status are kept) or ``superseded`` (a new date
    or preset: thresholds re-arm and the status returns to ``pending``; ``enabled`` is kept,
    so document text never overrides an owner's disable, except that an ``expired``
    deadline, which the scheduler disabled itself, is re-enabled). A name already
    held by any other task (a prompt-bearing deadline, a cron schedule) is refused as
    ``name_conflict`` and left untouched.

    A created or superseded deadline also replaces the same issuer's deadlines of the same
    kind from other documents dated on or before it (``replaced`` lists their ids) and keeps
    an owner's disable from any of them. A new row is refused as ``issuer_cap_reached`` or
    ``total_cap_reached`` once the active document deadlines are full; every refusal
    writes nothing. One transaction-scoped lock serializes these writes, so the caps hold
    under concurrent calls.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1::text))", _WRITE_LOCK)
        row = await conn.fetchrow(
            """
            SELECT id, task_type, dispatch_mode, job_name, target_date, job_args
            FROM scheduled_tasks WHERE name = $1 FOR UPDATE
            """,
            plan.name,
        )
        if row is not None:
            if (row["task_type"], row["dispatch_mode"], row["job_name"]) != (
                "deadline",
                "job",
                plan.job_name,
            ):
                raise DocumentDeadlineRefused(
                    "name_conflict", f"{plan.name} is held by another scheduled task"
                )
            existing_args = row["job_args"] if isinstance(row["job_args"], dict) else {}
            if (
                row["target_date"] == plan.target_date
                and existing_args.get("preset") == plan.preset
            ):
                if existing_args != plan.job_args:
                    await conn.execute(
                        "UPDATE scheduled_tasks SET job_args = $2, updated_at = now() "
                        "WHERE id = $1",
                        row["id"],
                        plan.job_args,
                    )
                return {"id": str(row["id"]), "status": "unchanged", "replaced": []}

        replaced, owner_disabled = await _supersede_siblings(conn, plan)
        if row is None:
            await _check_caps(conn, plan)
            new_id = await conn.fetchval(
                """
                INSERT INTO scheduled_tasks (
                    name, cron, dispatch_mode, job_name, job_args, source, enabled,
                    next_run_at, task_type, target_date, lead_time_days,
                    alert_thresholds, deadline_status, fired_thresholds, depends_on
                )
                VALUES (
                    $1, '0 0 * * *', 'job', $2, $3, 'db', $7,
                    now(), 'deadline', $4, $5, $6, 'pending', '[]'::jsonb, '[]'::jsonb
                )
                ON CONFLICT (name) DO NOTHING
                RETURNING id
                """,
                plan.name,
                plan.job_name,
                plan.job_args,
                plan.target_date,
                plan.lead_time_days,
                plan.alert_thresholds,
                not owner_disabled,
            )
            if new_id is None:
                # Another writer took the name between the lookup and the insert.
                raise DocumentDeadlineRefused(
                    "name_conflict", f"{plan.name} is held by another scheduled task"
                )
            return {"id": str(new_id), "status": "created", "replaced": replaced}

        await conn.execute(
            """
            UPDATE scheduled_tasks
            SET target_date = $2, lead_time_days = $3, alert_thresholds = $4,
                job_args = $5, fired_thresholds = '[]'::jsonb, deadline_status = 'pending',
                enabled = (enabled OR deadline_status = 'expired') AND NOT $6,
                next_run_at = now(), updated_at = now()
            WHERE id = $1
            """,
            row["id"],
            plan.target_date,
            plan.lead_time_days,
            plan.alert_thresholds,
            plan.job_args,
            owner_disabled,
        )
        return {"id": str(row["id"]), "status": "superseded", "replaced": replaced}
