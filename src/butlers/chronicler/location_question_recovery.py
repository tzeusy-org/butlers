"""Removal-only recovery for an actual rejected receiving birth without a loan.

The fixed source plan and committed whole-source fence bind the original body.
These rows contain no delivery loan, admitted input or scheduling permission.
Every actual caller/server/context lifetime still requires its own producer.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_policy import PolicyUnavailableError


async def recover_rejected_questions(runtime: Any, plan: dict, question: dict) -> list[str]:
    """Own constructor/current attempt plus source-floor; never an absence verdict."""
    from butlers.core.delegation_source import _writers

    writer = _writers.get(runtime.domain)
    if not runtime.active or writer is None or writer.runtime is not runtime:
        raise PolicyUnavailableError("Native question recovery constructor differs")
    if question.get("complete_input") is not True or question.get("target_name") != runtime.name:
        return []
    source = plan.get("source_name", "chronicler")
    ledger, generation = UUID(question["ledger_id"]), UUID(question["question_generation"])
    root = dict(
        source_name=source,
        ledger_id=ledger,
        question_generation=generation,
        body_digest=bytes.fromhex(question["body_digest"]),
        decision_id=UUID(str(plan["decision_id"])),
        manifest_digest=bytes.fromhex(plan["manifest_digest"]),
    )
    # An actual known delivery loan uses the existing exact delivery-floor
    # path. It cannot be downgraded into this no-loan removal-only profile.
    deliveries = {UUID(loan["receiving_generation"]) for loan in question["loans"]}
    bindings = []
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_question_source_floors "
                "WHERE source_name=$1 AND ledger_id=$2",
                source,
                ledger,
            )
            if floor is None or any(floor[k] != v for k, v in root.items()):
                raise PolicyUnavailableError("Native question recovery source floor differs")
            attempts = await conn.fetch(
                "SELECT * FROM location_received_delegation_attempts "
                "WHERE ledger_id=$1 ORDER BY receiving_generation",
                ledger,
            )
            for attempt in attempts:
                receiving = attempt["receiving_generation"]
                if receiving in deliveries:
                    continue
                if (
                    attempt["source_name"] != source
                    or attempt["body_digest"] != root["body_digest"]
                    or attempt["receiving_incarnation"] != runtime.incarnation
                    or receiving in writer.receiving
                    or any(p.receiving == receiving for p in writer.pending.values())
                ):
                    continue
                # An unknown admission ACK stays on its actual admitted path.
                # No current lookup may reinterpret it as rejected processing.
                if (
                    await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_received_delegation_inputs "
                        "WHERE receiving_generation=$1) OR EXISTS(SELECT 1 FROM "
                        "location_received_delegation_schedules WHERE receiving_generation=$1) "
                        "OR EXISTS(SELECT 1 FROM location_received_delegation_floors "
                        "WHERE receiving_generation=$1)",
                        receiving,
                    )
                    is not False
                ):
                    continue
                if attempt["server_request"] is not None:
                    witnessed = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_received_delegation_server_finished "
                        "WHERE receiving_generation=$1 AND server_request=$2 AND body_digest=$3)",
                        receiving,
                        attempt["server_request"],
                        root["body_digest"],
                    )
                else:
                    witnessed = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_received_question_refusals r "
                        "JOIN location_runtime_tool_intents t USING(tool_generation) "
                        "WHERE r.receiving_generation=$1 AND r.tool_generation=$2 "
                        "AND t.receiving_session=$3 AND t.tool_name='delegate_receive' "
                        "AND t.module_name='core' AND r.receiving_incarnation=$4 "
                        "AND r.body_digest=$5)",
                        receiving,
                        attempt["tool_generation"],
                        attempt["receiving_session"],
                        runtime.incarnation,
                        root["body_digest"],
                    )
                if witnessed is not True:
                    continue
                binding = dict(
                    receiving_generation=receiving,
                    **root,
                    receiving_incarnation=runtime.incarnation,
                )
                await conn.execute(
                    "INSERT INTO location_received_question_recoveries "
                    "(receiving_generation,source_name,ledger_id,question_generation,body_digest,"
                    "decision_id,manifest_digest,receiving_incarnation) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING",
                    *binding.values(),
                )
                row = await conn.fetchrow(
                    "SELECT * FROM location_received_question_recoveries "
                    "WHERE receiving_generation=$1",
                    receiving,
                )
                if row is None or any(row[k] != v for k, v in binding.items()):
                    raise PolicyUnavailableError("Native question recovery binding differs")
                bindings.append((binding, attempt))
    receipts = []
    for binding, attempt in bindings:
        async with runtime.domain.acquire() as observed:
            row = await observed.fetchrow(
                "SELECT * FROM location_received_question_recoveries WHERE receiving_generation=$1",
                binding["receiving_generation"],
            )
        if row is None or any(row[k] != v for k, v in binding.items()):
            raise PolicyUnavailableError("Committed native question recovery is unknown")
        receipt = await _close_recovery(runtime, binding, attempt)
        if receipt is not None:
            receipts.append(str(receipt))
    return receipts


async def _close_recovery(runtime: Any, binding: dict, attempt: dict) -> UUID | None:
    """The recovery binding is a floor; only genuine own lifetime closes it."""
    from butlers.core.delegation_source import _writers

    writer = _writers.get(runtime.domain)
    if not runtime.active or writer is None or writer.runtime is not runtime:
        raise PolicyUnavailableError("Native question recovery terminal constructor differs")
    generation = binding["receiving_generation"]
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            row = await conn.fetchrow(
                "SELECT * FROM location_received_question_recoveries "
                "WHERE receiving_generation=$1 FOR UPDATE",
                generation,
            )
            if row is None or any(row[k] != v for k, v in binding.items()):
                raise PolicyUnavailableError("Native question recovery terminal binding differs")
            if generation in runtime.delegation_writer.receiving or any(
                p.receiving == generation for p in runtime.delegation_writer.pending.values()
            ):
                return None
            if (
                await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_received_delegation_inputs "
                    "WHERE receiving_generation=$1) OR EXISTS(SELECT 1 FROM "
                    "location_received_delegation_schedules WHERE receiving_generation=$1) "
                    "OR EXISTS(SELECT 1 FROM location_received_delegation_claims "
                    "WHERE receiving_generation=$1)",
                    generation,
                )
                is not False
            ):
                return None
            if attempt["server_request"] is not None:
                finished = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_received_delegation_server_finished "
                    "WHERE receiving_generation=$1 AND server_request=$2 AND body_digest=$3)",
                    generation,
                    attempt["server_request"],
                    binding["body_digest"],
                )
            else:
                from butlers.chronicler.location_question_refusals import (
                    closed_rejected_question_tool,
                )

                plan = dict(
                    decision_id=str(binding["decision_id"]),
                    manifest_digest=binding["manifest_digest"].hex(),
                )
                schema = '"' + runtime.identity[0].replace('"', '""') + '"'
                if not await closed_rejected_question_tool(
                    conn,
                    runtime,
                    schema,
                    attempt["receiving_session"],
                    plan,
                    attempt,
                ):
                    return None
                finished = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents t "
                    "JOIN location_runtime_context_bindings b "
                    "ON b.receiving_session=t.receiving_session "
                    "JOIN location_runtime_context_dispositions d USING(input_generation) "
                    "WHERE t.tool_generation=$1 AND t.receiving_session=$2)",
                    attempt["tool_generation"],
                    attempt["receiving_session"],
                )
            if finished is not True:
                return None
            receipt = await conn.fetchval(
                "SELECT receipt_id FROM location_received_question_recovery_dispositions "
                "WHERE receiving_generation=$1",
                generation,
            )
            if not runtime.active or _writers.get(runtime.domain) is not writer:
                raise PolicyUnavailableError("Native question recovery terminal constructor ended")
            if receipt is None:
                receipt = uuid4()
                await conn.execute(
                    "INSERT INTO location_received_question_recovery_dispositions "
                    "(receiving_generation,receipt_id) VALUES($1,$2)",
                    generation,
                    receipt,
                )
    async with runtime.domain.acquire() as observed:
        row = await observed.fetchrow(
            "SELECT r.*,d.receipt_id FROM location_received_question_recoveries r "
            "JOIN location_received_question_recovery_dispositions d USING(receiving_generation) "
            "WHERE r.receiving_generation=$1",
            generation,
        )
        if (
            not runtime.active
            or _writers.get(runtime.domain) is not writer
            or row is None
            or row["receipt_id"] != receipt
            or any(row[k] != v for k, v in binding.items())
        ):
            raise PolicyUnavailableError("Committed native question recovery terminal is unknown")
    return receipt
