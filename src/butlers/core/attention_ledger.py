"""The attention ledger — one durable record of all proactive owner egress.

Move 8 (2026-07-04 JARVIS pursuit, slice 1/2) — bu-qvnce.8. See RFC 0011
Amendment 1 (``about/legends-and-lore/rfcs/0011-proactive-insight-delivery.md``)
for the design rationale.

Every proactive message that could reach the owner passes through one of a
small, known set of egress paths:

- ``notify()`` (``butlers.core_tools._notifications``) — the core MCP tool
  every non-STAFFER butler registers, for direct owner-facing sends.
- ``delivery_cycle()`` (``roster/switchboard/tools/switchboard/insight/broker.py``)
  — the daily insight-delivery-cycle job that arbitrates ``insight_candidates``.
- ``butlers.jobs.secrets_lifecycle`` (bu-1lb5j) — a scheduled scan running
  inside the dashboard-api process, a process boundary away from any butler
  daemon's ``notify()`` closure. It cannot call ``notify()`` itself, so it
  composes the same gating (``get_suppressing_context_signal``,
  ``approvals_policy``) and dispatch (``switchboard.notification.deliver``)
  primitives ``notify()`` calls, rather than re-deriving their logic.
- ``butlers.jobs.home._send_notify`` (bu-tdd4k.3) — the Home butler's four
  deterministic monitoring crons (energy digest, device health check,
  environment report, maintenance schedule check). These run inside the Home
  daemon process and do have a live ``switchboard_client``, but the
  deterministic scheduler dispatches job handlers with a fixed
  ``(pool, job_args)`` signature, so the client is recovered via the
  ``get_current_switchboard_client()`` contextvar
  (``butlers.core.tool_call_capture``) rather than a widened handler
  signature. Composes the same gating primitives as the paths above, then
  dispatches through the ``deliver`` MCP tool (see ``_send_notify``'s
  docstring for why it goes through the MCP tool rather than an in-process
  ``deliver()`` call, even though ``deliver()``/``route()``'s
  ``butler_registry`` lookups are now schema-qualified — bu-tdd4k.2).
- ``butlers.jobs.decision_review`` (bu-ckkpz.4) — Switchboard's weekly
  decision-review digest and P1/deploy age-escalation crons. Run inside the
  Switchboard daemon process itself, so unlike Home's crons above they call
  ``switchboard.notification.deliver`` in-process (no MCP round-trip needed
  — mirrors ``secrets_lifecycle``'s composition, not Home's). See that
  module's docstring for why the underlying beads data is read from a
  bind-mounted JSONL file rather than a live query.

All five call :func:`record_attention_event` at each terminal decision point
so a notification is never silently dropped: it is recorded as delivered,
coalesced (folded into a digest), deferred (retryable later), or suppressed
(quiet hours / context bus), always with a machine-readable ``reason``.

This module is intentionally free of any notify()/insight-broker import so it
can be imported from either side without a circular-import risk.

Degraded-honesty contract: :func:`record_attention_event` is best-effort. A
ledger-write failure (e.g. the table is mid-migration) must never block or
fail the notification it is describing — it is logged at WARNING and
swallowed, mirroring the existing ``_emit_notification_event`` pattern in
``_notifications.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------

Source = Literal["notify", "insight"]
# "deferred" means a benign, chosen hold that resolves on its own (quiet
# hours, a coalescing window) -- the notification WILL be attempted again.
# "failed" (bu-hmdqz.3) means a genuine terminal failure at this attempt (no
# recipient configured, a transport/delivery error, an unexpected exception)
# -- distinct from "deferred" precisely because nothing automatically retries
# it unless the caller explicitly enqueues a retry envelope (see
# ``butlers.core.temporal.delivery_db.insert_deferred_notification``).
# Conflating the two lets an outage impersonate quiet-hours discipline in the
# ledger -- the exact failure mode bu-hmdqz.3 fixed for secrets_lifecycle.
Outcome = Literal["delivered", "coalesced", "deferred", "suppressed", "failed"]

VALID_SOURCES = frozenset({"notify", "insight"})
VALID_OUTCOMES = frozenset({"delivered", "coalesced", "deferred", "suppressed", "failed"})

# Priority range shared with RFC 0011's Priority Scoring Convention (1-100
# scale): 90-100 is "time-critical — action needed within 24-48 hours". The
# attention policy (quiet hours, context-bus dnd/sleeping) fails OPEN for any
# candidate/notification at or above this threshold — it always gets through,
# regardless of quiet hours or dnd/sleeping context signals. Only the routine
# (below-threshold) path is budgeted/suppressible.
URGENT_PRIORITY_THRESHOLD = 90

# notify()'s priority parameter is a 3-level enum (high/medium/low), not the
# insight pipeline's 1-100 scale. This mapping lets both paths log a single
# comparable priority_score to the ledger. "high" is pinned at the urgent
# threshold's floor so notify(priority="high") reads as urgent everywhere the
# ledger is queried, consistent with notify()'s existing "high always bypasses
# quiet hours" behaviour.
_PRIORITY_LABEL_SCORES: dict[str, int] = {
    "high": URGENT_PRIORITY_THRESHOLD,
    "medium": 50,
    "low": 20,
}


def normalize_priority(priority: str | int | None) -> tuple[str | None, int | None]:
    """Return ``(priority_label, priority_score)`` for ledger recording.

    Accepts either a notify()-style label (``"high"``/``"medium"``/``"low"``)
    or an insight-style integer (1-100). Unrecognised input degrades to
    ``(str(priority), None)`` rather than raising — the ledger is an
    observability surface and must never fail the call it is instrumenting.
    """
    if priority is None:
        return None, None
    if isinstance(priority, str) and priority in _PRIORITY_LABEL_SCORES:
        return priority, _PRIORITY_LABEL_SCORES[priority]
    if isinstance(priority, bool):
        # bool is a subclass of int; explicitly reject before the int branch.
        return str(priority), None
    if isinstance(priority, int):
        score = priority if 1 <= priority <= 100 else None
        return str(priority), score
    # Fallback: numeric string (e.g. an insight candidate's priority passed as str)
    if isinstance(priority, str):
        try:
            as_int = int(priority)
        except ValueError:
            return priority, None
        score = as_int if 1 <= as_int <= 100 else None
        return priority, score
    return str(priority), None


def is_priority_urgent(priority_score: int | None) -> bool:
    """Return True when *priority_score* meets the urgent bypass threshold."""
    return priority_score is not None and priority_score >= URGENT_PRIORITY_THRESHOLD


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


async def record_attention_event(
    pool: asyncpg.Pool | None,
    *,
    origin_butler: str,
    source: Source,
    outcome: Outcome,
    channel: str | None = None,
    intent: str | None = None,
    priority: str | int | None = None,
    dedup_key: str | None = None,
    reason: str | None = None,
    notification_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Record one attention-ledger row. Best-effort — never raises.

    Returns the new row's id (as a string) on success, or ``None`` if the
    write could not be completed (pool absent, table missing on an
    unmigrated DB, or any other error). Callers must not branch on the
    return value for delivery-affecting decisions — it exists purely for
    tests and for callers that want to correlate a ledger row with a
    downstream reference (e.g. logging).
    """
    if pool is None:
        return None
    if source not in VALID_SOURCES:
        logger.warning("record_attention_event: invalid source %r; dropping ledger row", source)
        return None
    if outcome not in VALID_OUTCOMES:
        logger.warning("record_attention_event: invalid outcome %r; dropping ledger row", outcome)
        return None

    priority_label, priority_score = normalize_priority(priority)

    # Pre-serialize + explicit ::jsonb cast (not a raw dict bind): portable
    # across both a pool with a registered dict->jsonb codec (production, via
    # Database.connect()) and a bare asyncpg pool with no custom codec (tests
    # that connect directly). Mirrors the existing pattern in
    # propose_insight_candidate() — never bind a raw dict without this cast.
    metadata_json = json.dumps(metadata) if metadata is not None else None

    try:
        row_id = await pool.fetchval(
            """
            INSERT INTO public.attention_ledger
                (origin_butler, source, channel, intent, priority_label,
                 priority_score, dedup_key, outcome, reason, notification_ref, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
            RETURNING id
            """,
            origin_butler,
            source,
            channel,
            intent,
            priority_label,
            priority_score,
            dedup_key,
            outcome,
            reason,
            notification_ref,
            metadata_json,
        )
    except Exception:
        # Never let ledger-write trouble affect the notification it describes.
        logger.warning(
            "record_attention_event: failed to record ledger row "
            "(origin_butler=%s source=%s outcome=%s)",
            origin_butler,
            source,
            outcome,
            exc_info=True,
        )
        return None
    return str(row_id) if row_id is not None else None


