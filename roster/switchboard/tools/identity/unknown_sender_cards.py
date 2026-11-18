"""The unknown-sender card and its sink, built but dark (bu-g0nsbw).

A newly surfaced unknown sender can become one signed confirm card through the
``switchboard.unknown_sender`` ``ConfirmSource``: the sender's name, raw address and
subject, an Open link to the transitory entity, and two options, Add as contact and
Ignore this sender.

* The card step writes a snapshot to the Switchboard ``state`` table before the first
  request, keyed by the transitory entity. ``prepare`` rebuilds the prompt only from that
  snapshot, so a repeated step asks the identical question and gets the existing card.
* ``apply`` runs in one Switchboard transaction under a row lock on the entity, with a
  receipt keyed by ``confirm_id`` in the same ``state`` table. A retried ``confirm_id``
  returns its stored receipt.

  - Add as contact clears ``metadata.unidentified`` (and ``duplicate_candidate``), as the
    dashboard's promote does, naming a placeholder entity by its address.
  - Ignore archives the entity (``metadata.archived_at``, which every entity surface,
    the Unidentified queue included, hides and the dashboard can undo) and, for an email
    address, adds one global ``sender_address`` skip rule so later mail from that sender
    never reaches a routing session. Switchboard does not write
    ``relationship.entity_facts`` (switchboard-identity), so the queue's
    ``queue.dismissed`` triple is not used here.

Nothing here runs until the canonical goal-runtime switch composes the signed-confirm
topology with ``switchboard_confirm_sources`` (follow-up bu-6hszs3). Until then the owner
gets the plain-text notice from ``inject.unknown_sender_notice_lines``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final
from uuid import UUID

from butlers.core.confirm_authority import UNKNOWN_SENDER_SOURCE, ConfirmSource
from butlers.core.confirm_models import ConfirmCard, ConfirmOption, ConfirmPrompt, Recipient
from butlers.core.confirm_recipient import OwnerRecipient
from butlers.roster_imports import roster_module

_inject = roster_module("switchboard", "tools/identity/inject")
_verdict_log = roster_module("switchboard", "tools/routing/verdict_log")

SOURCE_NAME: Final = UNKNOWN_SENDER_SOURCE
VALUES: Final = frozenset({"add_contact", "ignore"})
OPTIONS: Final = (
    ConfirmOption("add_contact", "Add as contact"),
    ConfirmOption("ignore", "Ignore this sender"),
)
QUESTION: Final = "Someone new wrote to you."
TIMEOUT_SECONDS: Final = 86400
SNAPSHOT_KEY_PREFIX: Final = "identity:unknown_card:"
RECEIPT_KEY_PREFIX: Final = "identity:unknown_card_receipt:"
#: Same priority as promoted rules: an explicit owner choice for one exact address.
SKIP_RULE_PRIORITY: Final = 10

#: The trusted producer client's ``request(key, data)``.
CardRequest = Callable[[str, dict], Awaitable[Mapping[str, Any]]]


class CardRefused(ValueError):
    """``prepare`` will not build a card for this sender now."""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value


def _recipient_json(recipient: Recipient) -> dict[str, str]:
    return {
        "entity_id": str(recipient.entity_id),
        "sender_id": recipient.sender_id,
        "chat_id": recipient.chat_id,
    }


def prompt_from_snapshot(snapshot: Mapping[str, Any]) -> ConfirmPrompt:
    """The one prompt an unknown sender's card can ever carry."""
    recipient = snapshot["recipient"]
    return ConfirmPrompt(
        snapshot["question"],
        OPTIONS,
        Recipient(UUID(recipient["entity_id"]), recipient["sender_id"], recipient["chat_id"], True),
        timeout_seconds=snapshot["timeout_seconds"],
        card=ConfirmCard(facts=tuple(snapshot["facts"])),
    )


async def _state(conn: Any, key: str) -> Any:
    row = await conn.fetchrow("SELECT value FROM state WHERE key = $1", key)
    return None if row is None else _json(row["value"])


async def _put_state(conn: Any, key: str, value: dict[str, Any]) -> bool:
    """Insert one state row once; False when the key already existed."""
    row = await conn.fetchrow(
        """
        INSERT INTO state (key, value, updated_at, version)
        VALUES ($1, $2, now(), 1)
        ON CONFLICT (key) DO NOTHING
        RETURNING key
        """,
        key,
        value,
    )
    return row is not None


async def request_card(
    pool: Any,
    *,
    request: CardRequest,
    owner_recipient: OwnerRecipient,
    entity_id: UUID,
    channel: str,
    address: str,
    display_name: str | None,
    subject: str | None,
) -> str:
    """Snapshot (once) and request the card for one surfaced unknown sender.

    Returns ``carded``, ``recipient_unavailable`` or ``card_unavailable``. A refused or
    unavailable request leaves the snapshot, so a retry asks the identical question.
    """
    recipient = await owner_recipient()
    if recipient is None:
        return "recipient_unavailable"
    key = str(entity_id)
    snapshot = {
        "question": QUESTION,
        "facts": [
            line[:256]
            for line in _inject.unknown_sender_notice_lines(
                channel=channel,
                address=address,
                display_name=display_name,
                subject=subject,
                entity_id=entity_id,
            )
        ],
        "recipient": _recipient_json(recipient),
        "timeout_seconds": TIMEOUT_SECONDS,
        "entity_id": key,
        "channel": channel,
        "address": address,
        "display_name": display_name,
        "requested_at": datetime.now(UTC).isoformat(),
    }
    prompt_from_snapshot(snapshot)  # refuse here, before anything is stored, if invalid
    await _put_state(pool, f"{SNAPSHOT_KEY_PREFIX}{key}", snapshot)
    try:
        response = await request(key, {"entity_id": key})
        UUID(str(response["confirm_id"]))
    except Exception:
        return "card_unavailable"
    return "carded"


