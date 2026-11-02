"""Document deadlines: template-pinned, prompt-free deadlines taken from read documents.

A due, renewal, cancel-by, expiry or return-by date read from a document becomes a
``scheduled_tasks`` deadline in **job** mode. It carries no prompt, so a firing threshold
runs the fixed ``paperwork_deadline_notify`` job (``butlers.jobs.document_deadlines``),
which sends the owner a templated reminder and starts no session or goal. Because no
document text can schedule work, the capability is effect class ``record`` (RFC 0038 D4);
prompt-bearing deadlines stay ``administer``.

One deadline exists per ``(document_id, kind)``: setting it again with the same date and
preset changes nothing, and a new date supersedes in place and re-arms its thresholds.
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


async def document_deadline_set(pool: asyncpg.Pool, plan: DocumentDeadlinePlan) -> dict[str, Any]:
    """Upsert the plan's deadline; return ``{"id", "status"}``.

    ``status`` is ``created``, ``unchanged`` (same date and preset; only the issuer text
    may be refreshed, fired thresholds and status are kept) or ``superseded`` (a new date
    or preset: thresholds re-arm and the status returns to ``pending``). A name already
    held by any other task (a prompt-bearing deadline, a cron schedule) is refused as
    ``name_conflict`` and left untouched.
    """
    async with pool.acquire() as conn, conn.transaction():
        row = None
        for _ in range(2):
            row = await conn.fetchrow(
                """
                SELECT id, task_type, dispatch_mode, job_name, target_date, job_args
                FROM scheduled_tasks WHERE name = $1 FOR UPDATE
                """,
                plan.name,
            )
            if row is not None:
                break
            new_id = await conn.fetchval(
                """
                INSERT INTO scheduled_tasks (
                    name, cron, dispatch_mode, job_name, job_args, source, enabled,
                    next_run_at, task_type, target_date, lead_time_days,
                    alert_thresholds, deadline_status, fired_thresholds, depends_on
                )
                VALUES (
                    $1, '0 0 * * *', 'job', $2, $3, 'db', true,
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
            )
            if new_id is not None:
                return {"id": str(new_id), "status": "created"}
        if row is None:
            raise DocumentDeadlineRefused("busy", "concurrent write; retry")

        if (row["task_type"], row["dispatch_mode"], row["job_name"]) != (
            "deadline",
            "job",
            plan.job_name,
        ):
            raise DocumentDeadlineRefused(
                "name_conflict", f"{plan.name} is held by another scheduled task"
            )
        existing_args = row["job_args"] if isinstance(row["job_args"], dict) else {}
        if row["target_date"] == plan.target_date and existing_args.get("preset") == plan.preset:
            if existing_args != plan.job_args:
                await conn.execute(
                    "UPDATE scheduled_tasks SET job_args = $2, updated_at = now() WHERE id = $1",
                    row["id"],
                    plan.job_args,
                )
            return {"id": str(row["id"]), "status": "unchanged"}

        await conn.execute(
            """
            UPDATE scheduled_tasks
            SET target_date = $2, lead_time_days = $3, alert_thresholds = $4,
                job_args = $5, fired_thresholds = '[]'::jsonb, deadline_status = 'pending',
                enabled = true, next_run_at = now(), updated_at = now()
            WHERE id = $1
            """,
            row["id"],
            plan.target_date,
            plan.lead_time_days,
            plan.alert_thresholds,
            plan.job_args,
        )
        return {"id": str(row["id"]), "status": "superseded"}
