"""Owner-chat commitment candidate cards and their sink (bu-jv1moa.11, PR4).

REQ-commitment-lifecycle-013 and -014. A pending candidate becomes one signed
confirm card (Track, Already done, Ignore) through the ``relationship.commitment_candidate``
``ConfirmSource``. Only the owner's Track answer promotes a candidate to a commitment.

* The card step writes a card snapshot before the first request: the question, the
  card spans, the owner recipient and the timeout, fixed once. ``prepare`` rebuilds
  the prompt only from that snapshot, so a card step repeated after a crash asks the
  confirm service the identical question and gets the existing card back. It never
  makes a second one.
* No re-card. A card that expires unanswered stays ``carded`` and lapses silently at
  the candidate's 14-day horizon.
* ``apply`` runs the disposition, the commitment write (Track only, and only when
  the fingerprint is not already active) and the receipt keyed by ``confirm_id`` in
  one Relationship transaction, under a row lock on the candidate. The first terminal
  state wins, and a retried ``confirm_id`` returns its stored receipt.

Nothing here runs until the canonical goal-runtime switch installs the signed-confirm
composition (bu-atc3xi) with this source.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

import asyncpg

from butlers.core.commitments import create_commitment
from butlers.core.confirm_authority import ConfirmSource
from butlers.core.confirm_models import ConfirmCard, ConfirmOption, ConfirmPrompt, Recipient
from butlers.core.entity_posture import ACTIVE, fetch_entity_posture
from butlers.core.owner_conditions import get_active_condition

SOURCE_NAME: Final = "relationship.commitment_candidate"
VALUES: Final = frozenset({"track", "done", "ignore"})
OPTIONS: Final = (
    ConfirmOption("track", "Track"),
    ConfirmOption("done", "Already done"),
    ConfirmOption("ignore", "Ignore"),
)
QUESTION: Final = "You promised this in a chat. Track it?"
COMMITMENT_SOURCE: Final = "relationship:commitment"
MAX_CARD_SECONDS: Final = 86400
#: A candidate this close to its lapse is never carded; it lapses.
MIN_CARD_SECONDS: Final = 30
#: Candidates considered per card step. The next run continues.
CARD_BATCH: Final = 50

_CLEAR_CONTENT: Final = """
    kind = NULL, direction = NULL, counterparty_entity_id = NULL, action_description = NULL,
    deadline = NULL, confidence = NULL, marker = NULL, fingerprint = NULL, channel = NULL,
    evidence_ref = NULL, taint = NULL, card_snapshot = NULL, card_expires_at = NULL
"""

#: The owner's current confirm recipient, or None when it cannot be resolved.
OwnerRecipient = Callable[[], Awaitable[Recipient | None]]
#: The trusted producer client's ``request(key, data)``.
CardRequest = Callable[[str, dict], Awaitable[Mapping[str, Any]]]


class CardRefused(ValueError):
    """``prepare`` will not build a card for this candidate now; it stays pending."""


def owner_recipient_reader(pool: asyncpg.Pool) -> OwnerRecipient:
    """The owner entity and the numeric Telegram chat id it answers from.

    A ``@username`` cannot be a confirm recipient: the callback names a numeric
    sender and chat, so only a stored ``telegram_chat_id`` qualifies.
    """
    from butlers.core.owner import resolve_owner_entity_id_two_step
    from butlers.credential_store import resolve_owner_entity_info

    async def read() -> Recipient | None:
        entity = await resolve_owner_entity_id_two_step(pool)
        chat = await resolve_owner_entity_info(pool, "telegram_chat_id")
        if entity is None or not chat or not str(chat).lstrip("-").isdigit():
            return None
        return Recipient(entity, str(chat), str(chat), True)

    return read


def _recipient_json(recipient: Recipient) -> dict[str, str]:
    return {
        "entity_id": str(recipient.entity_id),
        "sender_id": recipient.sender_id,
        "chat_id": recipient.chat_id,
    }


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value


def prompt_from_snapshot(snapshot: Mapping[str, Any]) -> ConfirmPrompt:
    """The one prompt a candidate's card can ever carry."""
    recipient = snapshot["recipient"]
    return ConfirmPrompt(
        snapshot["question"],
        OPTIONS,
        Recipient(UUID(recipient["entity_id"]), recipient["sender_id"], recipient["chat_id"], True),
        timeout_seconds=snapshot["timeout_seconds"],
        card=ConfirmCard(facts=tuple(snapshot["facts"]), quotes=tuple(snapshot["quotes"])),
    )


