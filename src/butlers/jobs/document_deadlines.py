"""Fixed-template owner notice for document deadlines (``paperwork_deadline_notify``).

The scheduler's deadline pass runs this job when a job-mode document deadline crosses a
threshold (``butlers.core.temporal.document_deadlines``). It starts no session: the
message is built from a fixed template whose only variable parts are the deadline kind
(one of five fixed labels), the date and the issuer, re-sanitized here to inert text. It
goes only to the owner through :func:`butlers.jobs.owner_notify.send_owner_telegram`, so
quiet hours, the owner-only recipient and the attention ledger all apply.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

from butlers.core.temporal.document_deadlines import NOTICE_JOB_NAME, sanitize_issuer
from butlers.jobs import owner_notify

_KIND_LABELS = {
    "due": "payment due",
    "renewal": "renews",
    "cancel_by": "cancel by",
    "expiry": "expires",
    "return_by": "return by",
}


def _when(days_remaining: int) -> str:
    if days_remaining <= 0:
        return "today"
    if days_remaining == 1:
        return "tomorrow"
    return f"in {days_remaining} days"


def render_document_deadline_notice(job_args: dict[str, Any]) -> str | None:
    """Return the owner message, or ``None`` when the stored row is not a valid notice."""
    kind = job_args.get("kind")
    label = _KIND_LABELS.get(kind) if isinstance(kind, str) else None
    context = job_args.get("deadline")
    if label is None or not isinstance(context, dict):
        return None
    try:
        target = date.fromisoformat(str(context["target_date"]))
        days_remaining = int(context["days_remaining"])
    except (KeyError, TypeError, ValueError):
        return None
    issuer = sanitize_issuer(job_args.get("issuer"))
    subject = f"Paperwork reminder ({issuer})" if issuer else "Paperwork reminder"
    when = f"{target:%a} {target.day} {target:%b %Y}"
    return f"{subject}: {label} {when}, {_when(days_remaining)}."


async def run_paperwork_deadline_notice(
    pool: asyncpg.Pool, job_args: dict[str, Any] | None, *, origin_butler: str
) -> dict[str, Any]:
    """Send one templated reminder to the owner and return the recorded outcome."""
    message = render_document_deadline_notice(job_args or {})
    if message is None:
        return {"outcome": "skipped", "reason": "invalid_kind"}
    outcome = await owner_notify.send_owner_telegram(
        pool, origin_butler=origin_butler, message=message, log_context=NOTICE_JOB_NAME
    )
    return {"outcome": outcome}
