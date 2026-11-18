"""One durable "was this you?" question per authenticated security event (bu-s11n0s.1 S5c).

openspec ``endpoint-custody-holds`` REQ-endpoint-custody-holds-008, dashboard half. The bus
excludes a publisher from its own fan-out, so Switchboard keeps a publisher-local outbox
(``public.security_questions``, core_349): one row per authenticated
``switchboard.security_event``, keyed by the event id. Duplicate observations and consumer
retries find the same row. Unverified and failed events get no row (the event stays an honest
record) and never interrupt the owner.

Presentation is one bounded notify with a dashboard link. The row is claimed (``pending`` to
``presenting``) before the send and never claimed again, so a send that may have started
without confirmation ends ``ambiguous`` and is never re-sent. The owner's answer is committed
on the dashboard (``public.security_question_dashboard``); this module then records the canonical
account-security case with its static recovery door for every committed ``no``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from butlers.core.account_security_events import SECURITY_EVENT_TYPE, record_security_answer

logger = logging.getLogger(__name__)

AUTHENTICATED = "authenticated"
#: A claimed question with no recorded outcome this long after the claim is ambiguous.
STALE_PRESENTING = timedelta(minutes=15)
#: Events older than this are not admitted by the sweep (bounds a first-deploy burst).
ADMISSION_WINDOW = timedelta(days=2)
PRESENT_BATCH = 5
CASE_BATCH = 20

_KIND_PHRASES = {
    "new_sign_in": "a new sign-in",
    "password_changed": "a password change",
    "mfa_changed": "a change to two-step verification",
    "recovery_changed": "a change to recovery options",
    "deletion_scheduled": "a scheduled account deletion",
}

NotifyFn = Callable[[str], Awaitable[dict[str, Any]]]

# record_event pre-dumps its payload, so a pool with the jsonb codec stores a JSON string
# scalar; unwrap it before reading fields.
_PAYLOAD = """
CROSS JOIN LATERAL (
    SELECT CASE WHEN jsonb_typeof(e.payload) = 'string'
                THEN (e.payload #>> '{}')::jsonb ELSE e.payload END AS body
) p
"""


def question_message(provider: str, kind: str, link: str) -> str:
    """Fixed vocabulary only: no sender text, subject or message body."""
    what = _KIND_PHRASES.get(kind, "a security change")
    return (
        f"Was this you? Your {provider.title()} account reported {what}. "
        f"Answer on your dashboard: {link}"
    )


def question_link(dashboard_url: str | None, event_id: str) -> str | None:
    # The configured dashboard base (``DASHBOARD_URL``); anything but an http(s) base is no link.
    base = dashboard_url or ""
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{base.rstrip('/')}/security/{event_id}"


async def admit_event(pool: Any, *, event_id: str, provider: str, kind: str) -> bool:
    """Admit one question for *event_id*; ``False`` when it already has one."""
    admitted = await pool.fetchval(
        """
        INSERT INTO public.security_questions (event_id, provider, kind)
        VALUES ($1::uuid, $2, $3)
        ON CONFLICT (event_id) DO NOTHING
        RETURNING 1
        """,
        event_id,
        provider,
        kind,
    )
    return bool(admitted)


async def admit_missing(
    pool: Any, *, now: datetime | None = None, limit: int = PRESENT_BATCH * 4
) -> int:
    """Recover events published but not admitted (crash between publish and admission)."""
    since = (now or datetime.now(UTC)) - ADMISSION_WINDOW
    rows = await pool.fetch(
        f"""
        INSERT INTO public.security_questions (event_id, provider, kind)
        SELECT e.id, p.body ->> 'provider', p.body ->> 'kind'
        FROM public.domain_events e {_PAYLOAD}
        WHERE e.event_type = $1 AND e.occurred_at >= $2
          AND p.body ->> 'sender_verification' = $3
          AND coalesce(p.body ->> 'provider', '') <> ''
          AND coalesce(p.body ->> 'kind', '') <> ''
          AND NOT EXISTS (SELECT 1 FROM public.security_questions q WHERE q.event_id = e.id)
        ORDER BY e.occurred_at
        LIMIT $4
        ON CONFLICT (event_id) DO NOTHING
        RETURNING event_id
        """,
        SECURITY_EVENT_TYPE,
        since,
        AUTHENTICATED,
        limit,
    )
    return len(rows)


async def present_pending(
    pool: Any,
    *,
    notify: NotifyFn,
    dashboard_url: str | None,
    now: datetime | None = None,
    limit: int = PRESENT_BATCH,
) -> dict[str, int]:
    """Send each pending question once. Never re-sends a claimed question."""
    moment = now or datetime.now(UTC)
    counts = {"presented": 0, "unavailable": 0, "ambiguous": 0}
    stale = await pool.fetch(
        """
        UPDATE public.security_questions
        SET state = 'ambiguous', detail = 'no_outcome_recorded'
        WHERE state = 'presenting' AND attempted_at < $1
        RETURNING event_id
        """,
        moment - STALE_PRESENTING,
    )
    counts["ambiguous"] += len(stale)
    rows = await pool.fetch(
        """
        SELECT event_id, provider, kind FROM public.security_questions
        WHERE state = 'pending' ORDER BY admitted_at, event_id LIMIT $1
        """,
        limit,
    )
    for row in rows:
        event_id = str(row["event_id"])
        link = question_link(dashboard_url, event_id)
        if link is None:
            # Without a link the owner cannot answer: visibly unanswered, nothing sent.
            if await _transition(pool, event_id, "pending", "unavailable", "no_dashboard_link"):
                counts["unavailable"] += 1
            continue
        claimed = await pool.fetchval(
            """
            UPDATE public.security_questions SET state = 'presenting', attempted_at = $2
            WHERE event_id = $1::uuid AND state = 'pending' RETURNING 1
            """,
            event_id,
            moment,
        )
        if not claimed:
            continue
        outcome = await _send(notify, question_message(row["provider"], row["kind"], link))
        state, detail, notification_id = outcome
        await pool.execute(
            """
            UPDATE public.security_questions
            SET state = $2, detail = $3, notification_id = $4::uuid,
                presented_at = CASE WHEN $2 = 'presented' THEN $5 ELSE NULL END
            WHERE event_id = $1::uuid AND state = 'presenting'
            """,
            event_id,
            state,
            detail,
            notification_id,
            moment,
        )
        counts[state] += 1
    return counts


async def _send(notify: NotifyFn, message: str) -> tuple[str, str | None, str | None]:
    """``(state, detail, notification_id)``. Any doubt that a send started is ``ambiguous``."""
    from butlers.telegram_api import terminal_telegram_metadata

    try:
        result = await notify(message)
    except Exception:
        logger.warning("security question notify raised", exc_info=True)
        return "ambiguous", "notify_raised", None
    if not isinstance(result, dict):
        return "ambiguous", "unreadable_result", None
    if result.get("status") == "sent":
        notification_id = result.get("notification_id")
        return "presented", None, str(notification_id) if notification_id else None
    if terminal_telegram_metadata(result):
        return "unavailable", "provider_rejected", None
    return "ambiguous", "delivery_unconfirmed", None


async def _transition(pool: Any, event_id: str, old: str, new: str, detail: str) -> bool:
    return bool(
        await pool.fetchval(
            """
            UPDATE public.security_questions SET state = $3, detail = $4
            WHERE event_id = $1::uuid AND state = $2 RETURNING 1
            """,
            event_id,
            old,
            new,
            detail,
        )
    )


async def record_answered_cases(pool: Any, *, limit: int = CASE_BATCH) -> int:
    """Open the canonical case and recovery-door evidence for each committed ``no``.

    Idempotent per event (``record_security_answer``); a failure leaves the row unrecorded so the
    next run retries, and the dashboard reports ``case_recorded`` false meanwhile.
    """
    rows = await pool.fetch(
        """
        SELECT q.event_id FROM public.security_questions q
        JOIN public.security_answers a USING (event_id)
        WHERE a.answer = 'no' AND q.case_recorded_at IS NULL
        ORDER BY a.answered_at LIMIT $1
        """,
        limit,
    )
    recorded = 0
    for row in rows:
        event_id = str(row["event_id"])
        try:
            result = await record_security_answer(pool, event_id=event_id, answer="no")
        except Exception:
            logger.warning("security answer case failed for %s", event_id, exc_info=True)
            continue
        if result.get("status") != "ok" or not result.get("case_id"):
            continue
        await pool.execute(
            """
            UPDATE public.security_questions SET case_id = $2::uuid, case_recorded_at = now()
            WHERE event_id = $1::uuid AND case_recorded_at IS NULL
            """,
            event_id,
            result["case_id"],
        )
        recorded += 1
    return recorded


async def run_security_question_cycle(
    pool: Any, *, notify: NotifyFn, dashboard_url: str | None, now: datetime | None = None
) -> dict[str, Any]:
    """One scheduled pass: admit, present, then record cases for answered ``no`` decisions."""
    admitted = await admit_missing(pool, now=now)
    presented = await present_pending(pool, notify=notify, dashboard_url=dashboard_url, now=now)
    cases = await record_answered_cases(pool)
    return {"admitted": admitted, **presented, "cases_recorded": cases}