async def _snapshot(
    conn: Any, row: Mapping[str, Any], recipient: Recipient, now: datetime
) -> dict[str, Any]:
    name = await conn.fetchval(
        "SELECT canonical_name FROM public.entities WHERE id = $1", row["counterparty_entity_id"]
    )
    facts = [f"To: {name}" if isinstance(name, str) and name.strip() else "To: a contact"]
    if row["deadline"] is not None:
        facts.append(f"Due: {row['deadline'].isoformat(timespec='minutes')}")
    timeout = min(MAX_CARD_SECONDS, int((row["lapse_at"] - now).total_seconds()))
    snapshot = {
        "question": QUESTION,
        "facts": [fact[:256] for fact in facts],
        "quotes": [row["action_description"]],
        "recipient": _recipient_json(recipient),
        "timeout_seconds": timeout,
        "requested_at": now.isoformat(),
    }
    prompt_from_snapshot(snapshot)  # refuse here, before anything is stored, if invalid
    return snapshot


async def card_step(
    pool: asyncpg.Pool, *, request: CardRequest, owner_recipient: OwnerRecipient
) -> dict[str, int]:
    """Card every pending candidate that may be carded now; return outcome counts."""
    outcomes: dict[str, int] = {}
    ids = await pool.fetch(
        """
        SELECT id FROM relationship.commitment_candidates
        WHERE state = 'pending' ORDER BY created_at, id LIMIT $1
        """,
        CARD_BATCH,
    )
    for record in ids:
        outcome = await _card_one(pool, record["id"], request, owner_recipient)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


async def _card_one(
    pool: asyncpg.Pool, candidate_id: UUID, request: CardRequest, owner_recipient: OwnerRecipient
) -> str:
    async with pool.acquire() as acquired, acquired.transaction():
        # The posture and condition readers accept a connection but are annotated as Pool.
        conn: Any = acquired
        row = await conn.fetchrow(
            """
            SELECT *, now() AS db_now FROM relationship.commitment_candidates
            WHERE id = $1 AND state = 'pending' FOR UPDATE SKIP LOCKED
            """,
            candidate_id,
        )
        if row is None:
            return "busy"
        now = row["db_now"]
        snapshot = _json(row["card_snapshot"])
        if snapshot is None:
            if row["lapse_at"] - now < timedelta(seconds=MIN_CARD_SECONDS):
                return "lapsing"
            if await get_active_condition(
                conn, source=COMMITMENT_SOURCE, fingerprint=row["fingerprint"]
            ):
                await conn.execute(
                    f"""
                    UPDATE relationship.commitment_candidates
                    SET state = 'superseded', terminal_at = now(), {_CLEAR_CONTENT}
                    WHERE id = $1
                    """,
                    candidate_id,
                )
                return "superseded"
            if await fetch_entity_posture(conn, row["counterparty_entity_id"]) != ACTIVE:
                return "posture_deferred"
            recipient = await owner_recipient()
            if recipient is None:
                return "recipient_unavailable"
            snapshot = await _snapshot(conn, row, recipient, now)
            await conn.execute(
                "UPDATE relationship.commitment_candidates SET card_snapshot = $2::jsonb "
                "WHERE id = $1",
                candidate_id,
                json.dumps(snapshot),
            )
    # The card request runs outside the row lock: the confirm service may wait on its
    # own transaction, and prepare reloads this row itself.
    key = str(candidate_id)
    try:
        response = await request(key, {"candidate_id": key})
        confirm_id = UUID(str(response["confirm_id"]))
    except Exception:
        # Refused or unavailable: the candidate stays pending and the next run retries
        # with the same snapshot, so the retried question is identical.
        return "card_unavailable"
    expires = datetime.fromisoformat(snapshot["requested_at"]) + timedelta(
        seconds=snapshot["timeout_seconds"]
    )
    marked = await pool.execute(
        """
        UPDATE relationship.commitment_candidates
        SET state = 'carded', confirm_id = $2, card_expires_at = $3
        WHERE id = $1 AND state = 'pending'
        """,
        candidate_id,
        confirm_id,
        expires,
    )
    return "carded" if marked == "UPDATE 1" else "settled_meanwhile"


