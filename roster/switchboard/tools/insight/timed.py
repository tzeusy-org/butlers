"""Timed insight lane (bu-jv1moa.8 PR-A; RFC 0011 Amendment 8).

A timed candidate is delivered between deliver_after and expires_at or not at
all; dnd and sleeping always hold it; quiet hours do not hold a candidate whose
premise is a confirmed presence edge under 15 minutes old.

The timed sub-cycle sends each due candidate on its own (never in a digest),
after the same verbosity opt-out, expiry, premise, cooldown and budget rules
the digest uses, and through the shared claim in ``broker``:

- unknown premise holds (the digest would send it stamped "as of"; a timed
  nudge about a moment that may not have happened is not worth sending);
- a timed send counts against the owner's configured budget (all lanes,
  today UTC) and against ``TIMED_DAILY_CAP``;
- a claim whose holder died is swept: without ``attempt_started_at`` it never
  reached transport and takes the ordinary failure path; with it, the send may
  have happened, so it expires as ``delivery_ambiguous`` and is never resent.

Dark: ``TIMED_INSIGHTS_LIVE`` is off, so proposals are refused and the cycle is
a no-op. No roster schedules ``insight_timed_subcycle`` yet; activation adds
the cron and flips the flag in one owner-approved change.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import asyncpg

from butlers.core.approvals_policy import get_approvals_policy_quiet_hours, is_policy_quiet_now
from butlers.core.attention_ledger import record_attention_event
from butlers.telegram_api import terminal_telegram_metadata

from . import broker
from .premises import check_premise

logger = logging.getLogger(__name__)

#: Master switch. Off: proposals for the timed lane are refused and the
#: sub-cycle returns ``timed_lane_dark`` without reading or writing anything.
TIMED_INSIGHTS_LIVE = False

#: At most this many timed sends per UTC day, inside the configured budget.
TIMED_DAILY_CAP = 6

#: A presence edge confirmed less than this long ago lets a timed candidate
#: through quiet hours (never through dnd or sleeping).
PRESENCE_EDGE_FRESHNESS = timedelta(minutes=15)

#: Premise probes whose truth is a confirmed owner presence edge. Each is
#: registered in ``premises.PREMISE_PROBES`` by its producer (the Home
#: departure card, ``butlers.jobs.home_departure``).
PRESENCE_EDGE_PROBES: frozenset[tuple[str, str]] = frozenset({("home", "departure_still_open")})

#: A timed claim older than this is swept (see the module docstring).
TIMED_STALE_CLAIM_AFTER = timedelta(minutes=10)

_ALWAYS_HOLD = ("dnd", "sleeping")


def fresh_presence_edge(premise: dict[str, Any] | None, *, now: datetime) -> bool:
    """True when ``premise`` names a presence-edge probe confirmed under 15 minutes ago."""
    if not premise or (premise.get("butler"), premise.get("probe")) not in PRESENCE_EDGE_PROBES:
        return False
    raw = (premise.get("args") or {}).get("confirmed_at")
    if not isinstance(raw, str):
        return False
    try:
        confirmed_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if confirmed_at.tzinfo is None:
        return False
    return timedelta(0) <= now - confirmed_at < PRESENCE_EDGE_FRESHNESS


def _context_hold(signals: list[Any], *, now: datetime) -> str | None:
    """The context signal holding timed delivery: dnd/sleeping always, others within max hold."""
    for signal in signals:
        if signal.signal_type in _ALWAYS_HOLD:
            return signal.signal_type
    for signal_type in broker._CONTEXT_SUPPRESSING_SIGNALS:
        for signal in signals:
            if (
                signal.signal_type == signal_type
                and now - signal.set_at < broker._CONTEXT_MAX_HOLD[signal_type]
            ):
                return signal_type
    return None


def _count(status: str) -> int:
    return int(str(status).rsplit(" ", 1)[-1] or 0)


async def _block(pool: asyncpg.Pool, candidate_id: Any, reason: str) -> None:
    await pool.execute(
        """
        UPDATE insight_candidates
        SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('blocked_by', $2::text)
        WHERE id = $1 AND status = 'pending' AND claim_id IS NULL
        """,
        candidate_id,
        reason,
    )


async def _expire_ambiguous(
    pool: asyncpg.Pool, candidate_id: Any, claim_id: Any, *, now: datetime, cause: str
) -> bool:
    row = await pool.fetchrow(
        """
        UPDATE insight_candidates
        SET status = 'expired',
            metadata = COALESCE(metadata, '{}'::jsonb)
                || jsonb_build_object('delivery_ambiguous', true,
                                      'ambiguous_cause', $3::text,
                                      'ambiguous_at', $4::text)
        WHERE id = $1 AND claim_id = $2 AND status = 'pending'
        RETURNING origin_butler, priority, dedup_key, channel
        """,
        candidate_id,
        claim_id,
        cause,
        now.isoformat(),
    )
    if row is None:
        return False
    await record_attention_event(
        pool,
        origin_butler=row["origin_butler"],
        source="insight",
        outcome="failed",
        channel=row["channel"],
        intent="insight",
        priority=row["priority"],
        dedup_key=row["dedup_key"],
        notification_ref=str(candidate_id),
        reason="delivery_ambiguous",
        metadata={"lane": "timed", "cause": cause, "retryable": False},
    )
    return True


async def _fail(pool: asyncpg.Pool, candidate: dict[str, Any], claim_id: Any, reason: str) -> None:
    """Ordinary failure: no send happened, so release the claim for a retry."""
    await pool.execute(
        """
        UPDATE insight_candidates
        SET delivery_attempt_count = delivery_attempt_count + 1,
            claim_id = NULL, claimed_at = NULL, attempt_started_at = NULL
        WHERE id = $1 AND claim_id = $2
        """,
        candidate["id"],
        claim_id,
    )
    status = await pool.fetchval(
        """
        UPDATE insight_candidates
        SET status = 'filtered',
            metadata = COALESCE(metadata, '{}'::jsonb)
                || jsonb_build_object('delivery_failure', true,
                                      'failed_attempts', delivery_attempt_count)
        WHERE id = $1 AND status = 'pending' AND claim_id IS NULL
          AND delivery_attempt_count >= 3
        RETURNING status
        """,
        candidate["id"],
    )
    await record_attention_event(
        pool,
        origin_butler=candidate["origin_butler"],
        source="insight",
        outcome="failed",
        channel=candidate.get("channel"),
        intent="insight",
        priority=candidate["priority"],
        dedup_key=candidate["dedup_key"],
        notification_ref=str(candidate["id"]),
        reason=reason,
        metadata={"lane": "timed", "retryable": status is None},
    )


async def _sweep_stale_claims(pool: asyncpg.Pool, *, now: datetime) -> tuple[int, int]:
    """Return ``(released, ambiguous)`` for timed claims whose holder died."""
    rows = await pool.fetch(
        """
        SELECT id, claim_id, attempt_started_at, origin_butler, priority, dedup_key, channel
        FROM insight_candidates
        WHERE lane = 'timed' AND status = 'pending' AND claim_id IS NOT NULL
          AND claimed_at <= $1
        """,
        now - TIMED_STALE_CLAIM_AFTER,
    )
    released = ambiguous = 0
    for row in rows:
        if row["attempt_started_at"] is not None:
            ambiguous += int(
                await _expire_ambiguous(
                    pool, row["id"], row["claim_id"], now=now, cause="stale_claim"
                )
            )
        else:
            await _fail(pool, dict(row), row["claim_id"], "stale_claim_before_transport")
            released += 1
    return released, ambiguous


async def _budget_refusal(
    conn: asyncpg.Connection, *, configured_budget: int, now: datetime
) -> str | None:
    if await broker.sends_today(conn, now=now) >= configured_budget:
        return "budget"
    if await broker.sends_today(conn, now=now, lane="timed") >= TIMED_DAILY_CAP:
        return "budget"
    return None


async def _withdraw(pool: asyncpg.Pool, candidate: dict[str, Any], *, now: datetime) -> bool:
    updated = await pool.execute(
        """
        UPDATE insight_candidates
        SET status = 'withdrawn',
            metadata = COALESCE(metadata, '{}'::jsonb)
                || jsonb_build_object('withdrawn_at', $2::text, 'withdrawn_reason', 'premise_false')
        WHERE id = $1 AND status = 'pending' AND claim_id IS NULL
        """,
        candidate["id"],
        now.isoformat(),
    )
    if _count(updated) != 1:
        return False
    await record_attention_event(
        pool,
        origin_butler=candidate["origin_butler"],
        source="insight",
        outcome="withdrawn",
        channel=candidate.get("channel"),
        intent="insight",
        priority=candidate["priority"],
        dedup_key=candidate["dedup_key"],
        notification_ref=str(candidate["id"]),
        reason="premise_false",
    )
    return True


async def _settle_delivered(
    pool: asyncpg.Pool,
    candidate: dict[str, Any],
    claim_id: uuid.UUID,
    *,
    now: datetime,
    notify_result: Any,
    message: str,
) -> None:
    async with pool.acquire() as proxy, proxy.transaction():
        conn = cast(asyncpg.Connection, proxy)
        await conn.execute(
            """
            UPDATE insight_candidates
            SET status = 'delivered', delivered_at = $2, delivery_attempt_count = 0,
                delivery_ref = $4::jsonb
            WHERE id = $1 AND claim_id = $3 AND status = 'pending'
            """,
            candidate["id"],
            now,
            claim_id,
            broker._build_delivery_ref(
                notify_result,
                channel=candidate.get("channel"),
                standalone=True,
                digest_line_index=None,
                delivered_at=now,
                line_text=candidate["message"],
                delivery_text=message,
            ),
        )
        await broker.record_cooldowns(conn, [candidate], now=now)
        await broker.record_engagement_rows(conn, [candidate], delivered_at=now)
    await record_attention_event(
        pool,
        origin_butler=candidate["origin_butler"],
        source="insight",
        outcome="delivered",
        channel=candidate.get("channel"),
        intent="insight",
        priority=candidate["priority"],
        dedup_key=candidate["dedup_key"],
        notification_ref=str(candidate["id"]),
        metadata={"lane": "timed"},
    )


async def timed_delivery_cycle(
    pool: asyncpg.Pool,
    *,
    notify_fn: Any | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Send every due timed candidate the rules allow, each on its own.

    Returns ``skipped``/``reason`` on an early exit, plus ``expired``,
    ``withdrawn``, ``released`` and ``ambiguous`` counts, ``delivered`` ids and
    ``held`` (candidate id -> reason) for candidates that stay pending.
    """
    result: dict[str, Any] = {
        "skipped": False,
        "expired": 0,
        "withdrawn": 0,
        "released": 0,
        "ambiguous": 0,
        "delivered": [],
        "held": {},
    }
    if not TIMED_INSIGHTS_LIVE:
        result.update(skipped=True, reason="timed_lane_dark")
        return result
    if notify_fn is None:
        result.update(skipped=True, reason="notify_fn_missing")
        return result
    if now is None:
        now = datetime.now(UTC)

    settings = await broker.get_insight_settings(pool)
    configured_budget = broker._get_configured_budget(settings)
    if configured_budget == 0:
        await pool.execute(
            """
            UPDATE insight_candidates SET status = 'filtered'
            WHERE lane = 'timed' AND status = 'pending' AND claim_id IS NULL
            """
        )
        result.update(skipped=True, reason="verbosity_off")
        return result

    result["released"], result["ambiguous"] = await _sweep_stale_claims(pool, now=now)
    result["expired"] = await broker.expire_candidates(pool, now=now)

    try:
        from butlers.context_bus import get_active_context

        policy = await get_approvals_policy_quiet_hours(pool)
        hold = _context_hold(await get_active_context(pool), now=now)
    except Exception:
        result.update(skipped=True, reason="attention_store_unavailable")
        return result

    rows = await pool.fetch(
        """
        SELECT id, origin_butler, priority, category, dedup_key, cooldown_days,
               expires_at, message, channel, metadata, premise, created_at
        FROM insight_candidates
        WHERE lane = 'timed' AND status = 'pending' AND claim_id IS NULL
          AND deliver_after <= $1 AND expires_at > $1
        ORDER BY priority DESC, deliver_after ASC, created_at ASC
        """,
        now,
    )
    if not rows:
        return result
    if hold is not None:
        for row in rows:
            await _block(pool, row["id"], "held_by")
            result["held"][str(row["id"])] = f"context_bus:{hold}"
        await record_attention_event(
            pool,
            origin_butler="switchboard",
            source="insight",
            outcome="suppressed",
            intent="insight",
            reason=f"context_bus:{hold}",
            metadata={"lane": "timed", "held_by": hold},
        )
        return result

    eligible = set(await broker.filter_by_cooldown(pool, [str(r["id"]) for r in rows], now=now))
    started = time.monotonic()
    for row in rows:
        candidate = dict(row)
        candidate_id = str(candidate["id"])
        if candidate_id not in eligible:
            continue
        # Sends are sequential: judge each candidate's window, quiet hours and
        # edge freshness at its own send instant, not at the cycle's start.
        send_now = now + timedelta(seconds=time.monotonic() - started)
        premise = broker._json_object(candidate.get("premise"))
        verdict = await check_premise(pool, premise)
        if verdict is False:
            result["withdrawn"] += int(await _withdraw(pool, candidate, now=send_now))
            continue
        if verdict is None:
            await _block(pool, candidate["id"], "premise_unknown")
            result["held"][candidate_id] = "premise_unknown"
            continue
        send_now = now + timedelta(seconds=time.monotonic() - started)
        quiet = is_policy_quiet_now(policy, now=send_now)
        if quiet and not fresh_presence_edge(premise, now=send_now):
            await _block(pool, candidate["id"], "held_by")
            result["held"][candidate_id] = "quiet_hours"
            continue

        async def budget_check(conn: asyncpg.Connection) -> str | None:
            return await _budget_refusal(conn, configured_budget=configured_budget, now=now)

        claim_id, refusal = await broker.claim_for_delivery(
            pool,
            [candidate_id],
            now=send_now,
            budget_check=budget_check,
            unexpired_at=send_now,
        )
        if claim_id is None:
            if refusal == "budget":
                await _block(pool, candidate["id"], "budget")
                result["held"][candidate_id] = "budget"
            continue
        await broker.mark_attempt_started(pool, claim_id, send_now)

        message = broker._format_standalone(candidate)
        metadata = {
            "insight_count": 1,
            "insight_ids": [candidate_id],
            "intent": "insight",
            "lane": "timed",
            "channel": candidate.get("channel"),
            "feedback_actions": [
                {
                    "insight_id": candidate_id,
                    "useful": f"/api/switchboard/insights/{candidate_id}/useful",
                    "snooze": f"/api/switchboard/insights/{candidate_id}/snooze",
                    "mute": f"/api/switchboard/insights/{candidate_id}/mute",
                }
            ],
        }
        try:
            notify_result = await notify_fn(message, metadata)
        except Exception as exc:
            # The transport may have sent before raising: never resend.
            logger.exception("insight-timed-cycle: notify raised for %s", candidate_id)
            result["ambiguous"] += int(
                await _expire_ambiguous(
                    pool, candidate["id"], claim_id, now=now, cause=type(exc).__name__
                )
            )
            continue
        if terminal_telegram_metadata(notify_result):
            result["ambiguous"] += int(
                await _expire_ambiguous(
                    pool, candidate["id"], claim_id, now=now, cause="telegram_partial_delivery"
                )
            )
            continue
        if isinstance(notify_result, dict) and notify_result.get("status") == "error":
            # An explicit refusal from the transport: nothing reached the owner.
            await _fail(
                pool,
                candidate,
                claim_id,
                f"delivery_error:{notify_result.get('error', 'unknown')}",
            )
            continue
        await _settle_delivered(
            pool, candidate, claim_id, now=send_now, notify_result=notify_result, message=message
        )
        result["delivered"].append(candidate_id)
    return result
