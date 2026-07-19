"""Fleet-halt attention-ledger owner push (bu-7o89u.4).

A monthly-spend-ceiling breach is exactly the class of event the attention
ledger exists for (``butlers.core.attention_ledger``): once
``check_monthly_ceiling`` denies a spawn, EVERY dispatch across EVERY butler
is denied for the rest of the calendar month (``spawner.py``'s ceiling-deny
branch), yet before this module existed that halt was only observable by
visiting the dashboard. This module pages the owner once, at the moment the
halt begins, with the denied count so far and a door into the ``/spend``
attempts drawer (bu-7o89u.3).

Call site
---------
``Spawner``'s ceiling-deny branch calls :func:`maybe_push_fleet_halt_attention`
immediately after writing the ``quota_skip`` dispatch-attempt row for a
ceiling breach. That deny path is HOT and shared across every butler in the
fleet -- it can be reached dozens of times a day while the fleet stays
halted -- so everything below is designed to be cheap on every call after the
first and to never raise, block, or meaningfully delay the deny decision it
augments (mirrors the ``record_attention_event`` degraded-honesty contract).

Once-per-window de-dup [decision]
----------------------------------
The ceiling resets on the calendar month boundary (``price_mtd_from_ledger``
prices month-to-date from ``date_trunc('month', now())``), so "once per halt
window" == "once per calendar month the halt persists into". Debounce is
persisted in ``public.audit_log`` (no new migration): the same pattern
``butlers.jobs.secrets_lifecycle`` already uses for its own once-per-state-
transition debounce. Each successful push writes an
``action="ceiling_halt_notified"`` row with ``note=<YYYY-MM>``; the next call
reads the most recent such row and skips entirely (no ledger write, no
delivery attempt) when its ``note`` already matches the current window. This
was chosen over a dedicated state table/column because ``audit_log`` is
already the established "debounce marker" surface in this codebase, is
already granted to every runtime role, and needs no migration (avoiding the
cross-worker migration-chain-collision trap noted in this repo's AGENTS.md).

Severity / gating [decision]
-----------------------------
The bead asks to respect the ledger's existing quiet-hours/context-bus
gating rather than bypassing it ad hoc, while still treating a fleet halt as
urgent. ``notify()`` already has a sanctioned lever for exactly this:
priority="high" skips the quiet-hours/context-bus consult entirely (see
``core_tools/_notifications.py`` lines ~593-598, "high-priority always
delivers immediately, per §8.6 spec"). This module calls that same lever
(``priority="high"``, the default) rather than inventing a parallel bypass --
respecting the gating means using the severity dial the gating itself
already defines, not special-casing around it. The ``priority`` parameter is
still threaded through (not hardcoded past the check) so the gating path is
real and testable: a lower priority genuinely gets suppressed by quiet hours
/ the context bus, proving this isn't a decorative call.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.api.routers import audit as audit_router
from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    is_policy_quiet_now,
)
from butlers.core.attention_ledger import get_suppressing_context_signal, record_attention_event
from butlers.core.model_routing import CEILING_DENIAL_REASON_PREFIX
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

_ACTOR = "fleet_halt_monitor"
_HALT_NOTIFIED_ACTION = "ceiling_halt_notified"
_HALT_NOTIFIED_TARGET = "ceiling_halt"

_DASHBOARD_PORT_DEFAULT = "41200"


def _dashboard_url() -> str:
    """Resolve the dashboard base URL from the environment.

    Kept local rather than imported (mirrors the same local copy in
    ``butlers.jobs.secrets_lifecycle`` -- that module's docstring explains why:
    a distinct concern from the Google-credential startup guard that owns the
    other copy of this lookup).
    """
    return os.environ.get(
        "DASHBOARD_URL",
        f"http://localhost:{os.environ.get('DASHBOARD_PORT', _DASHBOARD_PORT_DEFAULT)}",
    )


def _current_halt_window(now: datetime | None = None) -> str:
    """Return the current calendar-month window key, e.g. ``"2026-07"``."""
    moment = now if now is not None else datetime.now(UTC)
    return f"{moment.year:04d}-{moment.month:02d}"


def _current_month_start(now: datetime | None = None) -> datetime:
    moment = now if now is not None else datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _already_notified_this_window(pool: asyncpg.Pool, window: str) -> bool:
    """Return True when a push has already been recorded for this window.

    Fails open to False (i.e. "not yet notified") on any lookup error --
    consistent with treating an urgent push as more important to attempt than
    to skip on doubt.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT note FROM public.audit_log
            WHERE target = $1 AND action = $2
            ORDER BY ts DESC
            LIMIT 1
            """,
            _HALT_NOTIFIED_TARGET,
            _HALT_NOTIFIED_ACTION,
        )
    except Exception:
        logger.warning(
            "fleet_halt_attention: debounce lookup failed; treating as not-yet-notified",
            exc_info=True,
        )
        return False
    return row is not None and row["note"] == window


async def _count_denied_this_month(pool: asyncpg.Pool) -> int | None:
    """Count this month's ceiling-breach denials, or None if the query fails.

    Mirrors the exact prefix-match + month-window shape
    ``GET /api/dispatch/attempts?outcome=quota_skip&reason_prefix=...`` uses
    (``model_settings.py``), so this count can never diverge from what the
    /spend attempts drawer shows.
    """
    try:
        val = await pool.fetchval(
            """
            SELECT count(*) FROM public.model_dispatch_attempts
            WHERE outcome = 'quota_skip'
              AND left(failure_reason, length($1)) = $1
              AND ts >= $2
            """,
            CEILING_DENIAL_REASON_PREFIX,
            _current_month_start(),
        )
    except Exception:
        logger.warning("fleet_halt_attention: denied-count query failed", exc_info=True)
        return None
    return int(val) if val is not None else None


async def _check_suppression(pool: asyncpg.Pool) -> str | None:
    """Mirror notify()'s owner-default gate (quiet hours, then context bus).

    Only meaningful for the (non-default) ``priority != "high"`` path -- see
    module docstring's "Severity / gating" decision note.
    """
    try:
        policy = await get_approvals_policy_quiet_hours(pool)
    except Exception:
        logger.debug("fleet_halt_attention: quiet-hours policy lookup failed", exc_info=True)
        policy = None

    if is_policy_quiet_now(policy, now=datetime.now(UTC)):
        return "quiet_hours"

    context_signal = await get_suppressing_context_signal(pool)
    if context_signal is not None:
        return f"context_bus:{context_signal}"

    return None


def _compose_message(denied_count: int | None, dashboard_url: str, door_url: str) -> str:
    if denied_count is None:
        count_phrase = "an unknown number of dispatches"
    elif denied_count == 1:
        count_phrase = "1 dispatch"
    else:
        count_phrase = f"{denied_count} dispatches"
    return (
        "Fleet halt: monthly spend ceiling reached -- "
        f"{count_phrase} denied this month.\n"
        f"{door_url}"
    )


async def maybe_push_fleet_halt_attention(
    pool: asyncpg.Pool | None,
    *,
    priority: str = "high",
) -> None:
    """Best-effort, once-per-halt-window owner push for a ceiling breach.

    Must be called from the spawner's ceiling-deny path right after a
    ``quota_skip`` dispatch-attempt row has been written for a monthly-
    ceiling breach. Never raises -- every failure mode (debounce lookup,
    count query, suppression check, delivery) is caught, logged at WARNING,
    and swallowed, exactly like ``record_attention_event`` itself.

    ``priority`` defaults to "high" (the production call site's severity,
    per the module docstring's gating decision) but is threaded through so
    tests can exercise the quiet-hours/context-bus gate directly.
    """
    if pool is None:
        return

    try:
        window = _current_halt_window()
        if await _already_notified_this_window(pool, window):
            return

        denied_count = await _count_denied_this_month(pool)
        dashboard_url = _dashboard_url()
        door_url = f"{dashboard_url}/spend?openDrawer=fleet-halt"
        dedup_key = f"ceiling_halt:{window}"
        metadata: dict[str, Any] = {
            "denied_count": denied_count,
            "door": door_url,
            "window": window,
        }

        if priority != "high":
            suppress_reason = await _check_suppression(pool)
            if suppress_reason is not None:
                await record_attention_event(
                    pool,
                    origin_butler=_ACTOR,
                    source="notify",
                    outcome="suppressed",
                    channel="telegram",
                    intent="send",
                    priority=priority,
                    reason=suppress_reason,
                    dedup_key=dedup_key,
                    metadata=metadata,
                )
                return

        recipient = await resolve_owner_telegram_recipient(pool)
        if not recipient:
            await record_attention_event(
                pool,
                origin_butler=_ACTOR,
                source="notify",
                outcome="failed",
                channel="telegram",
                intent="send",
                priority=priority,
                reason="no_recipient_configured",
                dedup_key=dedup_key,
                metadata=metadata,
            )
            return

        message = _compose_message(denied_count, dashboard_url, door_url)

        # Local import: mirrors butlers.jobs.secrets_lifecycle's own local
        # import of the same symbol -- roster/ modules aren't always
        # importable at collection time, and this is the only place in this
        # module that needs the live dispatch path.
        from butlers.tools.switchboard.notification.deliver import deliver

        deliver_result = await deliver(
            pool,
            channel="telegram",
            message=message,
            recipient=recipient,
            source_butler="switchboard",
            metadata={"origin": _ACTOR, "window": window},
        )

        if deliver_result.get("status") == "failed":
            await record_attention_event(
                pool,
                origin_butler=_ACTOR,
                source="notify",
                outcome="failed",
                channel="telegram",
                intent="send",
                priority=priority,
                reason=f"delivery_error:{deliver_result.get('error', 'unknown')}",
                dedup_key=dedup_key,
                metadata=metadata,
            )
            return

        await record_attention_event(
            pool,
            origin_butler=_ACTOR,
            source="notify",
            outcome="delivered",
            channel="telegram",
            intent="send",
            priority=priority,
            reason=f"ceiling_halt_onset:{window}",
            dedup_key=dedup_key,
            notification_ref=deliver_result.get("notification_id"),
            metadata=metadata,
        )
        # Debounce marker: written only on confirmed delivery, so a deferred/
        # suppressed attempt correctly retries on the very next denial instead
        # of going silent for the rest of the halt window.
        await audit_router.append(
            pool,
            _ACTOR,
            _HALT_NOTIFIED_ACTION,
            target=_HALT_NOTIFIED_TARGET,
            note=window,
        )
    except Exception:
        logger.warning(
            "fleet_halt_attention: unexpected error pushing fleet-halt attention",
            exc_info=True,
        )
