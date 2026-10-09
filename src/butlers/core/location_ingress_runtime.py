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


async def _require_ingress_runtime_source(conn: Any, captured: tuple):
    """Complete fixed owning source validation shared by both runtime paths."""
    runtime, child = captured
    if current_ingress_runtime_input(runtime.pool) != captured:
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
    return source


async def reserve_ingress_runtime(conn: Any, captured: tuple, context: Any, prompt: str) -> None:
    """Same pre-context transaction, complete original claimed source read."""
    if not isinstance(prompt, str):
        raise CopyFloorUnavailable("ingress_runtime_input_differs")
    _, child = captured
    source = await _require_ingress_runtime_source(conn, captured)
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


async def reserve_structured_ingress_input(
    pool: Any, *, prompt: str, system_prompt: str, tools: list[dict[str, Any]]
):
    """Actual structured classifier reserves before copying its input to the SDK.

    This is not a session or terminal witness. Ordinary unconfigured processing
    has no private ingress producer and retains the existing adapter contract.
    Each actual retry captures its own complete body, never a reduced parent set.
    """
    captured = current_ingress_runtime_input(pool)
    if captured is None:
        return None
    if (
        not isinstance(prompt, str)
        or not isinstance(system_prompt, str)
        or not isinstance(tools, list)
    ):
        raise CopyFloorUnavailable("ingress_structured_input_differs")
    from uuid import uuid4

    from butlers.core.location_ingress_copies import lock_ingress_census

    _, child = captured
    generation = uuid4()
    prompt_digest = hashlib.sha256(prompt.encode()).digest()
    system_digest = hashlib.sha256(system_prompt.encode()).digest()
    tools_digest = content_digest({"tools": tools})
    async with pool.acquire() as conn:
        async with conn.transaction():
            await lock_ingress_census(conn)
            source = await _require_ingress_runtime_source(conn, captured)
            await conn.execute(
                "INSERT INTO location_ingress_structured_inputs "
                "(input_generation,copy_generation,request_id,stored_digest,envelope_digest,"
                "prompt_digest,system_digest,tools_digest) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                generation,
                child.generation,
                source["request_id"],
                source["stored_digest"],
                child.envelope_digest,
                prompt_digest,
                system_digest,
                tools_digest,
            )
    async with pool.acquire() as observed:
        actual = await observed.fetchrow(
            "SELECT s.copy_generation,s.request_id,s.stored_digest,s.envelope_digest,"
            "s.prompt_digest,s.system_digest,s.tools_digest,a.request_id AS original_request,"
            "a.stored_digest AS original_digest FROM location_ingress_structured_inputs s "
            "LEFT JOIN location_ingress_accepted_inputs a USING(copy_generation) "
            "WHERE s.input_generation=$1",
            generation,
        )
    if actual is None or tuple(actual.values()) != (
        child.generation,
        source["request_id"],
        source["stored_digest"],
        child.envelope_digest,
        prompt_digest,
        system_digest,
        tools_digest,
        source["request_id"],
        source["stored_digest"],
    ):
        raise CopyFloorUnavailable("ingress_structured_commit_unknown")
    # The stored input generation supplies ancestry only. No adapter return,
    # handler status, label or this locator may close its processing/SDK copies.
    return generation


