"""Owning ingress processing ancestry for the actual configured runtime.

The private active processing producer supplies its original generation.
Neither an accepted UUID nor a caller prompt/session supplies that authority.
This relation reserves a descendant; it never certifies its disposal.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from butlers.core.location_copy_retention import CopyFloorUnavailable
from butlers.location_retention import content_digest


def native_ingress_error_redaction() -> bool:
    """Inherited private input only restricts diagnostics, never grants authority.

    An adapter invocation may run in a child Task of the original processing
    producer. Even a stale/mismatched scope must stay content-blind; original
    producer/currentness checks still govern admission and disposal separately.
    No request label, returned session or caller argument selects this flag.
    """
    from butlers.core.location_ingress_copies import _processing_scope

    return _processing_scope.get() is not None


def current_ingress_runtime_input(pool: Any):
    from butlers.core.location_ingress_copies import _processing_scope, _writers

    captured = _processing_scope.get()
    if captured is None:
        return None
    runtime, child = captured
    if (
        runtime.pool is not pool
        or _writers.get(pool) is not runtime
        or not runtime.active
        or runtime._inputs.get(id(child)) is not child
        or child.kind != 3
        or child.task is not asyncio.current_task()
        or child.task.done()
        or child.handler is None
    ):
        raise CopyFloorUnavailable("ingress_runtime_producer_differs")
    return captured


async def reserve_ingress_runtime(conn: Any, captured: tuple, context: Any, prompt: str) -> None:
    """Same pre-context transaction, complete original claimed source read."""
    runtime, child = captured
    if current_ingress_runtime_input(runtime.pool) != captured or not isinstance(prompt, str):
        raise CopyFloorUnavailable("ingress_runtime_input_differs")
    if tuple((await conn.fetchrow("SELECT current_schema(),current_user")).values()) != (
        "switchboard",
        "butler_switchboard_rw",
    ):
        raise CopyFloorUnavailable("ingress_runtime_identity_differs")
    source = await conn.fetchrow(
        "SELECT a.request_id,a.stored_digest,b.envelope_digest,c.handler_generation,"
        "c.incarnation,p.parent_generation,pa.request_id AS parent_request,"
        "pa.stored_digest AS parent_digest "
        "FROM location_ingress_input_births b "
        "LEFT JOIN location_ingress_input_claims c USING(copy_generation) "
        "LEFT JOIN location_ingress_accepted_inputs a USING(copy_generation) "
        "LEFT JOIN location_ingress_input_parents p USING(copy_generation) "
        "LEFT JOIN location_ingress_accepted_inputs pa "
        "ON pa.copy_generation=p.parent_generation WHERE b.copy_generation=$1",
        child.generation,
    )
    if (
        source is None
        or source["request_id"] is None
        or source["stored_digest"] is None
        or source["envelope_digest"] != child.envelope_digest
        or source["handler_generation"] != child.handler
        or source["incarnation"] != runtime.incarnation
        or source["parent_generation"] is None
        or source["parent_request"] != source["request_id"]
        or source["parent_digest"] != source["stored_digest"]
    ):
        raise CopyFloorUnavailable("ingress_runtime_ancestry_unknown")
    canonical = await conn.fetchrow(
        "SELECT raw_payload,normalized_text FROM message_inbox WHERE id=$1 FOR SHARE",
        source["request_id"],
    )
    if canonical is None or content_digest(dict(canonical)) != source["stored_digest"]:
        raise CopyFloorUnavailable("ingress_runtime_source_changed")
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_retention_source_floors WHERE dedupe_digest=$1)",
        child.dedupe_digest,
    ):
        raise CopyFloorUnavailable("ingress_runtime_source_disposed")
    await conn.execute(
        "INSERT INTO location_ingress_runtime_inputs "
        "(input_generation,copy_generation,receiving_session,request_id,stored_digest,"
        "envelope_digest,prompt_digest) VALUES($1,$2,$3,$4,$5,$6,$7)",
        context.generation,
        child.generation,
        context.session,
        source["request_id"],
        source["stored_digest"],
        child.envelope_digest,
        hashlib.sha256(prompt.encode()).digest(),
    )


async def verify_ingress_runtime(pool: Any, captured: tuple, context: Any, prompt: str) -> None:
    """Separate owning acquisition before processing the copied prompt."""
    _, child = captured
    async with pool.acquire() as observed:
        actual = await observed.fetchrow(
            "SELECT r.copy_generation,r.receiving_session,r.prompt_digest,r.envelope_digest,"
            "a.request_id AS original_request,a.stored_digest AS original_digest,"
            "r.request_id,r.stored_digest,i.receiving_session AS reserved_session "
            "FROM location_ingress_runtime_inputs r "
            "LEFT JOIN location_ingress_accepted_inputs a USING(copy_generation) "
            "LEFT JOIN location_runtime_context_intents i USING(input_generation) "
            "WHERE r.input_generation=$1",
            context.generation,
        )
    if (
        actual is None
        or actual["copy_generation"] != child.generation
        or actual["receiving_session"] != context.session
        or actual["reserved_session"] != context.session
        or actual["prompt_digest"] != hashlib.sha256(prompt.encode()).digest()
        or actual["envelope_digest"] != child.envelope_digest
        or actual["request_id"] != actual["original_request"]
        or actual["stored_digest"] != actual["original_digest"]
    ):
        raise CopyFloorUnavailable("ingress_runtime_commit_unknown")
