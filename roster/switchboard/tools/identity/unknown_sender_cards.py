"""The unknown-sender cards and their Ignore/Add sink (bu-g0nsbw, bu-p7glz9).

Live today, pre-cutover: the Ignore approval card (bu-p7glz9). When Switchboard surfaces
a new unknown sender, ``park_ignore_card`` parks one approval-gated pending action for
``identity_ignore_unknown_sender`` through ``park_pending_action``, so the decision-loop
push (RFC 0021) shows the owner Approve / Reject / Open. Approve runs the Ignore sink
(``ignore_unknown_sender``); Reject or expiry does nothing, so the sender stays in the
Unidentified queue.

* The action id is derived from the entity id, so a replayed notice or a backlog rescan
  finds the existing action and never parks a second one.
* The parked call carries the entity id, channel, address and display name, and the
  approval preview shows each of them. The park writes a snapshot of the same fields to
  the Switchboard ``state`` table in the same transaction; the tool refuses any call
  that does not match it, so only a call Switchboard parked for a surfaced sender can
  archive anything.
* With Switchboard's ``approval_delivery_rollout`` off, the park still commits the
  pending action (visible on the dashboard) without a push, and the owner keeps the
  plain-text notice. With admission and worker both on, the card replaces the notice.

Dark: the signed-confirm card (bu-g0nsbw). A newly surfaced unknown sender can become
one signed confirm card through the
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
    ``queue.dismissed`` triple is not used here. Both cards share this sink.

The signed-confirm source does not run until the canonical goal-runtime switch composes
the signed-confirm topology with ``switchboard_confirm_sources`` (follow-up bu-6hszs3).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid5

from butlers.core.confirm_authority import UNKNOWN_SENDER_SOURCE, ConfirmSource
from butlers.core.confirm_models import ConfirmCard, ConfirmOption, ConfirmPrompt, Recipient
from butlers.core.confirm_recipient import OwnerRecipient
from butlers.modules.approvals.command_contracts import UNKNOWN_SENDER_IGNORE_COMMAND
from butlers.modules.approvals.park import park_pending_action
from butlers.modules.approvals.rollout import read_approval_delivery_rollout
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

#: The pre-cutover Ignore approval card (bu-p7glz9).
IGNORE_TOOL_NAME: Final = UNKNOWN_SENDER_IGNORE_COMMAND.name
IGNORE_SNAPSHOT_KEY_PREFIX: Final = "identity:unknown_ignore:"
IGNORE_DEDUP_KEY_PREFIX: Final = "switchboard.unknown_sender.ignore:"
#: Matches Switchboard's ``default_expiry_hours``; expiry leaves the sender queued.
IGNORE_CARD_TTL: Final = timedelta(hours=72)
_IGNORE_ACTION_NAMESPACE: Final = UUID("6f1d2a52-3c1e-4f43-9a55-0b5e6c1d7a01")

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


# ---------------------------------------------------------------------------
# The pre-cutover Ignore approval card (bu-p7glz9)
# ---------------------------------------------------------------------------


def ignore_action_id(entity_id: UUID) -> UUID:
    """The one pending-action id an entity's Ignore card can ever have."""
    return uuid5(_IGNORE_ACTION_NAMESPACE, f"{IGNORE_DEDUP_KEY_PREFIX}{entity_id}")


def ignore_card_args(
    *, entity_id: UUID, channel: str, address: str, display_name: str | None
) -> dict[str, Any]:
    """The exact parked call: every field the owner sees on the card, and no more."""
    return UNKNOWN_SENDER_IGNORE_COMMAND.materialize(
        {
            "entity_id": str(entity_id),
            "channel": channel,
            "address": address,
            "display_name": _inject._clean(display_name, _inject._NAME_LIMIT) or None,
        }
    )


