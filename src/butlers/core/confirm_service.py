"""Durable informational answers and recoverable deterministic application.

Composition supplies a closed sink registry and restricted Messenger/Switchboard pool.
This service has no approval executor and no model-supplied routing interface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

from butlers.core.confirm_models import ConfirmPrompt
from butlers.core.confirm_tokens import mint_confirm_token, verify_confirm_token


@dataclass(frozen=True, slots=True)
class SinkRegistration:
    producer: str
    values: frozenset[str]
    apply: Callable[[UUID, str, str], Awaitable[dict]]


class ConfirmService:
    def __init__(self, pool, *, secret: str, sinks: Mapping[str, SinkRegistration], admission=None):
        if not secret:
            raise ValueError("confirm signing credential unavailable")
        self.pool, self.secret = pool, secret
        self._sinks = dict(sinks)
        self._admission = admission

    async def create(
        self,
        producer: str,
        producer_key: str,
        prompt: ConfirmPrompt,
        *,
        sink: str | None = None,
        sink_ref: str | None = None,
        goal_delivery_ref: str | None = None,
    ) -> dict:
        """Called only by authenticated service-plane composition, never public JSON."""
        if not producer or not producer_key or len(producer_key) > 256:
            raise ValueError("bounded durable producer identity required")
        if sink is not None:
            registration = self._sinks.get(sink)
            if (
                registration is None
                or registration.producer != producer
                or not sink_ref
                or len(sink_ref) > 256
                or goal_delivery_ref is not None
                or not {o.value for o in prompt.options} <= registration.values
            ):
                raise ValueError("unregistered confirm producer/sink/options")
        elif not goal_delivery_ref or self._admission is None or sink_ref is not None:
            raise ValueError("verified goal delivery reference and admission adapter required")
        payload = {
            "question": prompt.question,
            "options": [{"value": o.value, "label": o.label} for o in prompt.options],
            "timeout_seconds": prompt.timeout_seconds,
            "on_timeout_value": prompt.on_timeout_value,
            "facts": list(prompt.card.facts),
            "claims": list(prompt.card.claims),
            "quotes": list(prompt.card.quotes),
            "text": prompt.render_text(),
        }
        recipient = prompt.recipient
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO public.pending_confirms
                    (id,producer,producer_key,prompt,recipient_entity,recipient_sender,
                     recipient_chat,recipient_owner,sink,sink_ref,goal_delivery_ref,expires_at)
                VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9,$10,$11,
                        clock_timestamp()+$12::int*interval '1 second')
                ON CONFLICT (producer,producer_key) DO NOTHING RETURNING *
            """,
                uuid4(),
                producer,
                producer_key,
                payload,
                recipient.entity_id,
                recipient.sender_id,
                recipient.chat_id,
                recipient.owner,
                sink,
                sink_ref,
                goal_delivery_ref,
                prompt.timeout_seconds,
            )
            if row is None:
                row = await conn.fetchrow(
                    """SELECT * FROM public.pending_confirms
                    WHERE producer=$1 AND producer_key=$2""",
                    producer,
                    producer_key,
                )
                if (
                    row is None
                    or row["prompt"] != payload
                    or row["recipient_entity"] != recipient.entity_id
                    or row["recipient_sender"] != recipient.sender_id
                    or row["recipient_chat"] != recipient.chat_id
                    or row["recipient_owner"] != recipient.owner
                    or (row["sink"], row["sink_ref"], row["goal_delivery_ref"])
                    != (sink, sink_ref, goal_delivery_ref)
                ):
                    raise ValueError("producer key already binds a different immutable prompt")
        return dict(row)

    async def get(self, confirm_id: UUID) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM public.pending_confirms WHERE id=$1", confirm_id
            )
        return dict(row) if row else None

    async def list_owner(self, *, limit: int = 50) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM public.pending_confirms WHERE recipient_owner
                ORDER BY created_at DESC LIMIT $1""",
                min(max(limit, 1), 100),
            )
        return [dict(row) for row in rows]

    def keyboard(self, row: Mapping) -> dict:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": option["label"],
                        "callback_data": mint_confirm_token(
                            row["id"], index, chat_id=row["recipient_chat"], secret=self.secret
                        ),
                    }
                ]
                for index, option in enumerate(row["prompt"]["options"])
            ]
        }

    async def answer_callback(self, token: str, *, sender_id: str, chat_id: str) -> dict:
        verified = verify_confirm_token(token, chat_id=chat_id, secret=self.secret)
        if verified is None:
            return {"status": "refused"}
        return await self._answer(*verified, sender_id=sender_id, chat_id=chat_id)

    async def answer_owner(self, confirm_id: UUID, option: int) -> dict:
        """Only the authenticated dashboard boundary may call this owner entry point."""
        from butlers.core.owner_auth_context import verified_http_principal

        if verified_http_principal.get() != "owner":
            return {"status": "refused"}
        return await self._answer(confirm_id, option, owner=True)

    async def _answer(
        self,
        confirm_id: UUID,
        option: int,
        *,
        sender_id=None,
        chat_id=None,
        owner=False,
    ) -> dict:
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """SELECT *, expires_at <= clock_timestamp() AS elapsed
                FROM public.pending_confirms WHERE id=$1 FOR UPDATE""",
                confirm_id,
            )
            if (
                row is None
                or (owner and not row["recipient_owner"])
                or (
                    not owner
                    and (row["recipient_sender"] != sender_id or row["recipient_chat"] != chat_id)
                )
            ):
                return {"status": "refused"}
            if type(option) is not int or not 0 <= option < len(row["prompt"]["options"]):
                return {"status": "refused"}
            if row["status"] != "pending":
                return {
                    "status": "already_handled",
                    "state": row["status"],
                    "application_status": row["application_status"],
                }
            # Evaluate time after the lock wait, never from a statement-start snapshot.
            elapsed = await conn.fetchval(
                "SELECT $1::timestamptz <= clock_timestamp()", row["expires_at"]
            )
            status = "expired" if elapsed else "answered"
            await conn.execute(
                """UPDATE public.pending_confirms
                SET status=$2, answer_index=$3, answered_at=clock_timestamp(),
                    application_status='pending' WHERE id=$1""",
                confirm_id,
                status,
                None if elapsed else option,
            )
        return {"status": status, "application_status": "pending"}

    async def expire(self) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute("""UPDATE public.pending_confirms
                SET status='expired', answered_at=clock_timestamp(),application_status='pending'
                WHERE status='pending' AND expires_at <= clock_timestamp()""")
        return int(result.split()[-1])

    async def dispatch_one(self) -> bool:
        """Claim one durable obligation. Lost RPC replies retry the same domain key."""
        claim = uuid4()
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """WITH candidate AS (
                    SELECT id FROM public.pending_confirms
                    WHERE application_status IN ('pending','error')
                      AND (claim_until IS NULL OR claim_until <= clock_timestamp())
                    ORDER BY updated_at FOR UPDATE SKIP LOCKED LIMIT 1)
                UPDATE public.pending_confirms p SET claim_token=$1,
                    claim_until=clock_timestamp()+interval '30 seconds'
                FROM candidate c WHERE p.id=c.id RETURNING p.*""",
                claim,
            )
        if row is None:
            return False
        prompt = row["prompt"]
        value = (
            prompt["options"][row["answer_index"]]["value"]
            if row["status"] == "answered"
            else prompt["on_timeout_value"]
        )
        try:
            async with asyncio.timeout(15):
                if row["sink"] is not None:
                    registration = self._sinks.get(row["sink"])
                    if registration is None or registration.producer != row["producer"]:
                        raise ValueError("sink registration unavailable")
                    if value is None:
                        receipt = {"status": "applied", "outcome": "no_answer"}
                    elif value not in registration.values:
                        raise ValueError("sink option unavailable")
                    else:
                        receipt = await registration.apply(row["id"], row["sink_ref"], value)
                else:
                    if self._admission is None:
                        raise ValueError("admission consumer unavailable")
                    receipt = await self._admission(dict(row), value)
                if not isinstance(receipt, dict) or receipt.get("status") != "applied":
                    raise ValueError("application not confirmed")
        except Exception:
            # Error details may contain domain data; persist only a closed category.
            state, receipt = "error", {"code": "application_unconfirmed"}
        else:
            state = "applied"
        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE public.pending_confirms
                SET application_status=$3,application_receipt=$4::jsonb,
                    claim_token=NULL,claim_until=NULL
                WHERE id=$1 AND claim_token=$2""",
                row["id"],
                claim,
                state,
                receipt,
            )
        return True