async def capture_structured_ingress_output(
    pool: Any, generation: Any, *, tool_calls: list[dict[str, Any]], text: str | None
) -> None:
    """Freeze original SDK result lineage BEFORE executing copied tool inputs.

    The private processing producer and the original committed input supply
    authority. This records a descendant, not an SDK/route terminal verdict.
    Even invalid-schema output stays bound to its actual captured attempt.
    """
    captured = current_ingress_runtime_input(pool)
    if captured is None:
        if generation is not None:
            raise CopyFloorUnavailable("ingress_structured_output_producer_differs")
        return
    if (
        generation is None
        or not isinstance(tool_calls, list)
        or (text is not None and not isinstance(text, str))
    ):
        raise CopyFloorUnavailable("ingress_structured_output_differs")
    from butlers.core.location_ingress_copies import lock_ingress_census

    _, child = captured
    output_digest = content_digest({"tool_calls": tool_calls, "text": text})
    async with pool.acquire() as conn:
        async with conn.transaction():
            await lock_ingress_census(conn)
            source = await _require_ingress_runtime_source(conn, captured)
            original = await conn.fetchrow(
                "SELECT copy_generation,request_id,stored_digest,envelope_digest "
                "FROM location_ingress_structured_inputs WHERE input_generation=$1 FOR SHARE",
                generation,
            )
            if original is None or tuple(original.values()) != (
                child.generation,
                source["request_id"],
                source["stored_digest"],
                child.envelope_digest,
            ):
                raise CopyFloorUnavailable("ingress_structured_output_ancestry_unknown")
            await conn.execute(
                "INSERT INTO location_ingress_structured_outputs "
                "(input_generation,output_digest) VALUES($1,$2)",
                generation,
                output_digest,
            )
    async with pool.acquire() as observed:
        actual = await observed.fetchrow(
            "SELECT o.output_digest,s.copy_generation,s.request_id,s.stored_digest,"
            "s.envelope_digest,a.request_id AS original_request,"
            "a.stored_digest AS original_digest FROM location_ingress_structured_outputs o "
            "LEFT JOIN location_ingress_structured_inputs s USING(input_generation) "
            "LEFT JOIN location_ingress_accepted_inputs a USING(copy_generation) "
            "WHERE o.input_generation=$1",
            generation,
        )
    if actual is None or tuple(actual.values()) != (
        output_digest,
        child.generation,
        source["request_id"],
        source["stored_digest"],
        child.envelope_digest,
        source["request_id"],
        source["stored_digest"],
    ):
        raise CopyFloorUnavailable("ingress_structured_output_commit_unknown")


class _StructuredSDK:
    """Private actual SDK Task identity, never a caller terminal selector."""

    def __init__(self, generation: Any, runtime: Any, child: Any) -> None:
        from uuid import uuid4

        self.generation, self.task_generation = generation, uuid4()
        self.runtime, self.child = runtime, child
        self.task: asyncio.Task | None = None
        self.reply: Any = None
        self.gate = asyncio.Event()
        self.invoke: Any = None


async def prepare_structured_ingress_sdk(pool: Any, generation: Any):
    """Claim the actual SDK Task before it receives the source-derived input.

    Successful Task completion ends ONLY this SDK coroutine. Its returned
    body has separate captured output and processing/routed holder obligations.
    Error/cancellation Tasks remain unresolved with their original exception;
    Task.done alone must never certify traceback-held copies.
    """
    from butlers.core.location_ingress_copies import lock_ingress_census

    captured = current_ingress_runtime_input(pool)
    if captured is None:
        if generation is not None:
            raise CopyFloorUnavailable("ingress_structured_sdk_producer_differs")
        return None
    runtime, child = captured
    if generation is None or len(runtime._structured_sdk) >= 1024:
        raise CopyFloorUnavailable("ingress_structured_sdk_unavailable")
    binding = _StructuredSDK(generation, runtime, child)

    async def call():
        await binding.gate.wait()
        binding.reply = await binding.invoke()
        # The Task never retains the body in Task.result(). The separate reply
        # holder remains live until transfer to the classifier/route consumers.
        return None

    binding.task = asyncio.create_task(call())
    runtime._structured_sdk[id(binding)] = binding
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await lock_ingress_census(conn)
                source = await _require_ingress_runtime_source(conn, captured)
                original = await conn.fetchrow(
                    "SELECT copy_generation,request_id,stored_digest,envelope_digest "
                    "FROM location_ingress_structured_inputs WHERE input_generation=$1 FOR SHARE",
                    generation,
                )
                if original is None or tuple(original.values()) != (
                    child.generation,
                    source["request_id"],
                    source["stored_digest"],
                    child.envelope_digest,
                ):
                    raise CopyFloorUnavailable("ingress_structured_sdk_ancestry_unknown")
                await conn.execute(
                    "INSERT INTO location_ingress_structured_sdk_births "
                    "(input_generation,task_generation,handler_generation,incarnation) "
                    "VALUES($1,$2,$3,$4)",
                    generation,
                    binding.task_generation,
                    child.handler,
                    runtime.incarnation,
                )
        async with pool.acquire() as observed:
            actual = await observed.fetchrow(
                "SELECT task_generation,handler_generation,incarnation "
                "FROM location_ingress_structured_sdk_births WHERE input_generation=$1",
                generation,
            )
        if actual is None or tuple(actual.values()) != (
            binding.task_generation,
            child.handler,
            runtime.incarnation,
        ):
            raise CopyFloorUnavailable("ingress_structured_sdk_commit_unknown")
    except BaseException:
        # The gated child has not copied or invoked SDK input. Its interrupted
        # recorded claim stays pending; this cancellation grants no native end.
        binding.task.cancel()
        await asyncio.gather(binding.task, return_exceptions=True)
        raise
    return binding