def build_source(pool: asyncpg.Pool, *, owner_recipient: OwnerRecipient) -> ConfirmSource:
    """The ``relationship.commitment_candidate`` source for the trusted composition."""

    async def prepare(key: str, data: dict) -> tuple[ConfirmPrompt, str]:
        try:
            candidate_id = UUID(key)
        except ValueError:
            raise CardRefused("candidate_key_invalid") from None
        if data != {"candidate_id": key}:
            raise CardRefused("candidate_data_invalid")
        row = await pool.fetchrow(
            "SELECT state, counterparty_entity_id, card_snapshot "
            "FROM relationship.commitment_candidates WHERE id = $1",
            candidate_id,
        )
        snapshot = _json(row["card_snapshot"]) if row is not None else None
        if row is None or row["state"] not in ("pending", "carded") or snapshot is None:
            raise CardRefused("candidate_not_cardable")
        if row["state"] == "pending":
            if await fetch_entity_posture(pool, row["counterparty_entity_id"]) != ACTIVE:
                raise CardRefused("posture_not_active")
            current = await owner_recipient()
            if current is None or _recipient_json(current) != snapshot["recipient"]:
                raise CardRefused("owner_recipient_changed")
        return prompt_from_snapshot(snapshot), key

    async def apply(confirm_id: UUID, ref: str, value: str) -> dict:
        return await apply_disposition(pool, confirm_id, ref, value)

    return ConfirmSource(prepare, sink_name=SOURCE_NAME, values=VALUES, apply=apply)


def _receipt(outcome: str, fingerprint: str | None) -> dict[str, Any]:
    receipt: dict[str, Any] = {"status": "applied", "outcome": outcome}
    if fingerprint is not None:
        receipt["fingerprint"] = fingerprint
    return receipt


async def apply_disposition(pool: asyncpg.Pool, confirm_id: UUID, ref: str, value: str) -> dict:
    """Apply one owner answer, its receipt and any commitment write in one transaction."""
    if value not in VALUES:
        raise ValueError("confirm_option_unregistered")
    try:
        candidate_id = UUID(ref)
    except ValueError:
        raise ValueError("candidate_ref_invalid") from None
    async with pool.acquire() as acquired, acquired.transaction():
        # create_commitment and the condition reader accept this pooled connection, but
        # are annotated as Connection and Pool.
        conn: Any = acquired
        stored = await conn.fetchrow(
            "SELECT outcome, commitment_fingerprint FROM "
            "relationship.commitment_candidate_receipts WHERE confirm_id = $1",
            confirm_id,
        )
        if stored is not None:
            return _receipt(stored["outcome"], stored["commitment_fingerprint"])
        row = await conn.fetchrow(
            "SELECT *, now() AS db_now FROM relationship.commitment_candidates "
            "WHERE id = $1 FOR UPDATE",
            candidate_id,
        )
        fingerprint = None
        if row is None:
            outcome = "already_lapsed"
        elif row["state"] not in ("pending", "carded"):
            outcome = f"already_{row['state']}"
        elif row["confirm_id"] not in (None, confirm_id):
            raise ValueError("candidate_bound_to_another_confirm")
        elif value == "track":
            outcome, fingerprint = "tracked", row["fingerprint"]
            if not await get_active_condition(
                conn, source=COMMITMENT_SOURCE, fingerprint=fingerprint
            ):
                await create_commitment(
                    pool,
                    source=COMMITMENT_SOURCE,
                    summary=row["action_description"],
                    kind=row["kind"],
                    direction=row["direction"],
                    counterparty_entity_id=str(row["counterparty_entity_id"]),
                    confidence=row["confidence"],
                    evidence_opened=_evidence(row, confirm_id),
                    action_description=row["action_description"],
                    deadline=row["deadline"],
                    transaction_connection=conn,
                )
        else:
            outcome = "done" if value == "done" else "ignored"
        if row is not None and outcome in ("tracked", "done", "ignored"):
            await conn.execute(
                f"""
                UPDATE relationship.commitment_candidates
                SET state = $2, confirm_id = $3, commitment_fingerprint = $4,
                    terminal_at = now(), {_CLEAR_CONTENT}
                WHERE id = $1
                """,
                candidate_id,
                outcome,
                confirm_id,
                fingerprint,
            )
        if outcome != "tracked":
            fingerprint = None
        await conn.execute(
            """
            INSERT INTO relationship.commitment_candidate_receipts
                (confirm_id, candidate_id, outcome, commitment_fingerprint)
            VALUES ($1, $2, $3, $4)
            """,
            confirm_id,
            candidate_id,
            outcome,
            fingerprint,
        )
    return _receipt(outcome, fingerprint)


def _evidence(row: Mapping[str, Any], confirm_id: UUID) -> dict[str, Any]:
    """``evidence_opened`` for a tracked candidate: references only, never message text."""
    ref = _json(row["evidence_ref"]) or {}
    return {
        "source": "owner_chat_candidate",
        "channel": row["channel"],
        "ingestion_event_id": ref.get("ingestion_event_id"),
        "provider_message_id": ref.get("provider_message_id"),
        "event_time": row["event_time"].isoformat(),
        "marker": row["marker"],
        "candidate_id": str(row["id"]),
        "confirm_id": str(confirm_id),
        "applied_at": row["db_now"].isoformat(),
        "detected_at": row["created_at"].isoformat(),
    }
