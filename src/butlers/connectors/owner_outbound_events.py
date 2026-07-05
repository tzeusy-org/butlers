"""Owner-outbound-message point-event recording (bu-whhll.8).

Shared helper for the ``telegram_user_client`` and ``whatsapp_user_client``
connectors. Both are user-client (MTProto / WhatsApp-bridge) connectors that
mirror the owner's own account, so they already observe messages the owner
*sends*, not just messages the owner *receives*. Per the Chronicler
"workday sensors" epic (bu-whhll Tier 1), the owner's own outbound message
activity is a "phone in hand" signal — a weak corroborator for occupation
inference and a contradictor for sleep — that today is discarded entirely.

This module writes directly to ``connectors.owner_outbound_events``
(``core_161`` migration) rather than extending the comms Chronicler adapter's
read surface (``public.ingestion_events``): both connectors submit ingest
envelopes as whole-chat *batches* with ``sender.identity = "multiple"`` (see
each connector's ``_build_batch_envelope``), so there is no per-message,
per-sender granularity on that surface to tell "the owner sent this one"
apart from "someone else sent this one". Each connector already knows that
distinction per-message at ingest time, which makes recording here the
smaller-blast-radius lever.

Privacy contract (bead requirement, hard constraint): point events carry
timestamp + channel ONLY. No content, no counterpart identity. This module
enforces that at the schema level — ``connectors.owner_outbound_events`` has
no content column and no counterpart-identity column. The one caveat is the
idempotency key: replay-safety requires *some* stable per-message dedup
handle, and the only stable handles connectors have are chat/thread and
message identifiers, which are themselves derived from "who the owner was
messaging". To avoid smuggling counterpart identity into the DB even as an
inert dedup key, the raw identifier is SHA-256 hashed before it ever reaches
SQL — the digest is deterministic (so replays still dedup correctly) but is
not reversible back into the source chat/message id.

If counterpart-less point events turn out to limit corroboration value for a
future consumer (e.g. wanting to distinguish "many short bursts" from "one
long exchange"), that is a known, accepted limitation of this Tier-1 signal
— see the module docstring on ``chronicler/adapters/owner_outbound.py``.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime

import asyncpg

logger = logging.getLogger(__name__)

_TABLE = "connectors.owner_outbound_events"


def _hash_dedup_material(provider: str, dedup_material: str) -> str:
    """One-way digest of a connector-native (chat, message) identifier pair.

    Never store the raw chat/message identifier, even as an opaque dedup
    key: it is derived from who the owner was messaging, and the privacy
    contract for this signal is "no counterpart identity", full stop.
    """
    digest = hashlib.sha256(f"{provider}:{dedup_material}".encode()).hexdigest()
    return f"owner_outbound:{provider}:{digest}"


async def record_owner_outbound_point(
    pool: asyncpg.Pool | None,
    *,
    channel: str,
    provider: str,
    endpoint_identity: str,
    occurred_at: datetime,
    dedup_material: str,
) -> bool:
    """Best-effort, fail-soft record of one owner-outbound message point event.

    This is a corroboration signal, never on the critical path for message
    routing/ingest — any failure (missing pool, transient DB error) is
    logged and swallowed rather than raised, so it can never regress
    message delivery.

    Args:
        pool: Connector DB pool (``connector_writer`` role). No-ops if None.
        channel: ``"telegram_user_client"`` or ``"whatsapp_user_client"``.
        provider: Connector provider string (e.g. ``"telegram"``,
            ``"whatsapp"``) — folded into the idempotency hash so the two
            connectors' key spaces never collide.
        endpoint_identity: The owner's own connector identity (not the
            counterpart's).
        occurred_at: The message's own timestamp (not "now").
        dedup_material: Connector-native ``f"{chat_id}:{message_id}"`` (or
            equivalent) — hashed before use, never persisted in cleartext.

    Returns:
        True if a new row was inserted, False if it already existed (or the
        write was skipped/failed).
    """
    if pool is None:
        return False

    idempotency_key = _hash_dedup_material(provider, dedup_material)
    try:
        result = await pool.fetchval(
            f"""
            INSERT INTO {_TABLE} (
                idempotency_key, channel, endpoint_identity, occurred_at
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            idempotency_key,
            channel,
            endpoint_identity,
            occurred_at,
        )
    except Exception:
        logger.warning("Failed to record owner-outbound point event (non-fatal)", exc_info=True)
        return False
    return result is not None


__all__ = ["record_owner_outbound_point"]
