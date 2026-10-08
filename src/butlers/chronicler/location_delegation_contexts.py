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
    """Exact owning question-only profile; Memory/Tool descendants stay separate.

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
    committed_receipts = []
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_delegation_floors "
                "WHERE receiving_generation=$1 FOR UPDATE",
                binding["receiving_generation"],
            )
            if floor is None or any(floor[key] != value for key, value in binding.items()):
                raise PolicyUnavailableError("Native context receiving floor differs")
            admitted = await conn.fetchrow(
                "SELECT * FROM location_received_delegation_inputs WHERE receiving_generation=$1",
                binding["receiving_generation"],
            )
            if (
                admitted is None
                or any(
                    admitted[key] != binding[key]
                    for key in (
                        "ledger_id",
                        "source_name",
                        "question_generation",
                        "loan_id",
                        "body_digest",
                        "receiving_incarnation",
                    )
                )
                or admitted["exclusive_input"] is not True
                or admitted["parent_count"] < 1
            ):
                return
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
                captured = await conn.fetch(
                    "SELECT b.*,c.receiving_generation,c.prompt_digest,c.exclusive_input,"
                    "c.receiving_incarnation,e.receipt_id AS ended_receipt "
                    "FROM location_received_delegation_contexts b "
                    "JOIN location_received_delegation_claims c USING(claim_generation) "
                    "LEFT JOIN location_received_delegation_claims_ended e USING(claim_generation) "
                    "WHERE b.input_generation=$1",
                    generation,
                )
                if (
                    len(captured) != 1
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
                    "(input_generation,decision_id,manifest_digest,receipt_id) VALUES($1,$2,$3,$4)",
                    generation,
                    binding["decision_id"],
                    binding["manifest_digest"],
                    receipt,
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
                or session is None
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