def ignore_card_dossier(args: Mapping[str, Any], subject: str | None) -> dict[str, Any]:
    """Server-written approval context; it never carries the message body."""
    name, address, channel = args["display_name"], args["address"], args["channel"]
    sender = f"{name} <{address}>" if name else address
    email = channel == "email" and _verdict_log.is_email_sender_key(address.strip().lower())
    clean_subject = _inject._clean(subject, _inject._SUBJECT_LIMIT)
    first = f"{sender} wrote to you for the first time on {channel}"
    first += f', subject "{clean_subject}".' if clean_subject else "."
    effect = (
        "Approving archives this unidentified sender and adds a skip rule, so their "
        "future email never reaches a routing session."
        if email
        else "Approving archives this unidentified sender. No skip rule is added for "
        f"{channel}, so their later messages still arrive."
    )
    evidence = [{"type": "entity", "ref": args["entity_id"], "note": "Unidentified sender."}]
    if clean_subject:
        evidence.append({"type": "text", "ref": clean_subject, "note": "First message subject."})
    return {
        "agent_summary": f"Ignore {sender}"[:300],
        "why": (
            f"{first} {effect} Rejecting or letting this expire keeps them in "
            "Unidentified Entities."
        ),
        "evidence": evidence,
        "blast_radius": "self",
        "reversibility": "reversible",
    }


async def park_ignore_card(
    pool: Any,
    *,
    entity_id: UUID,
    channel: str,
    address: str,
    display_name: str | None,
    subject: str | None,
) -> bool:
    """Park the one Ignore card for a newly surfaced unknown sender.

    Returns True only when this call parked a card the delivery worker will push (the
    Switchboard rollout's admission and worker are both on), so the caller can skip the
    plain-text notice. A duplicate park, or a disabled rollout, returns False: the
    pending action is on the dashboard, but nothing reaches Telegram from it.
    """
    args = ignore_card_args(
        entity_id=entity_id, channel=channel, address=address, display_name=display_name
    )
    dossier = ignore_card_dossier(args, subject)
    now = datetime.now(UTC)
    async with pool.acquire() as conn, conn.transaction():
        await _put_state(conn, f"{IGNORE_SNAPSHOT_KEY_PREFIX}{entity_id}", dict(args))
        admission = await park_pending_action(
            conn,
            action_id=ignore_action_id(entity_id),
            tool_name=IGNORE_TOOL_NAME,
            tool_args=args,
            requested_at=now,
            expires_at=now + IGNORE_CARD_TTL,
            origin_butler="switchboard",
            deduplication_key=f"{IGNORE_DEDUP_KEY_PREFIX}{entity_id}",
            **dossier,
        )
        rollout = await read_approval_delivery_rollout(conn)
    return not admission.duplicate and admission.intent_id is not None and rollout.worker_enabled


async def ignore_unknown_sender(
    pool: Any, *, entity_id: str, channel: str, address: str, display_name: str | None
) -> dict[str, Any]:
    """Run the approved Ignore card: archive the sender, and skip their email.

    Idempotent: a replay finds the entity archived and the rule present. Refuses (with an
    ``error``, so the executor records a failure) a call Switchboard did not park, an
    owner or merged entity, and a sender the owner has since identified.
    """
    try:
        eid = UUID(entity_id)
    except (TypeError, ValueError):
        return {"error": "entity_ref_invalid"}
    called = {
        "entity_id": entity_id,
        "channel": channel,
        "address": address,
        "display_name": display_name,
    }
    async with pool.acquire() as conn, conn.transaction():
        entity = await conn.fetchrow(
            "SELECT canonical_name, roles, metadata FROM public.entities WHERE id = $1 FOR UPDATE",
            eid,
        )
        snapshot = await _state(conn, f"{IGNORE_SNAPSHOT_KEY_PREFIX}{eid}")
        if snapshot != called:
            return {"error": "ignore_card_unknown"}
        metadata = (_json(entity["metadata"]) or {}) if entity is not None else {}
        if entity is None or metadata.get("merged_into") or "owner" in (entity["roles"] or []):
            return {"error": "entity_not_ignorable"}
        if not metadata.get("unidentified"):
            return {"error": "entity_identified"}
        return await _ignore(conn, eid, metadata, snapshot)
