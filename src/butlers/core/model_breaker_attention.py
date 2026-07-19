"""Model-catalog breaker-open attention-ledger owner push (bu-hmdqz.2).

A dispatch-outcome circuit breaker opening for a catalog entry (see
``butlers.core.model_routing.get_breaker_state``) is exactly the class of
event the attention ledger exists for: the JARVIS pursuit incident that
motivated the breaker (a revoked Codex OAuth token, 29 suppressed failovers
in 48h, gpt-5.6-luna dispatched 20+ consecutive times despite failing every
time) was only observable by visiting the Models tab -- the owner never knew
the top-priority workhorse tier had gone dark. This module pages the owner
once, at the moment a breaker opens, with the model and the failure count.

Call site
---------
``Spawner`` calls :func:`maybe_push_breaker_open_attention` right after
writing the ``runtime_failure`` dispatch-attempt row that trips a breaker
open (see ``core.spawner``'s same-tier failover loop). That write only
happens on an actual dispatch attempt, and a breaker-open entry is excluded
from resolution until its half-open cooldown elapses -- so this call site is
naturally rate-limited by the resolver itself; the audit_log debounce below
is a defense-in-depth backstop against concurrent workers racing the same
write, not the primary rate limit.

Once-per-cooldown de-dup [decision]
------------------------------------
Unlike the fleet-halt ceiling breach (bu-7o89u.4), a breaker's state is
fully derived, not stored -- there is no persistent "already open" flag to
consult. Debounce is instead time-boxed: a push for a given catalog entry is
skipped if the entry was already pushed within
``_RENOTIFY_COOLDOWN_MINUTES`` (mirrors the breaker's own half-open cooldown,
``model_routing._BREAKER_HALF_OPEN_COOLDOWN_MINUTES``), persisted the same
way ``fleet_halt_attention`` and ``butlers.jobs.secrets_lifecycle`` already
debounce -- an ``public.audit_log`` row, no new migration.  A push for the
SAME entry that reopens after a failed half-open probe intentionally
re-notifies once the cooldown has passed again, since that is genuinely new
information (the credential/model is still broken).

Severity / gating [decision]
-----------------------------
Mirrors ``fleet_halt_attention``: ``priority="high"`` (the default) bypasses
quiet-hours/context-bus gating via ``notify()``'s existing high-priority
lever; ``priority`` is threaded through so tests can exercise the gate.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.api.routers import audit as audit_router
from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    is_policy_quiet_now,
)
from butlers.core.attention_ledger import get_suppressing_context_signal, record_attention_event
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

_ACTOR = "model_breaker_monitor"
_BREAKER_NOTIFIED_ACTION = "model_breaker_open_notified"
_BREAKER_NOTIFIED_TARGET_PREFIX = "model_breaker:"

_DASHBOARD_PORT_DEFAULT = "41200"

# Mirrors model_routing._BREAKER_HALF_OPEN_COOLDOWN_MINUTES: a breaker-open
# entry is not re-attempted (and so cannot re-trip a fresh runtime_failure
# write) until its own cooldown elapses, so re-notifying on the same cadence
# means "notify once per distinct open episode", not "notify once ever".
_RENOTIFY_COOLDOWN_MINUTES = 15


def _dashboard_url() -> str:
    """Resolve the dashboard base URL from the environment.

    Kept local rather than imported -- mirrors the same local copy in
    ``butlers.core.fleet_halt_attention`` and ``butlers.jobs.secrets_lifecycle``.
    """
    return os.environ.get(
        "DASHBOARD_URL",
        f"http://localhost:{os.environ.get('DASHBOARD_PORT', _DASHBOARD_PORT_DEFAULT)}",
    )


async def _recently_notified(pool: asyncpg.Pool, catalog_entry_id: uuid.UUID) -> bool:
    """Return True when a push was already recorded for this entry within the cooldown.

    Fails open to False (i.e. "not yet notified") on any lookup error --
    consistent with treating an urgent push as more important to attempt
    than to skip on doubt (mirrors ``fleet_halt_attention``).
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT ts FROM public.audit_log
            WHERE target = $1 AND action = $2
            ORDER BY ts DESC
            LIMIT 1
            """,
            f"{_BREAKER_NOTIFIED_TARGET_PREFIX}{catalog_entry_id}",
            _BREAKER_NOTIFIED_ACTION,
        )
    except Exception:
        logger.warning(
            "model_breaker_attention: debounce lookup failed; treating as not-yet-notified",
            exc_info=True,
        )
        return False
    if row is None:
        return False
    ts = row["ts"]
    if ts is None:
        return False
    return (datetime.now(UTC) - ts) < timedelta(minutes=_RENOTIFY_COOLDOWN_MINUTES)


async def _check_suppression(pool: asyncpg.Pool) -> str | None:
    """Mirror notify()'s owner-default gate (quiet hours, then context bus).

    Only meaningful for the (non-default) ``priority != "high"`` path.
    """
    try:
        policy = await get_approvals_policy_quiet_hours(pool)
    except Exception:
        logger.debug("model_breaker_attention: quiet-hours policy lookup failed", exc_info=True)
        policy = None

    if is_policy_quiet_now(policy, now=datetime.now(UTC)):
        return "quiet_hours"

    context_signal = await get_suppressing_context_signal(pool)
    if context_signal is not None:
        return f"context_bus:{context_signal}"

    return None


def _compose_message(
    alias: str,
    model_id: str,
    consecutive_failures: int,
    door_url: str,
) -> str:
    return (
        f"Model breaker open: {alias} ({model_id}) excluded from routing after "
        f"{consecutive_failures} consecutive dispatch failures.\n"
        f"{door_url}"
    )


async def maybe_push_breaker_open_attention(
    pool: asyncpg.Pool | None,
    *,
    catalog_entry_id: uuid.UUID,
    alias: str,
    model_id: str,
    consecutive_failures: int,
    priority: str = "high",
) -> None:
    """Best-effort, cooldown-debounced owner push for a newly-open model breaker.

    Must be called from the spawner's same-tier failover loop right after a
    ``runtime_failure`` dispatch-attempt row has been written AND
    ``model_routing.get_breaker_state`` confirms the entry's breaker is now
    open. Never raises -- every failure mode (debounce lookup, suppression
    check, delivery) is caught, logged at WARNING, and swallowed, exactly
    like ``record_attention_event`` and ``fleet_halt_attention`` themselves.
    """
    if pool is None:
        return

    try:
        if await _recently_notified(pool, catalog_entry_id):
            return

        dashboard_url = _dashboard_url()
        door_url = f"{dashboard_url}/settings/models?highlight={catalog_entry_id}"
        dedup_key = f"model_breaker_open:{catalog_entry_id}"
        metadata: dict[str, Any] = {
            "catalog_entry_id": str(catalog_entry_id),
            "alias": alias,
            "model_id": model_id,
            "consecutive_failures": consecutive_failures,
            "door": door_url,
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
            # Genuine terminal failure, not a benign hold -- bu-hmdqz.3
            # widened the Outcome vocabulary precisely so this reads
            # honestly instead of impersonating quiet-hours discipline.
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

        message = _compose_message(alias, model_id, consecutive_failures, door_url)

        # Local import: mirrors fleet_halt_attention's own local import of the
        # same symbol -- roster/ modules aren't always importable at
        # collection time.
        from butlers.tools.switchboard.notification.deliver import deliver

        deliver_result = await deliver(
            pool,
            channel="telegram",
            message=message,
            recipient=recipient,
            source_butler="switchboard",
            metadata={"origin": _ACTOR, "catalog_entry_id": str(catalog_entry_id)},
        )

        if deliver_result.get("status") == "failed":
            # Genuine terminal failure, not a benign hold -- see the
            # no_recipient_configured branch above for why this is "failed"
            # (bu-hmdqz.3), not "deferred".
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
            reason=f"breaker_open:{catalog_entry_id}",
            dedup_key=dedup_key,
            notification_ref=deliver_result.get("notification_id"),
            metadata=metadata,
        )
        # Debounce marker: written only on confirmed delivery, so a deferred/
        # suppressed attempt correctly retries on the very next trip instead
        # of going silent for the rest of the cooldown window.
        await audit_router.append(
            pool,
            _ACTOR,
            _BREAKER_NOTIFIED_ACTION,
            target=f"{_BREAKER_NOTIFIED_TARGET_PREFIX}{catalog_entry_id}",
            note=str(consecutive_failures),
        )
    except Exception:
        logger.warning(
            "model_breaker_attention: unexpected error pushing breaker-open attention",
            exc_info=True,
        )
