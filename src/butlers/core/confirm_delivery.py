"""Fenced Messenger handoff and idempotent terminal rendering for stored confirms."""

import asyncio
from collections.abc import Awaitable, Callable

from butlers.core.confirm_service import ConfirmService


class ConfirmDelivery:
    """Composition binds these calls to the authenticated Messenger service only.

    A begun send never retries without a provider reconciliation primitive. Telegram
    cannot establish absence after a lost send response, so those sends stay ambiguous.
    Rendering retries only edit the known message, never resend or reapply an answer.
    """

    def __init__(
        self,
        service: ConfirmService,
        *,
        send: Callable[[dict, dict], Awaitable[dict]],
        edit: Callable[[dict, str], Awaitable[bool]],
        allowed: Callable[[dict], Awaitable[bool]],
    ):
        self.service, self.send, self.edit, self.allowed = service, send, edit, allowed

    async def deliver(self, confirm_id):
        async with self.service._connection() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM public.pending_confirms WHERE id=$1 FOR UPDATE", confirm_id
            )
            if row is None or row["delivery_status"] != "new" or row["status"] != "pending":
                return False
            if not await self.allowed(dict(row)):
                # Rotate deferred rows behind other work without changing identity.
                await conn.execute(
                    "UPDATE public.pending_confirms SET updated_at=clock_timestamp() WHERE id=$1",
                    confirm_id,
                )
                return False
            if await conn.fetchval(
                "SELECT $1::timestamptz <= clock_timestamp()", row["expires_at"]
            ):
                return False
            await conn.execute(
                "UPDATE public.pending_confirms SET delivery_status='sending' WHERE id=$1",
                confirm_id,
            )
        # Commit the begun boundary before any provider call. A crash cannot make it new again.
        state, reference = "ambiguous", None
        try:
            async with asyncio.timeout(30):
                receipt = await self.send(dict(row), self.service.keyboard(row))
            if receipt.get("status") == "confirmed" and isinstance(receipt.get("reference"), str):
                state, reference = "confirmed", receipt["reference"]
            elif receipt.get("status") == "not_attempted":
                state = "new"  # Only affirmative pre-send evidence permits another attempt.
        except Exception:
            pass  # Unknown/provider exception is never evidence of non-delivery.
        finally:
            # Cancellation still records ambiguity; a killed process leaves 'sending',
            # equally non-retryable, for read models and restart recovery.
            async with self.service._connection() as conn:
                await conn.execute(
                    """UPDATE public.pending_confirms
                    SET delivery_status=$2,message_ref=$3
                    WHERE id=$1 AND delivery_status='sending' """,
                    confirm_id,
                    state,
                    reference,
                )
        return state == "confirmed"

    async def render(self, confirm_id):
        # Serialize provider edits with application status updates. A stale editor
        # cannot overwrite a newer receipt and leave its rendered marker behind.
        async with self.service._connection() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM public.pending_confirms WHERE id=$1 FOR UPDATE SKIP LOCKED",
                confirm_id,
            )
            if row is None or row["status"] == "pending" or not row["message_ref"]:
                return False
            rendering = f"{row['status']}:{row['application_status']}"
            if row["rendered_status"] == rendering:
                return True
            try:
                async with asyncio.timeout(15):
                    if not await self.edit(dict(row), terminal_text(row)):
                        return False
            except Exception:
                return False
            await conn.execute(
                "UPDATE public.pending_confirms SET rendered_status=$2 WHERE id=$1",
                confirm_id,
                rendering,
            )
        return True

    async def cycle(self, limit=20):
        await self.service.expire()
        # Bound work per tick. Each application is leased; every RPC key is stable.
        attempted = []
        for _ in range(limit):
            key = await self.service.dispatch_one(exclude=tuple(attempted))
            if key is None:
                break
            attempted.append(key)
        async with self.service._connection() as conn:
            rows = await conn.fetch(
                """SELECT id FROM public.pending_confirms
                WHERE (status='pending' AND delivery_status='new')
                   OR (status<>'pending' AND message_ref IS NOT NULL
                       AND rendered_status IS DISTINCT FROM status||':'||application_status)
                ORDER BY updated_at LIMIT $1""",
                limit,
            )
        for row in rows:
            try:
                await self.deliver(row["id"])
                await self.render(row["id"])
            except Exception:
                # One source's unavailable policy/endpoint must not strand peers.
                # The durable state remains available for another bounded tick.
                import logging

                logging.getLogger(__name__).warning("Confirm delivery reconciliation unavailable")
            finally:
                # Failed policy reads and failed terminal edits must rotate too:
                # otherwise one poisoned batch permanently strands all newer work.
                async with self.service._connection() as conn:
                    await conn.execute(
                        "UPDATE public.pending_confirms SET updated_at=clock_timestamp() "
                        "WHERE id=$1",
                        row["id"],
                    )


def terminal_text(row):
    if row["status"] == "expired":
        outcome = "Expired: no answer."
    else:
        label = row["prompt"]["options"][row["answer_index"]]["label"]
        outcome = f"Answer: {label}. " + (
            "Recorded." if row["application_status"] == "applied" else "Application pending."
        )
    return row["prompt"]["text"] + "\n\n" + outcome
