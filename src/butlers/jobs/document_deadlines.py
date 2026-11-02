"""Fixed-template owner notice for document deadlines (``paperwork_deadline_notify``).

The scheduler's deadline pass runs this job when a job-mode document deadline crosses a
threshold (``butlers.core.temporal.document_deadlines``). It starts no session: the
message is built from a fixed template whose only variable parts are the deadline kind
(one of five fixed labels), the date and the issuer, re-sanitized here to inert text. It
goes only to the owner through :func:`butlers.jobs.owner_notify.send_owner_telegram`, so
the owner-only recipient and the attention ledger apply. A notice that is not delivered
raises, so the deadline pass leaves its threshold unfired and retries on a later tick.

Failed sends back off: each attempt's ledger row carries a per-(deadline, threshold)
``dedup_key``, and the next attempt waits :func:`retry_backoff` of the failure count,
measured from the newest failed row. A Switchboard outage therefore writes one failed row
per backoff step rather than one per tick, and the threshold still fires once delivery
recovers. The ledger is the only state; nothing is stored on the deadline row.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any

import asyncpg

from butlers.core.attention_ledger import failed_attempts
from butlers.core.temporal.deadlines import DeadlineDispatchDeferred
from butlers.core.temporal.document_deadlines import NOTICE_JOB_NAME, sanitize_issuer
from butlers.jobs import owner_notify

_KIND_LABELS = {
    "due": "payment due",
    "renewal": "renews",
    "cancel_by": "cancel by",
    "expiry": "expires",
    "return_by": "return by",
}


RETRY_BACKOFF_BASE = timedelta(minutes=5)
RETRY_BACKOFF_CAP = timedelta(hours=1)


def retry_backoff(failures: int) -> timedelta:
    """Wait before the next attempt after ``failures`` failed sends: 5 min doubling to 1 h."""
    if failures <= 0:
        return timedelta(0)
    return min(RETRY_BACKOFF_BASE * 2 ** min(failures - 1, 16), RETRY_BACKOFF_CAP)


def attempt_key(job_args: dict[str, Any]) -> str | None:
    """Ledger ``dedup_key`` shared by every send attempt for one deadline threshold.

    It hashes the document, kind, target date and threshold, so a re-set date or the next
    threshold starts with no failures and the document id stays out of the ledger. Rows
    missing any part get no key and no backoff.
    """
    context = job_args.get("deadline")
    threshold = context.get("fired_threshold") if isinstance(context, dict) else None
    parts = [
        job_args.get("document_id"),
        job_args.get("kind"),
        context.get("target_date") if isinstance(context, dict) else None,
        threshold.get("days_before") if isinstance(threshold, dict) else None,
    ]
    if any(part is None for part in parts):
        return None
    digest = hashlib.sha256(json.dumps(parts).encode("utf-8")).hexdigest()[:32]
    return f"{NOTICE_JOB_NAME}:{digest}"


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
    """Send one templated reminder to the owner, or raise so the threshold stays unfired.

    A deadline threshold has no later run, so a notice that is not delivered must not
    consume it. Quiet hours or a dnd/sleeping signal raise
    :class:`DeadlineDispatchDeferred` before any send, so the pass retries on a later
    tick without writing an attention-ledger row per attempt. A failed send is recorded
    once by the notify boundary and raised, so the pass retries it as a failure; until
    its backoff has elapsed, the next attempt defers with ``retry_backoff`` and no row.
    """
    job_args = job_args or {}
    message = render_document_deadline_notice(job_args)
    if message is None:
        return {"outcome": "skipped", "reason": "invalid_kind"}
    reason = await owner_notify.check_owner_notify_suppression(pool, log_context=NOTICE_JOB_NAME)
    if reason is not None:
        raise DeadlineDispatchDeferred(reason)
    key = attempt_key(job_args)
    if key is not None:
        failures, since_last = await failed_attempts(pool, dedup_key=key)
        if since_last is not None and since_last < retry_backoff(failures):
            raise DeadlineDispatchDeferred("retry_backoff")
    outcome = await owner_notify.send_owner_telegram(
        pool,
        origin_butler=origin_butler,
        message=message,
        log_context=NOTICE_JOB_NAME,
        dedup_key=key,
    )
    if outcome == owner_notify.SUPPRESSED:
        # Suppression began between the check and the send: still retry later.
        raise DeadlineDispatchDeferred("suppressed")
    if outcome != owner_notify.DELIVERED:
        raise RuntimeError(f"{NOTICE_JOB_NAME}: owner notice {outcome}")
    return {"outcome": outcome}
