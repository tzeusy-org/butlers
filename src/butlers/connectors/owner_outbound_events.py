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
messaging". An unkeyed hash is not enough: Telegram chat ids are contact
handles and message ids are sequential, so a plain SHA-256 can be inverted by
enumeration (bu-szrvwu). The identifier is therefore HMAC-SHA256'd under
``OWNER_OUTBOUND_DEDUP_KEY`` from the shared credential store before it ever
reaches SQL: deterministic under one key (replays still dedup) and not
recoverable from stored rows without the key.

Missing key fails closed: the connector logs one startup warning and records
no point events at all, rather than falling back to a plain hash. Rotating
the key only weakens dedup across the rotation (a replayed message may be
recorded once more). Rows written before bu-szrvwu carry the legacy
``owner_outbound:<provider>:<sha256>`` key; they cannot be rehashed (the hash
is one-way) and do not age out, so scrubbing them is an owner-gated data
operation, not something this module does.

If counterpart-less point events turn out to limit corroboration value for a
future consumer (e.g. wanting to distinguish "many short bursts" from "one
long exchange"), that is a known, accepted limitation of this Tier-1 signal
— see the module docstring on ``chronicler/adapters/owner_outbound.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_TABLE = "connectors.owner_outbound_events"


OWNER_OUTBOUND_DEDUP_KEY_NAME = "OWNER_OUTBOUND_DEDUP_KEY"

# Process-wide HMAC key; set once at connector startup by ``resolve_dedup_key``.
_dedup_key: bytes | None = None


def configure_dedup_key(value: str | None) -> bool:
    """Set (or clear) the process-wide HMAC key. Returns whether a key is configured."""
    global _dedup_key
    if value is None or not str(value).strip():
        _dedup_key = None
        return False
    _dedup_key = str(value).strip().encode("utf-8")
    return True


async def resolve_dedup_key(credential_store: Any) -> bool:
    """Resolve the dedup HMAC key from the shared credential store, once, at startup.

    ``env_fallback=False``: the key must come from the credential store. Logs a
    single warning when the key is unavailable and never raises; while no key
    is configured, ``record_owner_outbound_point`` writes nothing.
    """
    value: str | None = None
    probe_failed = False
    if credential_store is not None:
        try:
            value = await credential_store.resolve(
                OWNER_OUTBOUND_DEDUP_KEY_NAME, env_fallback=False
            )
        except Exception as exc:  # noqa: BLE001 - a probe failure must not block startup
            value = None
            probe_failed = True
            # Log only the exception type: never the message, which could echo a value.
            logger.warning(
                "%s credential-store probe failed (%s); owner-outbound point events "
                "will not be recorded until the connector restarts with the store reachable",
                OWNER_OUTBOUND_DEDUP_KEY_NAME,
                type(exc).__name__,
            )
    configured = configure_dedup_key(value)
    if not configured and not probe_failed:
        logger.warning(
            "%s is not provisioned: owner-outbound point events will not be recorded",
            OWNER_OUTBOUND_DEDUP_KEY_NAME,
        )
    return configured


def _hash_dedup_material(provider: str, dedup_material: str) -> str | None:
    """Keyed one-way digest of a connector-native (chat, message) identifier pair.

    Never store the raw chat/message identifier, or an unkeyed hash of it: it
    is derived from who the owner was messaging, and the privacy contract for
    this signal is "no counterpart identity", full stop. ``None`` when no key
    is configured.
    """
    key = _dedup_key
    if key is None:
        return None
    message = f"{provider}:{dedup_material}".encode()
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"owner_outbound:{provider}:hmac:{digest}"


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
            equivalent) — HMAC'd before use, never persisted in cleartext.

    Returns:
        True if a new row was inserted, False if it already existed (or the
        write was skipped/failed, including when no dedup key is configured).
    """
    if pool is None:
        return False

    idempotency_key = _hash_dedup_material(provider, dedup_material)
    if idempotency_key is None:
        return False
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


__all__ = [
    "OWNER_OUTBOUND_DEDUP_KEY_NAME",
    "configure_dedup_key",
    "record_owner_outbound_point",
    "resolve_dedup_key",
]
