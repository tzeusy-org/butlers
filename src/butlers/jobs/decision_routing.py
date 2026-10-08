"""Route open decision beads to the owner as one-tap Telegram prompts.

bu-ckkpz.3 (``REQ-owner-decision-desk-003`` / ``-004``, OpenSpec change
``owner-decision-desk-write-bridge``). A deterministic Switchboard schedule job:
fixed templates, no model.

Each run offers eligible decisions (open, structured details available, no
prompt yet, no live intent; escalated first, then oldest) through the same
owner-attention gates the decision-review jobs use, in order:

1. quiet hours and the context bus (``decision_review._check_suppression``);
2. a budget of :data:`DAILY_PROMPT_BUDGET` prompts per rolling 24 hours.

A held candidate is recorded ``deferred`` in the attention ledger, at most
once per bead per :data:`_DEFERRAL_LEDGER_WINDOW`, and stays eligible.

Delivery is at most once per bead. The ``switchboard.decision_prompts`` row is
reserved *before* the send and is only ever re-reserved after a proven
``not_attempted`` outcome; ``uncertain``, ``rejected`` and ``delivered`` are
terminal (REQ-runtime-attention-outbox-002's rule, applied to prompts). The
``dsk1`` callback tokens are bound to the reservation's ``created_at``.

Routing is off unless ``BUTLERS_DECISION_ROUTING_ENABLED=1``: switching it on
sends real Telegram messages to the owner, so each deployment opts in.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.approval_callbacks import APPROVAL_CALLBACK_SECRET_KEY
from butlers.core.attention_ledger import attention_event_recorded_since, record_attention_event
from butlers.core.decision_callbacks import MAX_DECISION_OPTIONS, mint_decision_callback_token
from butlers.core.tool_call_capture import get_current_approval_push_runtime
from butlers.credential_store import resolve_owner_telegram_recipient
from butlers.jobs.decision_review import (
    DecisionBead,
    DecisionDigest,
    _check_suppression,
    _format_age,
    compute_decision_digest,
)

logger = logging.getLogger(__name__)

ROUTING_ENABLED_ENV = "BUTLERS_DECISION_ROUTING_ENABLED"
DAILY_PROMPT_BUDGET = 3
_BUDGET_WINDOW = timedelta(hours=24)
_DEFERRAL_LEDGER_WINDOW = timedelta(hours=12)
_IN_FLIGHT_TIMEOUT = timedelta(minutes=10)
_LABEL_CHARS = 64
_ACTOR = "decision_routing"
_PRIORITY = "medium"


@dataclass(frozen=True)
class _Reservation:
    prompt_id: uuid.UUID
    created_at: datetime


def routing_enabled() -> bool:
    return os.environ.get(ROUTING_ENABLED_ENV, "").strip() == "1"


def _dedup_key(bead_id: str) -> str:
    return f"decision_prompt:{bead_id}"


def _ordered_candidates(
    digest: DecisionDigest, *, prompted: set[str], decided: set[str]
) -> list[DecisionBead]:
    escalated = {hit.decision_id for hit in digest.escalations}
    eligible = [
        bead
        for bead in digest.open_decisions
        if bead.structured_details_available
        and bead.options
        and len(bead.options) <= MAX_DECISION_OPTIONS
        and bead.id not in prompted
        and bead.id not in decided
    ]
    # open_decisions is already oldest-first; a stable sort keeps that order
    # within each group.
    return sorted(eligible, key=lambda bead: bead.id not in escalated)


def _button_label(index: int, option: str) -> str:
    label = f"{index + 1}. {option}"
    return label if len(label) <= _LABEL_CHARS else label[: _LABEL_CHARS - 1] + "…"


def compose_prompt_message(bead: DecisionBead) -> str:
    """The fixed, reviewable prompt text. Options are listed in full."""
    lines = [f"Decision needed: {bead.title}", f"{bead.id}, open {_format_age(bead.age)}"]
    if bead.due_at is not None:
        lines[-1] += f", due {bead.due_at.astimezone(UTC).date().isoformat()}"
    lines.append("")
    lines.extend(f"{index + 1}. {option}" for index, option in enumerate(bead.options or ()))
    lines.append("")
    lines.append(f"Default if unanswered: {bead.default}")
    lines.append("Tap an option to record your choice.")
    return "\n".join(lines)


def build_decision_request_envelope(
    *,
    bead: DecisionBead,
    prompt_id: uuid.UUID,
    created_at: datetime,
    recipient: str,
    callback_secret: str,
    dashboard_base_url: str,
) -> dict[str, Any]:
    dashboard_url = f"{dashboard_base_url.rstrip('/')}/decisions?bead={bead.id}"
    choices = [
        {
            "verb": "choose",
            "label": _button_label(index, option),
            "callback_token": mint_decision_callback_token(
                prompt_id=prompt_id,
                option_index=index,
                created_at=created_at,
                secret=callback_secret,
            ),
            "dashboard_url": dashboard_url,
        }
        for index, option in enumerate(bead.options or ())
    ]
    return {
        "schema_version": "notify.v1",
        "origin_butler": "switchboard",
        "delivery": {
            "intent": "decision_request",
            "channel": "telegram",
            "message": compose_prompt_message(bead),
            "recipient": recipient,
        },
        "actions": [*choices, {"verb": "open_dashboard", "dashboard_url": dashboard_url}],
    }


async def _expire_in_flight(pool: asyncpg.Pool, *, now: datetime) -> None:
    """A reservation with no outcome after the timeout may have been sent: uncertain."""
    await pool.execute(
        """
        UPDATE switchboard.decision_prompts
        SET delivery_outcome = 'uncertain', updated_at = $1
        WHERE delivery_outcome IS NULL AND updated_at < $2
        """,
        now,
        now - _IN_FLIGHT_TIMEOUT,
    )


async def _budget_used(pool: asyncpg.Pool, *, now: datetime) -> int:
    return int(
        await pool.fetchval(
            """
            SELECT COUNT(*) FROM switchboard.decision_prompts
            WHERE (delivery_outcome IS NULL OR delivery_outcome IN ('delivered', 'uncertain'))
              AND updated_at >= $1
            """,
            now - _BUDGET_WINDOW,
        )
        or 0
    )


async def _reserve(pool: asyncpg.Pool, bead: DecisionBead, *, now: datetime) -> _Reservation | None:
    """Claim the bead's single prompt row; ``None`` when it is not re-sendable."""
    row = await pool.fetchrow(
        """
        INSERT INTO switchboard.decision_prompts
            (bead_id, options, default_option, created_at, updated_at)
        VALUES ($1, $2::jsonb, $3, $4, $4)
        ON CONFLICT (bead_id) DO UPDATE
            SET options = EXCLUDED.options,
                default_option = EXCLUDED.default_option,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                delivery_outcome = NULL
            WHERE decision_prompts.delivery_outcome = 'not_attempted'
        RETURNING id, created_at
        """,
        bead.id,
        json.dumps(list(bead.options or ())),
        bead.default,
        now,
    )
    return _Reservation(row["id"], row["created_at"]) if row is not None else None


