"""Park and flush of owner-default notices refused by an endpoint custody hold.

bu-s11n0s.1 S5b, REQ-endpoint-custody-holds-007 (park path) and -010 (automatic recovery).

An owner-default notice (no channel, recipient, entity, attachment or reply prompt) whose every
configured owner target is under a custody hold is parked in the butler's own
``deferred_notifications`` table instead of only being refused. The row is due at once and
carries a ``custody_park`` section naming the held targets (the content-blind
``(predicate, object)`` pairs the fence already uses; no credential, and no episode, since the
public ``custody_target_held`` read answers only a boolean).

The row needs no release-time write. ``deferred_notifications`` lives in each butler's schema,
which the public custody engine (core_347/348) cannot reach, so the flush asks the same
content-blind read instead: while every target is held (or custody cannot be read) the row stays
pending and nothing is sent; once any target is clear it is sent once through the stored
envelope, with the held-while-lost marker, by the owning pipeline, whose own egress fence
refuses a target that was re-held in the meantime. A row the flush expires (24 hours past
``deliver_at``, the ordinary pass rule) is never sent. Only this envelope shape is ever parked,
so an ambiguous, approval, voice, expired, superseded or historical item cannot reach this path;
an envelope that is not the exact generic shape is cancelled, never sent.

Single send: each row is flushed inside a transaction holding ``FOR UPDATE SKIP LOCKED`` on it
across the revalidation and the send, so a concurrent flush skips a row in flight, and a row is
marked ``delivered`` in the same transaction as the send returns.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any

from butlers.core.custody_egress import CustodyEgressRefused, custody_targets_held
from butlers.telegram_api import terminal_telegram_metadata

logger = logging.getLogger("butlers.core.custody_park")

PARK_KEY = "custody_park"
HELD_WHILE_LOST_MARKER = "[Held while your device was lost] "
_PARKABLE_INTENTS = frozenset({"send", "insight"})
_MAX_TARGETS = 4


def build_parked_envelope(
    *,
    origin_butler: str,
    intent: str,
    message: str,
    targets: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    """The ``notify.v1`` envelope of a parked owner-default notice."""
    return {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {"intent": intent, "channel": "telegram", "message": message},
        PARK_KEY: {"reason": "endpoint_held", "targets": [list(t) for t in targets]},
    }


def parked_targets(envelope: Any) -> list[tuple[str, str]] | None:
    """The held targets of an exactly generic parked envelope, or ``None`` when it is not one."""
    if not isinstance(envelope, dict):
        return None
    section = envelope.get(PARK_KEY)
    delivery = envelope.get("delivery")
    if not isinstance(section, dict) or not isinstance(delivery, dict):
        return None
    if delivery.get("intent") not in _PARKABLE_INTENTS:
        return None
    if set(delivery) - {"intent", "channel", "message"}:
        return None  # a recipient, attachment, option or prompt: never generic
    if set(envelope) - {"schema_version", "origin_butler", "delivery", PARK_KEY}:
        return None  # a dossier, context or other control-plane field
    raw = section.get("targets")
    if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX_TARGETS:
        return None
    targets: list[tuple[str, str]] = []
    for item in raw:
        if (
            not isinstance(item, list | tuple)
            or len(item) != 2
            or not all(isinstance(part, str) and part.strip() for part in item)
        ):
            return None
        targets.append((item[0], item[1]))
    return targets


async def park_owner_default_notice(
    pool: Any,
    *,
    butler_name: str,
    intent: str,
    priority: str,
    message: Any,
    targets: tuple[tuple[str, str], ...],
    now: datetime,
) -> str | None:
    """Park a refused owner-default notice, due at once. ``None`` when it is not parkable."""
    if pool is None or intent not in _PARKABLE_INTENTS:
        return None
    if not isinstance(message, str) or not message.strip() or not targets:
        return None
    from butlers.core.temporal.delivery_db import insert_deferred_notification

    envelope = build_parked_envelope(
        origin_butler=butler_name, intent=intent, message=message, targets=targets
    )
    if parked_targets(envelope) is None:
        return None
    try:
        return await insert_deferred_notification(
            pool,
            butler_name=butler_name,
            channel="telegram",
            message=message,
            priority=priority,
            envelope=envelope,
            deliver_at=now,
            deferred_at=now,
        )
    except Exception:
        logger.exception("Failed to park a custody-held owner notice; refusing it instead")
        return None


async def _first_clear(pool: Any, targets: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The first owner target (Telegram, then email) provably unheld; unreadable is not clear."""
    for target in targets:
        try:
            if not await custody_targets_held(pool, [target]):
                return target
        except CustodyEgressRefused:
            return None
    return None


def _marked(envelope: dict[str, Any], target: tuple[str, str]) -> dict[str, Any]:
    """The envelope the owning pipeline receives: the park section dropped, the channel of the
    clear target, the held-while-lost marker prefixed. No recipient: the owner default resolves
    again, behind the owning transport's own egress fence."""
    sent = copy.deepcopy(envelope)
    sent.pop(PARK_KEY, None)
    delivery = sent["delivery"]
    delivery["channel"] = "email" if target[0] == "has-email" else "telegram"
    delivery["message"] = HELD_WHILE_LOST_MARKER + str(delivery.get("message") or "")
    return sent


async def flush_custody_parked(
    pool: Any, now: datetime, rows: list[dict[str, Any]], notify_fn: Any
) -> int:
    """Flush due parked rows once each; returns how many were delivered."""
    from butlers.core.attention_ledger import record_attention_event

    delivered = 0
    for row in rows:
        row_id = row["id"]
        envelope = row["envelope"]
        targets = parked_targets(envelope)
        if targets is None:
            await pool.execute(
                "UPDATE deferred_notifications SET status = 'cancelled' "
                "WHERE id = $1 AND status = 'pending'",
                row_id,
            )
            logger.warning("Cancelled a custody-parked notice that is not the generic shape")
            continue
        try:
            async with pool.acquire() as conn, conn.transaction():
                locked = await conn.fetchval(
                    "SELECT id FROM deferred_notifications "
                    "WHERE id = $1 AND status = 'pending' AND deliver_at <= $2 "
                    "FOR UPDATE SKIP LOCKED",
                    row_id,
                    now,
                )
                if locked is None:
                    continue  # in flight elsewhere, or no longer pending
                clear = await _first_clear(pool, targets)
                if clear is None:
                    continue  # still held, re-held, or unreadable: stays parked
                outcome = await notify_fn(_marked(envelope, clear))
                metadata = terminal_telegram_metadata(outcome)
                await conn.execute(
                    "UPDATE deferred_notifications SET status = $3, delivered_at = $2 "
                    "WHERE id = $1 AND status = 'pending'",
                    row_id,
                    now,
                    "cancelled" if metadata else "delivered",
                )
        except Exception:
            logger.exception("Failed to flush custody-parked notice %s", row_id)
            continue
        if metadata:
            await record_attention_event(
                pool,
                origin_butler=row["butler_name"],
                source="notify",
                outcome="failed",
                channel=row["channel"],
                intent="send",
                priority=row["priority"],
                notification_ref=str(row_id),
                reason="telegram_delivery_uncertain",
                metadata=metadata,
            )
            continue
        delivered += 1
        await record_attention_event(
            pool,
            origin_butler=row["butler_name"],
            source="notify",
            outcome="delivered",
            channel=row["channel"],
            intent="send",
            priority=row["priority"],
            notification_ref=str(row_id),
            reason="custody_released",
        )
    return delivered