def build_source(pool: Any, *, owner_recipient: OwnerRecipient) -> ConfirmSource:
    """The ``switchboard.unknown_sender`` source for the trusted composition."""

    async def prepare(key: str, data: dict) -> tuple[ConfirmPrompt, str]:
        try:
            entity_id = UUID(key)
        except ValueError:
            raise CardRefused("entity_key_invalid") from None
        if data != {"entity_id": key}:
            raise CardRefused("entity_data_invalid")
        snapshot = await _state(pool, f"{SNAPSHOT_KEY_PREFIX}{key}")
        entity = await pool.fetchrow(
            "SELECT canonical_name, roles, metadata FROM public.entities WHERE id = $1",
            entity_id,
        )
        metadata = _json(entity["metadata"]) if entity is not None else None
        if snapshot is None or not isinstance(metadata, dict) or not metadata.get("unidentified"):
            raise CardRefused("entity_not_cardable")
        current = await owner_recipient()
        if current is None or _recipient_json(current) != snapshot["recipient"]:
            raise CardRefused("owner_recipient_changed")
        return prompt_from_snapshot(snapshot), key

    async def apply(confirm_id: UUID, ref: str, value: str) -> dict:
        return await apply_action(pool, confirm_id, ref, value)

    return ConfirmSource(prepare, sink_name=SOURCE_NAME, values=VALUES, apply=apply)


async def apply_action(pool: Any, confirm_id: UUID, ref: str, value: str) -> dict:
    """Apply one owner answer and its receipt in one Switchboard transaction."""
    if value not in VALUES:
        raise ValueError("confirm_option_unregistered")
    try:
        entity_id = UUID(ref)
    except ValueError:
        raise ValueError("entity_ref_invalid") from None
    receipt_key = f"{RECEIPT_KEY_PREFIX}{confirm_id}"
    async with pool.acquire() as conn, conn.transaction():
        # The entity lock comes first, so a concurrent replay of this confirm_id waits
        # here and then finds the receipt the first one committed.
        entity = await conn.fetchrow(
            "SELECT canonical_name, roles, metadata FROM public.entities WHERE id = $1 FOR UPDATE",
            entity_id,
        )
        stored = await _state(conn, receipt_key)
        if stored is not None:
            return stored
        snapshot = await _state(conn, f"{SNAPSHOT_KEY_PREFIX}{entity_id}") or {}
        metadata = (_json(entity["metadata"]) or {}) if entity is not None else {}
        if entity is None or metadata.get("merged_into") or "owner" in (entity["roles"] or []):
            receipt: dict[str, Any] = {"status": "applied", "outcome": "already_gone"}
        elif value == "add_contact":
            receipt = await _add_contact(conn, entity_id, entity, metadata, snapshot)
        else:
            receipt = await _ignore(conn, entity_id, metadata, snapshot)
        if not await _put_state(conn, receipt_key, receipt):
            # A concurrent replay of this confirm_id committed first; its receipt stands.
            # Raising rolls this attempt back, and the retried application reads it.
            raise RuntimeError("confirm_receipt_raced")
    return receipt


async def _add_contact(
    conn: Any,
    entity_id: UUID,
    entity: Mapping[str, Any],
    metadata: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    if not metadata.get("unidentified"):
        return {"status": "applied", "outcome": "already_identified"}
    name = str(entity["canonical_name"] or "")
    address = snapshot.get("address")
    if not snapshot.get("display_name") and isinstance(address, str) and address:
        if not name or name.startswith("Unknown "):
            name = address
    await conn.execute(
        """
        UPDATE public.entities
        SET canonical_name = $2,
            metadata = COALESCE(metadata, '{}'::jsonb)
                       - ARRAY['unidentified', 'duplicate_candidate'],
            updated_at = now()
        WHERE id = $1
        """,
        entity_id,
        name,
    )
    return {"status": "applied", "outcome": "added"}


async def _ignore(
    conn: Any, entity_id: UUID, metadata: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    if not metadata.get("archived_at"):
        await conn.execute(
            """
            UPDATE public.entities
            SET metadata = COALESCE(metadata, '{}'::jsonb) || $2,
                updated_at = now()
            WHERE id = $1
            """,
            entity_id,
            {"archived_at": datetime.now(UTC).isoformat()},
        )
    address = str(snapshot.get("address") or "").strip().lower()
    if snapshot.get("channel") != "email" or not _verdict_log.is_email_sender_key(address):
        return {"status": "applied", "outcome": "ignored", "rule": "not_applicable"}
    existing = await conn.fetchrow(
        """
        SELECT id FROM switchboard.ingestion_rules
        WHERE scope = 'global' AND rule_type = 'sender_address' AND action = 'skip'
          AND enabled AND deleted_at IS NULL
          AND lower(condition->>'address') = $1
          AND condition->>'match' IS NULL
        LIMIT 1
        """,
        address,
    )
    if existing is not None:
        return {"status": "applied", "outcome": "ignored", "rule": "existing"}
    await conn.fetchrow(
        """
        INSERT INTO switchboard.ingestion_rules
            (scope, rule_type, condition, action, priority, enabled, name, description,
             created_by)
        VALUES ('global', 'sender_address', $1, 'skip', $2, TRUE, $3, $4, 'api')
        RETURNING id
        """,
        {"address": address},
        SKIP_RULE_PRIORITY,
        f"Ignored sender: {address}"[:200],
        "Owner chose Ignore this sender on the unknown-sender card.",
    )
    return {"status": "applied", "outcome": "ignored", "rule": "created"}
