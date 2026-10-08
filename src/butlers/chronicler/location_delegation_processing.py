"""Actual scheduled question processing and runtime input binding.

The scheduler supplies its owning pool and canonical task; those locators only
select already admitted source/receiver history. The source must answer the
fixed live receiver challenge before this constructor reserves copied prompt
processing. No caller trigger string, UUID or returned session grants admission.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_delegation_copies import question_digest
from butlers.chronicler.location_policy import PolicyUnavailableError

logger = logging.getLogger(__name__)


@dataclass
class _ScheduledQuestion:
    writer: Any
    generation: UUID
    receiving: UUID
    prompt_digest: bytes
    exclusive: bool
    active: bool = True


_scheduled_question: ContextVar[_ScheduledQuestion | None] = ContextVar(
    "native_scheduled_question_input", default=None
)


def current_scheduled_question(pool: Any):
    current = _scheduled_question.get()
    if current is None:
        return None
    if (
        not current.active
        or not current.writer.runtime.active
        or current.writer.runtime.domain is not pool
    ):
        raise PolicyUnavailableError("Native scheduled processing lifetime differs")
    return current


@asynccontextmanager
async def scheduled_question_scope(pool: Any, task: UUID, prompt: str):
    from butlers.chronicler.location_delegation_receivers import (
        _QuestionPending,
        receiving_question_fenced,
    )
    from butlers.core.delegation_source import _writers

    writer = _writers.get(pool)
    if writer is None:
        yield
        return
    runtime = writer.runtime
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            selected = await conn.fetchrow(
                "SELECT i.*,s.task_id,s.prompt_digest,t.prompt AS scheduled_prompt "
                "FROM location_received_delegation_schedules s "
                "JOIN location_received_delegation_inputs i USING(receiving_generation) "
                "JOIN scheduled_tasks t ON t.id=s.task_id WHERE s.task_id=$1",
                task,
            )
            if selected is not None:
                if await receiving_question_fenced(conn, selected["receiving_generation"]):
                    raise PolicyUnavailableError("Native scheduled receiving input is fenced")
                canonical = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                    selected["ledger_id"],
                )
                if (
                    not runtime.active
                    or selected["receiving_incarnation"] != runtime.incarnation
                    or hashlib.sha256(selected["scheduled_prompt"].encode()).digest()
                    != selected["prompt_digest"]
                    or canonical is None
                    or canonical["target_butler"] != runtime.name
                    or question_digest(dict(canonical)) != selected["body_digest"]
                ):
                    raise PolicyUnavailableError("Native scheduled input source differs")
    if selected is None:
        yield
        return
    writer.pending = {k: p for k, p in writer.pending.items() if p.deadline > time.monotonic()}
    if len(writer.pending) >= 128:
        raise PolicyUnavailableError("Native scheduled input capacity is unavailable")
    nonce = secrets.token_urlsafe(32)
    pending = _QuestionPending(
        selected["ledger_id"],
        selected["source_name"],
        selected["body_digest"],
        time.monotonic() + 30,
        selected["receiving_generation"],
        None,
        schedule=task,
    )
    writer.pending[nonce] = pending
    try:
        verified = await runtime.exchange(
            await runtime.endpoint(selected["source_name"]),
            nonce,
            {
                "op": "question_delivery",
                "loan_id": str(selected["loan_id"]),
                "receiver": runtime.name,
            },
        )
        if pending.deadline <= time.monotonic() or verified != {
            "loan_id": str(selected["loan_id"]),
            "question_generation": str(selected["question_generation"]),
            "body_digest": selected["body_digest"].hex(),
            "source_incarnation": str(selected["source_incarnation"]),
        }:
            raise PolicyUnavailableError("Native scheduled current delivery differs")
    finally:
        writer.pending.pop(nonce, None)
    digest = hashlib.sha256(prompt.encode()).digest()
    generation = uuid4()
    exclusive = selected["exclusive_input"] is True and prompt == selected["scheduled_prompt"]
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            if pending.deadline <= time.monotonic() or not runtime.active:
                raise PolicyUnavailableError("Native scheduled input lifetime expired")
            if await receiving_question_fenced(conn, selected["receiving_generation"]):
                raise PolicyUnavailableError("Native scheduled receiving input is fenced")
            current = await conn.fetchrow(
                "SELECT * FROM scheduled_tasks WHERE id=$1 FOR UPDATE", task
            )
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                selected["ledger_id"],
            )
            if (
                current is None
                or current["prompt"] != selected["scheduled_prompt"]
                or canonical is None
                or canonical["status"] not in {"pending", "routed"}
                or question_digest(dict(canonical)) != selected["body_digest"]
            ):
                raise PolicyUnavailableError("Native scheduled current body differs")
            await conn.execute(
                "INSERT INTO location_received_delegation_claims "
                "(claim_generation,receiving_generation,task_id,prompt_digest,"
                "receiving_incarnation,exclusive_input) VALUES($1,$2,$3,$4,$5,$6)",
                generation,
                selected["receiving_generation"],
                task,
                digest,
                runtime.incarnation,
                exclusive,
            )
    async with runtime.domain.acquire() as conn:
        committed = await conn.fetchrow(
            "SELECT * FROM location_received_delegation_claims WHERE claim_generation=$1",
            generation,
        )
    if (
        committed is None
        or committed["receiving_generation"] != selected["receiving_generation"]
        or committed["task_id"] != task
        or committed["prompt_digest"] != digest
        or committed["receiving_incarnation"] != runtime.incarnation
        or committed["exclusive_input"] is not exclusive
    ):
        raise PolicyUnavailableError("Committed scheduled processing input is unknown")
    binding = _ScheduledQuestion(
        writer, generation, selected["receiving_generation"], digest, exclusive
    )
    token = _scheduled_question.set(binding)
    primary_failed = False
    try:
        yield
        if not await runtime.domain.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_delegation_contexts "
            "WHERE claim_generation=$1)",
            generation,
        ):
            raise PolicyUnavailableError("Native scheduled runtime input is unbound")
    except BaseException:
        primary_failed = True
        raise
    finally:
        binding.active = False
        _scheduled_question.reset(token)
        try:
            receipt = uuid4()
            async with runtime.domain.acquire() as conn:
                async with conn.transaction():
                    await runtime.lock_domain(conn)
                    await conn.execute(
                        "INSERT INTO location_received_delegation_claims_ended "
                        "(claim_generation,receipt_id) VALUES($1,$2)",
                        generation,
                        receipt,
                    )
            if (
                await runtime.domain.fetchval(
                    "SELECT receipt_id FROM location_received_delegation_claims_ended "
                    "WHERE claim_generation=$1",
                    generation,
                )
                != receipt
            ):
                raise PolicyUnavailableError("Committed scheduled processing lifetime is unknown")
        except BaseException:
            # Secondary witness failure cannot replace a handler failure or
            # cancellation. The missing/unknown receipt continues to hold the
            # copy; successful processing still refuses an unknown witness.
            if not primary_failed:
                raise
            logger.warning("Native scheduled lifetime witness unavailable after primary failure")


async def bind_question_context(conn: Any, binding: Any, prompt: str) -> None:
    selected = current_scheduled_question(binding.runtime.domain)
    if selected is None:
        return
    if hashlib.sha256(prompt.encode()).digest() != selected.prompt_digest:
        raise PolicyUnavailableError("Native scheduled composed prompt changed")
    if (
        await conn.fetchval(
            "SELECT claim_generation FROM location_runtime_context_question_intents "
            "WHERE input_generation=$1",
            binding.generation,
        )
        != selected.generation
    ):
        raise PolicyUnavailableError("Native question pre-context input differs")
    frozen = await conn.fetchrow(
        "SELECT * FROM location_runtime_context_bindings WHERE input_generation=$1",
        binding.generation,
    )
    if frozen is None or frozen["receiving_session"] != binding.session:
        raise PolicyUnavailableError("Native scheduled context reservation differs")
    await conn.execute(
        "INSERT INTO location_received_delegation_contexts "
        "(input_generation,claim_generation,receiving_session,bundle_digest) VALUES($1,$2,$3,$4)",
        binding.generation,
        selected.generation,
        binding.session,
        frozen["bundle_digest"],
    )
