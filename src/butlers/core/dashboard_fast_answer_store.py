"""Narrow owning SQL adapter for the dashboard fast runtime.

No wall-clock lease calculation or optimistic local completion occurs here.
Every write carries the database-issued generation and process owner.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from butlers.core.citations import normalize_citations

_REPLY_NAMESPACE = UUID("1322e81e-c48d-4b86-9eea-142d65782a40")


@dataclass(frozen=True)
class FastAnswerFence:
    message_id: UUID
    session_id: UUID
    owner_instance_id: UUID
    lease_generation: int


def reply_payload(message_id: UUID, conversation_id: UUID, text: str, sources: list[dict]) -> dict:
    """Bind deterministic identity to the whole conversation/text/source payload."""
    payload = {"conversation_id": str(conversation_id), "message": text, "sources": sources}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    labels = [f"{item['kind']}: {item['ref']} (as of {item['as_of']})" for item in sources]
    return {
        **payload,
        "digest": digest,
        "reply_id": str(uuid5(_REPLY_NAMESPACE, str(message_id))),
        "source_labels": labels,
        "citations": normalize_citations(labels).citations,
    }


def _object(value: Any) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or not isinstance(value.get("outcome"), str):
        raise RuntimeError("fast runtime control result unavailable")
    return value


class FastAnswerStore:
    """All calls use the Switchboard pool and its existing enforced runtime role."""

    def __init__(self, pool: Any):
        self.pool = pool

    async def register(
        self, message_id: UUID, request_id: UUID, session_id: UUID, owner: UUID
    ) -> dict:
        return _object(
            await self.pool.fetchval(
                "SELECT public.dashboard_fast_answer_register($1,$2,$3,$4)",
                message_id,
                request_id,
                session_id,
                owner,
            )
        )

    async def control(self, fence: FastAnswerFence, action: str, data: dict | None = None) -> dict:
        return _object(
            await self.pool.fetchval(
                "SELECT public.dashboard_fast_answer_control($1,$2,$3,$4,$5::jsonb)",
                fence.message_id,
                fence.owner_instance_id,
                fence.lease_generation,
                action,
                data or {},
            )
        )

    async def intent(self, message_id: UUID, lane: str) -> None:
        value = await self.pool.fetchval(
            "SELECT public.dashboard_turn_intent_lane($1,$2)",
            message_id,
            lane,
        )
        if value != lane:
            raise RuntimeError("dashboard intent lane unavailable")

    async def reconcile(self, owner: UUID) -> list[dict]:
        rows = await self.pool.fetch(
            "SELECT public.dashboard_fast_answer_reconcile($1) AS state", owner
        )
        return [
            json.loads(row["state"]) if isinstance(row["state"], str) else row["state"]
            for row in rows
        ]