def _require_structured_sdk(pool: Any, binding: _StructuredSDK):
    captured = current_ingress_runtime_input(pool)
    if (
        captured is None
        or captured[0] is not binding.runtime
        or captured[1] is not binding.child
        or binding.runtime._structured_sdk.get(id(binding)) is not binding
    ):
        raise CopyFloorUnavailable("ingress_structured_sdk_producer_differs")
    return captured


async def start_structured_ingress_sdk(pool: Any, binding: _StructuredSDK, invoke: Any) -> None:
    """Only the admitted original parent releases its own exact gated Task."""
    _require_structured_sdk(pool, binding)
    if binding.invoke is not None or binding.task.done() or binding.gate.is_set():
        raise CopyFloorUnavailable("ingress_structured_sdk_lifetime_differs")
    binding.invoke = invoke
    binding.gate.set()
    await binding.task  # Preserve the original SDK error/cancellation identity.


async def finish_structured_ingress_sdk(pool: Any, binding: _StructuredSDK):
    """Outside the SDK failure/fallback catch; transfer is not result disposal."""
    from uuid import uuid4

    from butlers.core.location_ingress_copies import lock_ingress_census

    captured = _require_structured_sdk(pool, binding)
    runtime, child = captured
    generation = binding.generation
    if (
        runtime._structured_sdk.get(id(binding)) is not binding
        or binding.task.cancelled()
        or not binding.task.done()
        or binding.task.exception() is not None
        or binding.task.result() is not None
        or binding.invoke is None
        or not binding.gate.is_set()
    ):
        raise CopyFloorUnavailable("ingress_structured_sdk_lifetime_differs")
    receipt = uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await lock_ingress_census(conn)
            await _require_ingress_runtime_source(conn, captured)
            actual = await conn.fetchrow(
                "SELECT task_generation,handler_generation,incarnation "
                "FROM location_ingress_structured_sdk_births "
                "WHERE input_generation=$1 FOR SHARE",
                generation,
            )
            if actual is None or tuple(actual.values()) != (
                binding.task_generation,
                child.handler,
                runtime.incarnation,
            ):
                raise CopyFloorUnavailable("ingress_structured_sdk_claim_differs")
            prior = await conn.fetchrow(
                "SELECT task_generation,receipt_id FROM location_ingress_structured_sdk_ends "
                "WHERE input_generation=$1 FOR SHARE",
                generation,
            )
            if prior is not None:
                from uuid import UUID

                if prior["task_generation"] != binding.task_generation or not isinstance(
                    prior["receipt_id"], UUID
                ):
                    raise CopyFloorUnavailable("ingress_structured_sdk_receipt_differs")
                # Only this same original eligible producer can recover its
                # committed ACK; no new receipt/body or Task authority is made.
                receipt = prior["receipt_id"]
            else:
                await conn.execute(
                    "INSERT INTO location_ingress_structured_sdk_ends "
                    "(input_generation,task_generation,receipt_id) VALUES($1,$2,$3)",
                    generation,
                    binding.task_generation,
                    receipt,
                )
    async with pool.acquire() as observed:
        actual = await observed.fetchrow(
            "SELECT task_generation,receipt_id FROM location_ingress_structured_sdk_ends "
            "WHERE input_generation=$1",
            generation,
        )
    if actual is None or tuple(actual.values()) != (binding.task_generation, receipt):
        raise CopyFloorUnavailable("ingress_structured_sdk_end_unknown")
    reply, binding.reply = binding.reply, None
    binding.invoke = None
    runtime._structured_sdk.pop(id(binding))
    # No SDK claim/end closes the transferred result or its parent processing.
    return reply