# ---------------------------------------------------------------------------
# Daily attention rollup (bu-tdd4k.5)
# ---------------------------------------------------------------------------
#
# The 60-minute engagement proxy (``check_and_update_engagement`` in the
# insight broker) is only meaningful when it counts OWNER-authored ingress —
# connector/automated traffic hitting the Switchboard must never impersonate
# owner engagement, or the disengagement ratchet
# (``check_total_disengagement_auto_off``) can never fire. This writer is
# called from the Switchboard pipeline's engagement gate, once identity
# resolution confirms the sender is the owner, so ``public.
# attention_daily_rollup`` accumulates a durable per-day owner-activity count
# that survives ``insight_engagement``'s 30-day purge (see
# ``roster/switchboard/tools/insight/broker.py``'s ``cleanup_old_rows`` for
# the companion rollup of insight delivered/engaged counts).


async def record_owner_ingress_rollup(
    pool: asyncpg.Pool | None,
    *,
    occurred_at: datetime | None = None,
) -> None:
    """Increment the UTC day's owner-ingress count in the daily rollup.

    Best-effort — a rollup-write failure must never block ingress routing.
    Callers must only invoke this for ingress already resolved to the owner;
    this function does not perform identity resolution itself.
    """
    if pool is None:
        return
    day = (occurred_at or datetime.now(UTC)).date()
    try:
        await pool.execute(
            """
            INSERT INTO public.attention_daily_rollup (day, owner_ingress_count)
            VALUES ($1, 1)
            ON CONFLICT (day) DO UPDATE
            SET owner_ingress_count = attention_daily_rollup.owner_ingress_count + 1,
                updated_at = now()
            """,
            day,
        )
    except Exception:
        logger.warning(
            "record_owner_ingress_rollup: failed to record rollup row for day=%s",
            day,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Reader helpers (notify-path counting / future dashboard use)
# ---------------------------------------------------------------------------


async def count_attention_events_since(
    pool: asyncpg.Pool | None,
    *,
    since: Any,
    outcome: Outcome | None = None,
) -> dict[str, int]:
    """Return outcome -> count for ledger rows with ``occurred_at >= since``.

    Always returns all four outcome keys (zero-filled), never a partial dict,
    so callers can render a stable summary even when a given outcome had no
    events in the window. Returns all-zero on any DB error (fail-open —
    this is an observability read, not a delivery gate).
    """
    zero_filled = {o: 0 for o in sorted(VALID_OUTCOMES)}
    if pool is None:
        return zero_filled

    try:
        if outcome is not None:
            rows = await pool.fetch(
                """
                SELECT outcome, COUNT(*) AS n
                FROM public.attention_ledger
                WHERE occurred_at >= $1 AND outcome = $2
                GROUP BY outcome
                """,
                since,
                outcome,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT outcome, COUNT(*) AS n
                FROM public.attention_ledger
                WHERE occurred_at >= $1
                GROUP BY outcome
                """,
                since,
            )
    except Exception:
        logger.warning(
            "count_attention_events_since: query failed; returning zero-filled",
            exc_info=True,
        )
        return zero_filled

    for row in rows:
        zero_filled[row["outcome"]] = int(row["n"])
    return zero_filled


# ---------------------------------------------------------------------------
# Context-bus consult (slice 2 — deterministic dnd/sleeping gating)
# ---------------------------------------------------------------------------

_SUPPRESSING_CONTEXT_SIGNALS = ("dnd", "sleeping")


async def get_suppressing_context_signal(pool: asyncpg.Pool | None) -> str | None:
    """Return the first active dnd/sleeping context-bus signal, else None.

    Deterministic, non-LLM read of ``public.user_context`` via the existing
    context-bus module (``butlers.context_bus.get_active_context``). Fails
    open (returns None) on any error, consistent with every other
    context-bus reader in this codebase (see
    ``spawner_context.fetch_situational_context_preamble``).
    """
    if pool is None:
        return None
    try:
        from butlers.context_bus import get_active_context

        signals = await get_active_context(pool)
    except Exception:
        logger.debug(
            "get_suppressing_context_signal: context bus unavailable; failing open",
            exc_info=True,
        )
        return None

    active_types = {s.signal_type for s in signals}
    for candidate in _SUPPRESSING_CONTEXT_SIGNALS:
        if candidate in active_types:
            return candidate
    return None
