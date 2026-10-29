"""Verified, current interruption causes; model ranking is never authority.

Only the scheduler's claimed occurrence and the accepted-source verifier bind
this private context. Every consumer evaluates the current persisted policy.
Opaque receipts carry no message, address, subject, prompt, or bearer material.
"""

from __future__ import annotations

import contextvars
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)
_model_urgency_requested: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "untrusted_urgency_telemetry_only", default=False
)


@dataclass(frozen=True, slots=True)
class AttentionOrigin:
    namespace: str | None = None
    task_id: uuid.UUID | None = None
    original_next_run_at: datetime | None = None
    entity_id: uuid.UUID | None = None
    sender_domain: str | None = None
    channel: str | None = None
    security_kind: str | None = None

    def record(self) -> dict[str, str]:
        return {
            field: str(value)
            if isinstance(value, uuid.UUID)
            else value.isoformat()
            if isinstance(value, datetime)
            else value
            for field in self.__slots__
            if (value := getattr(self, field)) is not None
        }

    @classmethod
    def from_record(cls, record: dict) -> AttentionOrigin:
        if type(record) is not dict or set(record) - set(cls.__slots__):
            raise ValueError("Invalid accepted attention origin")
        if any(type(value) is not str for value in record.values()):
            raise ValueError("Invalid accepted attention origin")
        values = dict(record)
        for field in ("task_id", "entity_id"):
            if field in values:
                values[field] = uuid.UUID(values[field])
        if "original_next_run_at" in values:
            values["original_next_run_at"] = datetime.fromisoformat(values["original_next_run_at"])
            if values["original_next_run_at"].utcoffset() is None:
                raise ValueError("Invalid accepted attention origin")
        return cls(**values)


_current_origin: contextvars.ContextVar[AttentionOrigin | None] = contextvars.ContextVar(
    "private_attention_origin", default=None
)


@dataclass(frozen=True)
class InterruptionDecision:
    cause: str = "routine"
    ref: str | None = None

    @property
    def interrupts(self) -> bool:
        return self.cause in {"owner_creation", "owner_clause", "home_safety"}


async def evaluate_interruption(
    pool: Any,
    *,
    gate: str,
    origin: AttentionOrigin | None = None,
) -> InterruptionDecision:
    """Unknown/unavailable is routine. Public metadata is not an input."""
    origin = origin if origin is not None else _current_origin.get()
    if origin is None or pool is None:
        return InterruptionDecision()
    # A task UUID alone is insufficient: only a real claimed occurrence can
    # contribute a creation stamp. Diagnostic invocation/session IDs never do.
    values = origin.record()
    if origin.original_next_run_at is None:
        values.pop("task_id", None)
        values.pop("namespace", None)
    values["gate"] = gate
    try:
        raw = await pool.fetchval("SELECT dashboard_attention.evaluate($1::jsonb)", values)
        data = json.loads(raw) if isinstance(raw, str) else raw
        if type(data) is dict and data.get("cause") in {"owner_creation", "owner_clause"}:
            ref = str(uuid.UUID(data["ref"]))
            return InterruptionDecision(data["cause"], ref)
    except Exception:
        # Class-only attribution: provider/SQL exception text is never evidence.
        logger.warning("attention_authority_unavailable")
    return InterruptionDecision()


def accepted_source_origin(row: Any, entity_id: uuid.UUID | None) -> AttentionOrigin:
    """Called only on the owning, digest-verified accepted inbox record."""
    from butlers.core.account_security_events import (
        classify_ingest_record,
        sender_authentication_verdict,
    )

    context = row["request_context"] or {}
    channel = context.get("source_channel")
    channel = channel if channel in {"telegram", "email", "whatsapp", "dashboard"} else None
    sender = context.get("source_sender_identity")
    sender = sender.strip().lower() if type(sender) is str else ""
    domain = kind = None
    if (
        sender_authentication_verdict(
            source_channel=channel or "",
            sender_address=sender,
            raw=row["raw_payload"],
        )
        == "authenticated"
    ):
        domain = sender.rsplit("@", 1)[-1].lower()
        classification = classify_ingest_record(
            source_channel=channel or "",
            sender_address=sender,
            normalized_text=row["normalized_text"],
            raw=row["raw_payload"],
        )
        if classification is not None:
            kind = classification.kind
    return AttentionOrigin(
        entity_id=entity_id, sender_domain=domain, channel=channel, security_kind=kind
    )


async def remember_candidate_origin(pool: Any, candidate: uuid.UUID) -> None:
    """Persist only the private producer projection, bound to the exact candidate."""
    origin = _current_origin.get()
    if origin is None:
        return
    try:
        await pool.fetchval(
            "SELECT dashboard_attention.remember($1,$2::jsonb)", candidate, origin.record()
        )
    except Exception:
        # Failure cannot promote the candidate. It stays routine.
        logger.warning("attention_candidate_cause_unavailable")


async def candidate_interruption(
    pool: Any, candidate: uuid.UUID, *, gate: str
) -> InterruptionDecision:
    try:
        data = await pool.fetchval("SELECT dashboard_attention.candidate_origin($1)", candidate)
        data = json.loads(data) if isinstance(data, str) else data
        if type(data) is not dict:
            return InterruptionDecision()
        return await evaluate_interruption(
            pool, gate=gate, origin=AttentionOrigin.from_record(data)
        )
    except Exception:
        return InterruptionDecision()


def home_safety_decision() -> InterruptionDecision:
    """The deterministic sentinel calls this after its source/watch/debounce checks.

    This is a code-only producer seam, never a tool argument, metadata enum,
    job-name allowlist, or general Home notification exemption.
    """
    return InterruptionDecision("home_safety", "home:safety")