async def _settle(pool: asyncpg.Pool, prompt_id: uuid.UUID, outcome: str, *, now: datetime) -> None:
    await pool.execute(
        """
        UPDATE switchboard.decision_prompts
        SET delivery_outcome = $2,
            delivered_at = CASE WHEN $2 = 'delivered' THEN $3 ELSE delivered_at END,
            updated_at = $3
        WHERE id = $1
        """,
        prompt_id,
        outcome,
        now,
    )


async def _ledger(
    pool: asyncpg.Pool, bead_id: str, outcome: str, reason: str | None = None, **extra: Any
) -> None:
    await record_attention_event(
        pool,
        origin_butler=_ACTOR,
        source="notify",
        outcome=outcome,  # type: ignore[arg-type]
        channel="telegram",
        intent="decision_request",
        priority=_PRIORITY,
        dedup_key=_dedup_key(bead_id),
        reason=reason,
        **extra,
    )


async def _defer(pool: asyncpg.Pool, bead_id: str, reason: str, *, now: datetime) -> None:
    if await attention_event_recorded_since(
        pool, dedup_key=_dedup_key(bead_id), since=now - _DEFERRAL_LEDGER_WINDOW
    ):
        return
    await _ledger(pool, bead_id, "deferred", reason)


def _prompt_outcome(result: dict[str, Any]) -> tuple[str, str | None]:
    """Map a ``deliver()`` result to ``(prompt outcome, ledger failure reason)``."""
    if result.get("status") == "sent":
        return "delivered", None
    transport = result.get("transport")
    outcome = transport.get("outcome") if isinstance(transport, dict) else None
    detail = transport.get("error_detail") if isinstance(transport, dict) else None
    if outcome == "not_attempted":
        return "not_attempted", f"not_attempted:{detail or 'unknown'}"
    if outcome == "rejected":
        return "rejected", f"rejected:{detail or 'unknown'}"
    if transport is None and "notification_id" not in result:
        # deliver() refused the envelope before routing (validation, origin
        # mismatch): provably unsent, and resending the same envelope would
        # fail the same way, so it is terminal rather than retried.
        return "rejected", "rejected:envelope_invalid"
    return "uncertain", "delivery_uncertain"


