"""Owning scheduled return input before copied prompt processing.

Canonical task IDs and metadata select stored bindings only. The constructor
verifies every actual incoming copy at its fixed source endpoint, then commits
its complete parent bundle and separately reads it back before the runtime
receives prompt bytes. An ended processing receipt never disposes descendants.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_delegation_answers import answer_bundle_digest
from butlers.chronicler.location_policy import PolicyUnavailableError
from butlers.location_retention import content_digest

logger = logging.getLogger(__name__)


@dataclass
class _ScheduledAnswer:
    writer: Any
    generation: UUID
    task: UUID
    prompt_digest: bytes
    bundle_digest: bytes
    exclusive: bool
    active: bool = True


_scheduled_answer: ContextVar[_ScheduledAnswer | None] = ContextVar(
    "native_scheduled_answer_input", default=None
)


def current_scheduled_answer(pool: Any):
    binding = _scheduled_answer.get()
    if binding is None:
        return None
    if (
        not binding.active
        or not binding.writer.runtime.active
        or binding.writer.runtime.domain is not pool
    ):
        raise PolicyUnavailableError("Native return processing lifetime differs")
    return binding


def _bundle(rows: list, prompt: str) -> bytes:
    """Every declared schedule binding participates; an inner join cannot shrink it."""
    if not rows or len(rows) > 128:
        raise PolicyUnavailableError("Native return processing cohort is unavailable")
    parents = []
    for row in rows:
        if (
            any(
                not isinstance(row[key], UUID)
                for key in (
                    "declared_receiving",
                    "receiving_generation",
                    "answer_generation",
                    "loan_id",
                    "source_incarnation",
                )
            )
            or row["receiving_generation"] != row["declared_receiving"]
            or not isinstance(row["bundle_digest"], bytes)
            or len(row["bundle_digest"]) != 32
            or row["prompt_digest"] != hashlib.sha256(prompt.encode()).digest()
            or row["scheduled_prompt"] != prompt
            or type(row["exclusive_input"]) is not bool
            or type(row["parent_count"]) is not int
            or row["parent_count"] < 0
        ):
            raise PolicyUnavailableError("Native return complete input cohort differs")
        parents.append(
            [
                str(row["declared_receiving"]),
                str(row["answer_generation"]),
                str(row["loan_id"]),
                row["bundle_digest"].hex(),
                str(row["source_incarnation"]),
                row["parent_count"],
                row["exclusive_input"],
                row["source_name"],
                str(row["ledger_id"]),
                row["wake_key"],
                str(row["receiving_incarnation"]),
            ]
        )
    if len({p[0] for p in parents}) != len(parents):
        raise PolicyUnavailableError("Native return input generation is duplicated")
    return content_digest(
        {
            "native_return_inputs.v1": sorted(parents),
            "prompt": hashlib.sha256(prompt.encode()).hexdigest(),
        }
    )


async def _inputs(runtime: Any, conn: Any, task: UUID, prompt: str):
    await runtime.lock_domain(conn)
    rows = await conn.fetch(
        "SELECT s.receiving_generation AS declared_receiving,i.*,a.ledger_id,a.wake_key,"
        "a.receiving_incarnation,s.prompt_digest,t.prompt AS scheduled_prompt "
        "FROM location_received_answer_schedules s "
        "LEFT JOIN location_received_answer_inputs i USING(receiving_generation) "
        "LEFT JOIN location_received_answer_attempts a USING(receiving_generation) "
        "LEFT JOIN scheduled_tasks t ON t.id=s.task_id "
        "WHERE s.task_id=$1 ORDER BY s.receiving_generation",
        task,
    )
    if not rows:
        return []
    _bundle(rows, prompt)
    for row in rows:
        if row["receiving_incarnation"] != runtime.incarnation or await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_answer_floors "
            "WHERE receiving_generation=$1)",
            row["declared_receiving"],
        ):
            raise PolicyUnavailableError("Native return receiving input is fenced")
        canonical = await conn.fetchrow(
            "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE OF delegation_ledger",
            row["ledger_id"],
        )
        if (
            canonical is None
            or canonical["asking_butler"] != runtime.name
            or canonical["target_butler"] != row["source_name"]
            or canonical["wake_key"] != row["wake_key"]
            or answer_bundle_digest(canonical) != row["bundle_digest"]
        ):
            raise PolicyUnavailableError("Native return canonical input differs")
    return rows


@asynccontextmanager
async def scheduled_answer_scope(pool: Any, task: UUID, prompt: str):
    from butlers.chronicler.location_delegation_returns import _AnswerPending
    from butlers.core.delegation_source import _writers

    writer = _writers.get(pool)
    if writer is None:
        yield
        return
    runtime = writer.runtime
    async with pool.acquire() as conn:
        async with conn.transaction():
            selected = await _inputs(runtime, conn, task, prompt)
    if not selected:
        yield  # Explicit no stored return binding; no ancestry is minted.
        return
    bundle = _bundle(selected, prompt)
    deadline = time.monotonic() + 30
    for row in selected:
        writer.answer_pending = {
            k: p for k, p in writer.answer_pending.items() if p.deadline > time.monotonic()
        }
        if len(writer.answer_pending) >= 128:
            raise PolicyUnavailableError("Native return challenge capacity is unavailable")
        nonce = secrets.token_urlsafe(32)
        writer.answer_pending[nonce] = _AnswerPending(
            row["ledger_id"],
            row["source_name"],
            row["wake_key"],
            row["receiving_generation"],
            deadline,
            schedule=task,
        )
        try:
            observed = await runtime.exchange(
                await runtime.endpoint(row["source_name"]),
                nonce,
                {
                    "op": "answer_delivery",
                    "loan_id": str(row["loan_id"]),
                    "receiver": runtime.name,
                },
            )
            if observed != {
                "loan_id": str(row["loan_id"]),
                "answer_generation": str(row["answer_generation"]),
                "bundle_digest": row["bundle_digest"].hex(),
                "source_incarnation": str(row["source_incarnation"]),
                "parent_count": row["parent_count"],
                "exclusive_input": row["exclusive_input"],
            }:
                raise PolicyUnavailableError("Native return current source differs")
        finally:
            writer.answer_pending.pop(nonce, None)
    claim = uuid4()
    digest = hashlib.sha256(prompt.encode()).digest()
    exclusive = all(row["exclusive_input"] is True for row in selected)
    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await _inputs(runtime, conn, task, prompt)
            if (
                not runtime.active
                or deadline <= time.monotonic()
                or _bundle(current, prompt) != bundle
            ):
                raise PolicyUnavailableError("Native return processing source changed")
            await conn.execute(
                "INSERT INTO location_received_answer_claims "
                "(claim_generation,task_id,prompt_digest,bundle_digest,parent_count,"
                "receiving_incarnation,exclusive_input) VALUES($1,$2,$3,$4,$5,$6,$7)",
                claim,
                task,
                digest,
                bundle,
                len(selected),
                runtime.incarnation,
                exclusive,
            )
            for row in selected:
                await conn.execute(
                    "INSERT INTO location_received_answer_claim_parents "
                    "(claim_generation,receiving_generation,bundle_digest) VALUES($1,$2,$3)",
                    claim,
                    row["receiving_generation"],
                    row["bundle_digest"],
                )
    async with pool.acquire() as observed:
        committed = await observed.fetchrow(
            "SELECT * FROM location_received_answer_claims WHERE claim_generation=$1",
            claim,
        )
        parents = await observed.fetch(
            "SELECT receiving_generation,bundle_digest FROM location_received_answer_claim_parents "
            "WHERE claim_generation=$1",
            claim,
        )
    if (
        committed is None
        or any(
            committed[k] != v
            for k, v in {
                "task_id": task,
                "prompt_digest": digest,
                "bundle_digest": bundle,
                "parent_count": len(selected),
                "receiving_incarnation": runtime.incarnation,
                "exclusive_input": exclusive,
            }.items()
        )
        or len(parents) != len(selected)
        or {(p["receiving_generation"], p["bundle_digest"]) for p in parents}
        != {(p["receiving_generation"], p["bundle_digest"]) for p in selected}
    ):
        raise PolicyUnavailableError("Committed native return processing input is unknown")
    binding = _ScheduledAnswer(writer, claim, task, digest, bundle, exclusive)
    token = _scheduled_answer.set(binding)
    primary_failed = False
    try:
        yield
        if not await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_answer_contexts "
            "WHERE claim_generation=$1)",
            claim,
        ):
            raise PolicyUnavailableError("Native return runtime context is unbound")
    except BaseException:
        primary_failed = True
        raise
    finally:
        binding.active = False
        _scheduled_answer.reset(token)
        try:
            receipt = uuid4()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await runtime.lock_domain(conn)
                    await conn.execute(
                        "INSERT INTO location_received_answer_claims_ended "
                        "(claim_generation,receipt_id) VALUES($1,$2)",
                        claim,
                        receipt,
                    )
            if (
                await pool.fetchval(
                    "SELECT receipt_id FROM location_received_answer_claims_ended "
                    "WHERE claim_generation=$1",
                    claim,
                )
                != receipt
            ):
                raise PolicyUnavailableError(
                    "Committed native return processing lifetime is unknown"
                )
        except asyncio.CancelledError:
            raise
        except BaseException:
            if not primary_failed:
                raise
            logger.warning("Native return lifetime witness unavailable after primary failure")


async def bind_answer_context(conn: Any, binding: Any, prompt: str) -> None:
    selected = current_scheduled_answer(binding.runtime.domain)
    if selected is None:
        return
    if (
        hashlib.sha256(prompt.encode()).digest() != selected.prompt_digest
        or await conn.fetchval(
            "SELECT claim_generation FROM location_runtime_context_answer_intents "
            "WHERE input_generation=$1",
            binding.generation,
        )
        != selected.generation
    ):
        raise PolicyUnavailableError("Native return pre-context input differs")
    frozen = await conn.fetchrow(
        "SELECT * FROM location_runtime_context_bindings WHERE input_generation=$1",
        binding.generation,
    )
    if frozen is None or frozen["receiving_session"] != binding.session:
        raise PolicyUnavailableError("Native return composed context differs")
    await conn.execute(
        "INSERT INTO location_received_answer_contexts "
        "(input_generation,claim_generation,receiving_session,bundle_digest,claim_bundle_digest) "
        "VALUES($1,$2,$3,$4,$5)",
        binding.generation,
        selected.generation,
        binding.session,
        frozen["bundle_digest"],
        selected.bundle_digest,
    )
