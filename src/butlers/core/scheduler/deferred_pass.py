"""Tick pass 4: deferred-notification flush."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from butlers.telegram_api import terminal_telegram_metadata
from butlers.telegram_text import PartialDeliveryLatch

logger = logging.getLogger("butlers.core.scheduler")


_PARTIAL_DEFERRED = PartialDeliveryLatch()


def _format_deferred_digest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compose >1 due deferred-notification rows into one notify.v1 envelope.

    Same-window coalescing (bu-o8233, JARVIS pursuit move 8 slice 4): rows
    passed here already share one (channel, recipient) delivery target (see
    ``_group_due_deferred_notifications``) and all landed due in the same
    flush tick — the "window" this coalesces. Mirrors the insight broker's
    digest formatting (``roster/switchboard/tools/insight/broker.py``'s
    ``_format_digest``) so a composed message reads consistently regardless
    of which pipeline produced it.

    Every row's stored envelope is used verbatim for everything except the
    composed message body — this only changes *how many pings* the owner
    gets for the window, not the delivery mechanics (recipient/channel
    resolution still happens exactly as it would for a single send).
    """
    count = len(rows)
    lines = [f"Batched updates ({count}):"]
    for i, row in enumerate(rows, start=1):
        delivery = (row["envelope"] or {}).get("delivery", {}) or {}
        message = delivery.get("message") or row.get("message") or ""
        butler = row.get("butler_name") or ""
        label = f"[{butler.capitalize()}]" if butler else ""
        lines.append(f"{i}. {label} {message}".strip())
    composed_message = "\n".join(lines)

    first_envelope = rows[0]["envelope"] or {}
    first_delivery = first_envelope.get("delivery", {}) or {}

    delivery: dict[str, Any] = {
        "intent": "send",
        "channel": first_delivery.get("channel") or rows[0]["channel"],
        "message": composed_message,
    }
    recipient = first_delivery.get("recipient")
    if recipient is not None:
        delivery["recipient"] = recipient

    return {
        "schema_version": "notify.v1",
        "origin_butler": first_envelope.get("origin_butler") or rows[0].get("butler_name"),
        "delivery": delivery,
    }


