"""Owning core-only receiving question context disposal.

The fixed native constructor and admitted immutable question bundle select the
copy. A source receipt cannot proxy its session lifetime or own domain writes;
no optional Memory pool is fabricated and no peer-private schema is queried.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from butlers.chronicler.location_policy import PolicyUnavailableError
from butlers.location_retention import content_digest


async def dispose_core_question_contexts(runtime: Any, binding: dict) -> None:
    await _dispose_core_delegated_contexts(runtime, binding)


async def dispose_core_answer_contexts(runtime: Any, binding: dict) -> None:
    await _dispose_core_delegated_contexts(runtime, binding, answer=True)


async def _dispose_core_delegated_contexts(
    runtime: Any, binding: dict, *, answer: bool = False
) -> None:
    """Exact owning delegated-prompt profile; Memory/Tool descendants stay separate.

    Preparation has already committed the permanent input floor. No context
    receipt is produced for missing intents, unfinished processing, additional
    copied input, executed tools, stored descendants or changed composed body.
    An independent system prefix survives; no remote recipient is attested.
    """
    from butlers.chronicler.location_delegation_runtime import NativeDelegationRuntime
    from butlers.chronicler.location_memory_context import context_writer
    from butlers.core.delegation_source import _writers

    writer = _writers.get(runtime.domain)
    if (
        not isinstance(runtime, NativeDelegationRuntime)
        or writer is None
        or writer.runtime is not runtime
        or context_writer(runtime.domain) is not runtime
        or not runtime.active
    ):
        return  # Actual Memory constructors require their configured owning writer.
    floor_table = (
        "location_received_answer_floors" if answer else "location_received_delegation_floors"
    )
    input_table = (
        "location_received_answer_inputs" if answer else "location_received_delegation_inputs"
    )
    input_keys = (
        ("source_name", "answer_generation", "loan_id", "bundle_digest", "source_incarnation")
        if answer
        else (
            "ledger_id",
            "source_name",
            "question_generation",
            "loan_id",
            "body_digest",
            "receiving_incarnation",
        )
    )
    committed_receipts = []
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            floor = await conn.fetchrow(
                f"SELECT * FROM {floor_table} WHERE receiving_generation=$1 FOR UPDATE",
                binding["receiving_generation"],
            )
            if floor is None or any(floor[key] != value for key, value in binding.items()):
                raise PolicyUnavailableError("Native context receiving floor differs")
            admitted = await conn.fetchrow(
                f"SELECT * FROM {input_table} WHERE receiving_generation=$1",
                binding["receiving_generation"],
            )
            if (
                admitted is None
                or any(admitted[key] != binding[key] for key in input_keys)
                or admitted["exclusive_input"] is not True
                or admitted["parent_count"] < 1
            ):
                return
            if answer:
                contexts = await conn.fetch(
                    "SELECT i.input_generation FROM location_runtime_context_answer_intents i "
                    "JOIN location_received_answer_claim_parents p USING(claim_generation) "
                    "WHERE p.receiving_generation=$1 ORDER BY i.input_generation",
                    binding["receiving_generation"],
                )
            else:
                contexts = await conn.fetch(
                    "SELECT i.input_generation FROM location_runtime_context_question_intents i "
                    "JOIN location_received_delegation_claims c USING(claim_generation) "
                    "WHERE c.receiving_generation=$1 ORDER BY i.input_generation",
                    binding["receiving_generation"],
                )
            for selected in contexts:
                generation = selected["input_generation"]
                previous = await conn.fetchrow(
                    "SELECT * FROM location_runtime_context_dispositions WHERE input_generation=$1",
                    generation,
                )
                if previous is not None:
                    if (
                        previous["decision_id"] != binding["decision_id"]
                        or previous["manifest_digest"] != binding["manifest_digest"]
                    ):
                        raise PolicyUnavailableError("Native context disposition binding differs")
                    committed_receipts.append((generation, previous["receipt_id"]))
                    continue
                frozen = await conn.fetchrow(
                    "SELECT b.*,i.server_request,e.receipt_id AS ended_receipt "
                    "FROM location_runtime_context_bindings b "
                    "JOIN location_runtime_context_intents i USING(input_generation) "
                    "LEFT JOIN location_runtime_context_ended e USING(input_generation) "
                    "WHERE b.input_generation=$1",
                    generation,
                )
                if (
                    frozen is None
                    or frozen["ended_receipt"] is None
                    or frozen["exclusive_input"] is not True
                    or frozen["context_bytes"] != 0
                ):
                    continue
                if frozen["server_request"] is not None and not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_context_server_finished "
                    "WHERE input_generation=$1 AND server_request=$2)",
                    generation,
                    frozen["server_request"],
                ):
                    continue
                if answer:
                    from butlers.chronicler.location_answer_disposal import (
                        closed_answer_context_input,
                    )
                    from butlers.chronicler.location_memory_context import _own_schema

                    schema = _own_schema(runtime)
                    if not await closed_answer_context_input(
                        conn, schema, runtime, frozen, generation, binding
                    ):
                        continue
                else:
                    captured = await conn.fetch(
                        "SELECT b.*,c.receiving_generation,c.prompt_digest,c.exclusive_input,"
                        "c.receiving_incarnation,i.claim_generation AS reserved_claim,"
                        "e.receipt_id AS ended_receipt "
                        "FROM location_received_delegation_contexts b "
                        "JOIN location_received_delegation_claims c USING(claim_generation) "
                        "LEFT JOIN location_runtime_context_question_intents i "
                        "ON i.input_generation=b.input_generation "
                        "LEFT JOIN location_received_delegation_claims_ended e "
                        "ON e.claim_generation=b.claim_generation "
                        "WHERE b.input_generation=$1",
                        generation,
                    )
                    if (
                        len(captured) != 1
                        or captured[0]["reserved_claim"] != captured[0]["claim_generation"]
                        or captured[0]["receiving_generation"] != binding["receiving_generation"]
                        or captured[0]["receiving_session"] != frozen["receiving_session"]
                        or captured[0]["bundle_digest"] != frozen["bundle_digest"]
                        or captured[0]["prompt_digest"] != frozen["prompt_digest"]
                        or captured[0]["exclusive_input"] is not True
                        or captured[0]["receiving_incarnation"] != runtime.incarnation
                        or captured[0]["ended_receipt"] is None
                    ):
                        continue
                # Complete question-only profile, not an absence-only shortcut.
                # Every additional actual input or descendant retains this copy.
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_catalog_copy_lifetimes "
                    "WHERE (holder_kind='unbound_processing' AND holder_id=$1) "
                    "OR (holder_kind='runtime_session' AND holder_id=$2)) "
                    "OR EXISTS(SELECT 1 FROM location_runtime_context_episodes "
                    "WHERE input_generation=$1) "
                    "OR EXISTS(SELECT 1 FROM location_runtime_context_artifacts "
                    "WHERE input_generation=$1) "
                    "OR EXISTS(SELECT 1 FROM location_runtime_tool_intents "
                    "WHERE receiving_session=$2) "
                    "OR EXISTS(SELECT 1 FROM location_native_delegation_inputs "
                    "WHERE context_generation=$1) "
                    "OR EXISTS(SELECT 1 FROM location_native_delegation_answers "
                    "WHERE context_generation=$1)",
                    generation,
                    frozen["receiving_session"],
                ):
                    continue
                session = await conn.fetchrow(
                    "SELECT * FROM sessions WHERE id=$1 FOR UPDATE OF sessions",
                    frozen["receiving_session"],
                )
                if (
                    session is None
                    or session["completed_at"] is None
                    or session["tool_calls"] != []
                    or not isinstance(session["prompt"], str)
                    or not isinstance(session["effective_system_prompt"], str)
                    or hashlib.sha256(session["prompt"].encode()).digest()
                    != frozen["prompt_digest"]
                    or hashlib.sha256(session["effective_system_prompt"].encode()).digest()
                    != frozen["system_digest"]
                    or frozen["context_digest"] != hashlib.sha256(b"").digest()
                    or content_digest(
                        {
                            "loans": [],
                            "context": frozen["context_digest"].hex(),
                            "system": frozen["system_digest"].hex(),
                            "prompt": frozen["prompt_digest"].hex(),
                        }
                    )
                    != frozen["bundle_digest"]
                ):
                    continue
                receipt = uuid4()
                await conn.execute(
                    "UPDATE session_process_logs SET command='[Location input forgotten]',"
                    "stderr=NULL WHERE session_id=$1",
                    frozen["receiving_session"],
                )
                # Keep the independent configured system and its provenance
                # byte exact. There was no appended Memory context in this profile.
                await conn.execute(
                    "UPDATE sessions SET prompt='[Location input forgotten]',"
                    "result='[Location output forgotten]',tool_calls='[]'::jsonb,error=NULL "
                    "WHERE id=$1",
                    frozen["receiving_session"],
                )
                await conn.execute(
                    "INSERT INTO location_runtime_context_dispositions "
                    "(input_generation,decision_id,manifest_digest,receipt_id,reduced_system_digest,"
                    "reduced_provenance_digest) VALUES($1,$2,$3,$4,$5,$6)",
                    generation,
                    binding["decision_id"],
                    binding["manifest_digest"],
                    receipt,
                    frozen["system_digest"],
                    content_digest(session["prompt_provenance"]),
                )
                committed_receipts.append((generation, receipt))
    # Separate actual outer-COMMIT readback, including preserved instructions.
    async with runtime.domain.acquire() as observed:
        for generation, receipt in committed_receipts:
            row = await observed.fetchrow(
                "SELECT d.*,b.receiving_session,b.system_digest "
                "FROM location_runtime_context_dispositions d "
                "JOIN location_runtime_context_bindings b USING(input_generation) "
                "WHERE d.input_generation=$1",
                generation,
            )
            session = (
                None
                if row is None
                else await observed.fetchrow(
                    "SELECT * FROM sessions WHERE id=$1",
                    row["receiving_session"],
                )
            )
            if (
                row is None
                or row["receipt_id"] != receipt
                or row["decision_id"] != binding["decision_id"]
                or row["manifest_digest"] != binding["manifest_digest"]
                or row["reduced_system_digest"] != row["system_digest"]
                or row["reduced_provenance_digest"] is None
                or session is None
                or content_digest(session["prompt_provenance"]) != row["reduced_provenance_digest"]
                or session["prompt"] != "[Location input forgotten]"
                or session["result"] != "[Location output forgotten]"
                or session["tool_calls"] != []
                or session["error"] is not None
                or not isinstance(session["effective_system_prompt"], str)
                or hashlib.sha256(session["effective_system_prompt"].encode()).digest()
                != row["system_digest"]
                or await observed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM session_process_logs WHERE session_id=$1 "
                    "AND ((command IS DISTINCT FROM '[Location input forgotten]' "
                    "AND command IS DISTINCT FROM '[Location-derived diagnostic forgotten]') "
                    "OR stderr IS NOT NULL))",
                    row["receiving_session"],
                )
            ):
                raise PolicyUnavailableError("Committed native core context disposal is unknown")


async def closed_question_context_input(
    conn: Any, schema: str, runtime: Any, frozen: Any, generation: Any, binding: dict
) -> bool:
    """Own configured Memory witness, never a Chronicle/private peer query.

    The schema is produced by the configured constructor. The binding is the
    previously committed own floor selected through the fixed source MCP plan.
    A smaller surviving JOIN or a receipt from another receiver cannot qualify.
    """
    if binding["receiving_incarnation"] != runtime.incarnation:
        return False
    floor = await conn.fetchrow(
        f"SELECT * FROM {schema}.location_received_delegation_floors "
        "WHERE receiving_generation=$1 FOR UPDATE",
        binding["receiving_generation"],
    )
    if floor is None or any(floor[k] != v for k, v in binding.items()):
        raise PolicyUnavailableError("Configured question context floor differs")
    admitted = await conn.fetchrow(
        f"SELECT * FROM {schema}.location_received_delegation_inputs WHERE receiving_generation=$1",
        binding["receiving_generation"],
    )
    if admitted is None or admitted["exclusive_input"] is not True or admitted["parent_count"] < 1:
        return False
    if any(
        admitted[k] != binding[k]
        for k in (
            "ledger_id",
            "source_name",
            "question_generation",
            "loan_id",
            "body_digest",
            "receiving_incarnation",
        )
    ):
        return False
    captured = await conn.fetch(
        f"SELECT b.*,c.receiving_generation,c.prompt_digest,c.exclusive_input,"
        "c.receiving_incarnation,i.claim_generation AS reserved_claim,"
        "e.receipt_id AS ended_receipt "
        f"FROM {schema}.location_received_delegation_contexts b "
        f"JOIN {schema}.location_received_delegation_claims c USING(claim_generation) "
        f"LEFT JOIN {schema}.location_runtime_context_question_intents i "
        "ON i.input_generation=b.input_generation "
        f"LEFT JOIN {schema}.location_received_delegation_claims_ended e "
        "ON e.claim_generation=b.claim_generation "
        "WHERE b.input_generation=$1",
        generation,
    )
    return len(captured) == 1 and all(
        (
            captured[0]["reserved_claim"] is not None,
            captured[0]["reserved_claim"] == captured[0]["claim_generation"],
            captured[0]["receiving_generation"] == binding["receiving_generation"],
            captured[0]["receiving_session"] == frozen["receiving_session"],
            captured[0]["bundle_digest"] == frozen["bundle_digest"],
            captured[0]["prompt_digest"] == frozen["prompt_digest"],
            captured[0]["exclusive_input"] is True,
            captured[0]["receiving_incarnation"] == runtime.incarnation,
            captured[0]["ended_receipt"] is not None,
        )
    )


async def dispose_memory_question_contexts(runtime: Any, binding: dict, plan: dict) -> None:
    """Select actual owning Memory contexts after the fixed source plan/floor.

    Never create a pool, assume a default identity or borrow the source owner's
    connection. Other catalog/local inputs remain independently required by
    the complete context engine's existing same-writer body/descendant checks.
    """
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_memory_context import context_writer, dispose_runtime_context

    if (
        not runtime.active
        or _runtimes.get(getattr(runtime, "memory", None)) is not runtime
        or context_writer(runtime.domain) is not runtime
    ):
        return
    if (
        str(plan["decision_id"]) != str(binding["decision_id"])
        or bytes.fromhex(plan["manifest_digest"]) != binding["manifest_digest"]
    ):
        raise PolicyUnavailableError("Configured question context plan differs")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_delegation_floors "
                "WHERE receiving_generation=$1 FOR UPDATE",
                binding["receiving_generation"],
            )
            if floor is None or any(floor[k] != v for k, v in binding.items()):
                raise PolicyUnavailableError("Configured question context floor differs")
            selected = await conn.fetch(
                "SELECT i.input_generation FROM location_runtime_context_question_intents i "
                "JOIN location_received_delegation_claims c USING(claim_generation) "
                "WHERE c.receiving_generation=$1 ORDER BY i.input_generation",
                binding["receiving_generation"],
            )
    for row in selected:
        await dispose_runtime_context(
            runtime, row["input_generation"], plan, question_binding=binding
        )