async def _callback_secret() -> str | None:
    runtime = get_current_approval_push_runtime()
    store = getattr(runtime, "credential_store", None)
    if store is None:
        return None
    return await store.resolve(APPROVAL_CALLBACK_SECRET_KEY, env_fallback=False)


def _dashboard_base_url() -> str:
    from butlers.modules.approvals.notifications import dashboard_base_url

    return dashboard_base_url()


async def _offer(pool: asyncpg.Pool, bead: DecisionBead, *, now: datetime) -> str:
    reservation = await _reserve(pool, bead, now=now)
    if reservation is None:
        return "duplicate"
    try:
        recipient = await resolve_owner_telegram_recipient(pool)
        secret = await _callback_secret() if recipient else None
        if not recipient or not secret:
            reason = "no_recipient_configured" if not recipient else "callback_secret_unavailable"
            await _settle(pool, reservation.prompt_id, "not_attempted", now=now)
            await _ledger(pool, bead.id, "failed", reason)
            return "not_attempted"

        envelope = build_decision_request_envelope(
            bead=bead,
            prompt_id=reservation.prompt_id,
            created_at=reservation.created_at,
            recipient=recipient,
            callback_secret=secret,
            dashboard_base_url=_dashboard_base_url(),
        )
        # Local import: roster modules are not always importable at collection
        # time (mirrors decision_review._deliver).
        from butlers.tools.switchboard.notification.deliver import deliver

        result = await deliver(pool, source_butler="switchboard", notify_request=envelope)
    except Exception:  # noqa: BLE001 - an exception after reservation may have sent
        logger.warning("decision_routing: prompt for %s failed", bead.id, exc_info=True)
        await _settle(pool, reservation.prompt_id, "uncertain", now=now)
        await _ledger(pool, bead.id, "failed", "delivery_uncertain")
        return "uncertain"

    outcome, reason = _prompt_outcome(result)
    await _settle(pool, reservation.prompt_id, outcome, now=now)
    await _ledger(
        pool,
        bead.id,
        "delivered" if outcome == "delivered" else "failed",
        reason,
        notification_ref=str(result["notification_id"]) if result.get("notification_id") else None,
    )
    return outcome


async def run_decision_routing(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    _now: datetime | None = None,
    _digest: DecisionDigest | None = None,
) -> dict[str, Any]:
    """One routing pass. ``_now``/``_digest`` are test-only overrides."""
    del job_args
    if not routing_enabled():
        return {"enabled": False}

    now = _now or datetime.now(UTC)
    digest = _digest if _digest is not None else compute_decision_digest(now=_now)
    if not digest.available:
        return {"enabled": True, "available": False, "reason": digest.unavailable_reason}

    await _expire_in_flight(pool, now=now)
    bead_ids = [bead.id for bead in digest.open_decisions]
    prompted = {
        row["bead_id"]
        for row in await pool.fetch(
            "SELECT bead_id FROM switchboard.decision_prompts "
            "WHERE bead_id = ANY($1::text[]) "
            "AND (delivery_outcome IS NULL OR delivery_outcome <> 'not_attempted')",
            bead_ids,
        )
    }
    decided = {
        row["bead_id"]
        for row in await pool.fetch(
            "SELECT bead_id FROM switchboard.decision_intents "
            "WHERE bead_id = ANY($1::text[]) AND status IN ('pending', 'applying', 'applied')",
            bead_ids,
        )
    }
    candidates = _ordered_candidates(digest, prompted=prompted, decided=decided)
    summary: dict[str, Any] = {
        "enabled": True,
        "available": True,
        "candidates": len(candidates),
        "outcomes": {},
    }
    if not candidates:
        return summary

    suppress_reason = await _check_suppression(pool, now=now)
    if suppress_reason is not None:
        for bead in candidates:
            await _defer(pool, bead.id, suppress_reason, now=now)
        summary["deferred"] = suppress_reason
        return summary

    remaining = DAILY_PROMPT_BUDGET - await _budget_used(pool, now=now)
    outcomes: dict[str, int] = summary["outcomes"]
    for bead in candidates:
        if remaining <= 0:
            await _defer(pool, bead.id, "budget_exhausted", now=now)
            outcomes["budget_exhausted"] = outcomes.get("budget_exhausted", 0) + 1
            continue
        outcome = await _offer(pool, bead, now=now)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome in {"delivered", "uncertain"}:
            remaining -= 1
    return summary


__all__ = [
    "DAILY_PROMPT_BUDGET",
    "ROUTING_ENABLED_ENV",
    "build_decision_request_envelope",
    "compose_prompt_message",
    "routing_enabled",
    "run_decision_routing",
]