def _group_due_deferred_notifications(
    rows: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Group due deferred-notification rows by delivery target, order-preserving.

    Rows destined for the same (channel, recipient) pair are the "same
    window" this coalesces — they were all still queued at the moment this
    tick flushed, regardless of when each was originally deferred. A ``None``
    recipient (resolve the owner's default channel identifier) is its own
    group key, distinct from any explicit recipient, so an explicitly-targeted
    send is never silently folded into an owner-default digest or vice versa.

    A decision dossier is bound to one non-owner delivery decision. Do not
    fold such rows into a rewritten digest envelope: each must retain its own
    validated context for flush-time recipient-gate revalidation. The same is
    true for ``approval_request`` control-plane
    envelopes: coalescing would discard their callback tokens and inline
    keyboard payload.
    """
    groups: dict[tuple[str | None, str | None, str | None], list[dict[str, Any]]] = {}
    order: list[tuple[str | None, str | None, str | None]] = []
    for row in rows:
        envelope = row["envelope"] or {}
        delivery = envelope.get("delivery", {}) or {}
        control_plane_row_id = (
            str(row["id"])
            if (
                envelope.get("decision_dossier") is not None
                or delivery.get("intent") in {"approval_request"}
            )
            else None
        )
        key = (
            delivery.get("channel") or row["channel"],
            delivery.get("recipient"),
            control_plane_row_id,
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    return [groups[key] for key in order]


async def _tick_deferred_notification_pass(
    pool: asyncpg.Pool,
    now: datetime,
    *,
    notify_fn=None,
) -> int:
    """Flush deferred notifications: expire old ones and deliver due ones.

    - Marks pending notifications > 24h past deliver_at as expired.
    - Delivers pending notifications with deliver_at <= now via the standard
      notify pipeline (``notify_fn``), re-using the stored ``notify.v1``
      envelope rather than re-prompting the LLM spawner.
    - Same-window coalescing (bu-o8233): when more than one due notification
      targets the same (channel, recipient) pair, they are composed into ONE
      digest message and delivered via a single ``notify_fn`` call instead of
      one ping per queued item — the whole point of a butler opting into
      ``delivery_preferences.batch_low_priority``/``batch_delivery_time`` is
      defeated if the flush still sends one message per row. A target with
      exactly one due row is delivered unchanged (its stored envelope,
      verbatim) — solo-item behaviour and ledger semantics are unaffected.

    ``notify_fn`` must be an async callable that accepts a single positional
    argument — the ``notify.v1`` envelope dict — and raises on failure.
    Example signature::

        async def notify_fn(envelope: dict) -> None: ...

    When ``notify_fn`` is ``None`` (e.g., unit tests that don't need real
    delivery), due notifications are counted but left ``pending`` so they are
    retried on the next tick once a real ``notify_fn`` is wired in.

    Idempotency: a group's rows are only marked ``delivered`` after
    ``notify_fn`` returns successfully for that group; a failed group's rows
    stay ``pending`` for retry (uncomposed) on the next tick. Since the next
    tick's due-fetch only ever selects ``status = 'pending'`` rows, a
    successfully delivered row can never be re-sent — the row's own status
    transition is the guard, same as the pre-existing single-item path.

    Returns the number of underlying notifications delivered (a composed
    digest of N rows counts as N, not 1 — it reflects candidates consumed,
    not ``notify_fn`` calls made).
    """
    import json as _json

    from butlers.core.attention_ledger import record_attention_event

    async def terminal_partial(group: list[dict[str, Any]], metadata: dict[str, bool]) -> None:
        ids = [row["id"] for row in group]
        for row in group:
            _PARTIAL_DEFERRED.mark(pool, str(row["id"]), metadata)
        await pool.execute(
            "UPDATE deferred_notifications SET status = 'cancelled' "
            "WHERE id = ANY($1::uuid[]) AND status = 'pending'",
            ids,
        )
        for row in group:
            _PARTIAL_DEFERRED.clear(pool, str(row["id"]))
            await record_attention_event(
                pool,
                origin_butler=row["butler_name"],
                source="notify",
                outcome="failed",
                channel=row["channel"],
                intent="send",
                priority=row["priority"],
                notification_ref=str(row["id"]),
                reason=(
                    "telegram_partial_delivery"
                    if metadata.get("partial_delivery")
                    else "telegram_delivery_uncertain"
                ),
                metadata=metadata,
            )

    # Reconcile the exact old logical send even if its due/expiry window changed.
    # No fresh-row grouping or provider call happens before this terminal write.
    # A row a wake-recovery run has since reserved (any ``release_*`` status) belongs to
    # that run; it is skipped here and its latch entry released below.
    quarantined = _PARTIAL_DEFERRED.keys(pool)
    if quarantined:
        rows = await pool.fetch(
            "SELECT id, butler_name, channel, priority FROM deferred_notifications "
            "WHERE id = ANY($1::uuid[]) AND status NOT LIKE 'release\\_%'",
            quarantined,
        )
        for row in rows:
            await terminal_partial([dict(row)], _PARTIAL_DEFERRED.metadata(pool, str(row["id"])))
        # A deleted row cannot be re-admitted; release its process-local owner too.
        for key in quarantined:
            _PARTIAL_DEFERRED.clear(pool, key)

    # The ordinary flush owns plain ``pending`` rows only. A row a wake-recovery run has
    # reserved, committed or retained (any ``release_*`` status) is never selected, expired
    # or transitioned here, and every status write below repeats the ``pending`` guard so a
    # row reserved after the due-fetch is left as the protocol set it
    # (strict-owner-telegram-wake-recovery: Fenced Wake-Recovery Cohorts).
    # Expire stale pending notifications (> 24h past deliver_at)
    await pool.execute(
        """
        UPDATE deferred_notifications
        SET status = 'expired'
        WHERE status = 'pending'
          AND deliver_at < $1
        """,
        now - timedelta(hours=24),
    )

    # Fetch due notifications (pending, deliver_at <= now)
    due_rows = await pool.fetch(
        """
        SELECT id, butler_name, channel, message, priority, envelope
        FROM deferred_notifications
        WHERE status = 'pending' AND deliver_at <= $1
        ORDER BY deliver_at
        """,
        now,
    )

    if not due_rows:
        return 0

    if notify_fn is None:
        # No delivery callable wired — leave rows pending for next tick.
        logger.warning(
            "_tick_deferred_notification_pass: %d due notification(s) skipped"
            " because notify_fn is not configured; will retry on next tick.",
            len(due_rows),
        )
        return 0

    parsed_rows: list[dict[str, Any]] = []
    for row in due_rows:
        envelope = row["envelope"]
        if isinstance(envelope, str):
            envelope = _json.loads(envelope)
        parsed_rows.append({**dict(row), "envelope": envelope})

    delivered = 0
    for group in _group_due_deferred_notifications(parsed_rows):
        if len(group) == 1:
            row = group[0]
            notif_id = row["id"]
            envelope = row["envelope"]
            try:
                # Deliver via the standard notify pipeline using the stored
                # notify.v1 envelope, NOT via the LLM spawner.
                outcome = await notify_fn(envelope)
                if metadata := terminal_telegram_metadata(outcome):
                    await terminal_partial(group, metadata)
                    continue
                await pool.execute(
                    """
                    UPDATE deferred_notifications
                    SET status = 'delivered', delivered_at = $2
                    WHERE id = $1 AND status = 'pending'
                    """,
                    notif_id,
                    now,
                )
                delivered += 1
                logger.info(
                    "Delivered deferred notification %s on channel %s", notif_id, row["channel"]
                )
                await record_attention_event(
                    pool,
                    origin_butler=row["butler_name"],
                    source="notify",
                    outcome="delivered",
                    channel=row["channel"],
                    intent="send",
                    priority=row["priority"],
                    notification_ref=str(notif_id),
                )
            except Exception:
                logger.exception("Failed to deliver deferred notification %s", notif_id)
                # Keep status=pending for next-tick retry
            continue

        # Same-window coalescing: >1 due row shares one delivery target —
        # compose and send once.
        group_ids = [row["id"] for row in group]
        digest_envelope = _format_deferred_digest(group)
        try:
            outcome = await notify_fn(digest_envelope)
            if metadata := terminal_telegram_metadata(outcome):
                await terminal_partial(group, metadata)
                continue
            await pool.execute(
                """
                UPDATE deferred_notifications
                SET status = 'delivered', delivered_at = $2
                WHERE id = ANY($1::uuid[]) AND status = 'pending'
                """,
                group_ids,
                now,
            )
            delivered += len(group)
            logger.info(
                "Delivered %d deferred notifications as one composed digest (channel=%s)",
                len(group),
                digest_envelope["delivery"].get("channel"),
            )
            for row in group:
                await record_attention_event(
                    pool,
                    origin_butler=row["butler_name"],
                    source="notify",
                    outcome="coalesced",
                    channel=row["channel"],
                    intent="send",
                    priority=row["priority"],
                    notification_ref=str(row["id"]),
                )
        except Exception:
            logger.exception(
                "Failed to deliver composed deferred-notification digest (%d items)", len(group)
            )
            # Keep the whole group status=pending for next-tick retry — a
            # partial send (some delivered, some not) would defeat the point
            # of a single composed message and complicate the retry story.

    return delivered
