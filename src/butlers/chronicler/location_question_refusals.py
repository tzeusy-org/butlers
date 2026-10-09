"""Actual receiving handler's pre-admission rejection, not an error verdict.

A private exception produced after attempt birth selects this owning stage.
It attests only that this invocation returned before any schedule admission;
source floors, caller context and final receiving lifetime close separately.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_policy import PolicyUnavailableError
from butlers.core.tool_call_capture import fingerprint_tool_call_payload

_REFUSED_RESULT = {"status": "error", "error": "Native question input is unavailable."}


def _refused_digest() -> bytes:
    return bytes.fromhex(fingerprint_tool_call_payload(_REFUSED_RESULT))


async def record_question_refusal(writer: Any, failure: Any) -> None:
    from butlers.chronicler.location_delegation_receivers import _QuestionReceiveRefusal
    from butlers.chronicler.location_tool_copies import current_tool_copy

    runtime = writer.runtime
    tool = current_tool_copy(runtime)
    if (
        not isinstance(failure, _QuestionReceiveRefusal)
        or not failure.active
        or failure.writer is not writer
        or tool is None
        or tool is not failure.tool
        or not tool.active
        or tool.module != "core"
        or tool.name != "delegate_receive"
        or not runtime.active
    ):
        raise PolicyUnavailableError("Native question rejection stage differs")
    pending = failure.pending
    if (
        pending.tool is not tool
        or pending.receiving in writer.receiving
        or any(p.receiving == pending.receiving for p in writer.pending.values())
    ):
        raise PolicyUnavailableError("Native question rejection lifetime differs")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            attempt = await conn.fetchrow(
                "SELECT * FROM location_received_delegation_attempts "
                "WHERE receiving_generation=$1 FOR UPDATE",
                pending.receiving,
            )
            if (
                attempt is None
                or attempt["ledger_id"] != pending.ledger
                or attempt["source_name"] != pending.source
                or attempt["body_digest"] != pending.digest
                or attempt["receiving_incarnation"] != runtime.incarnation
                or attempt["receiving_session"] != tool.session
                or attempt["tool_generation"] != tool.generation
                or attempt["server_request"] is not None
                or not tool.active
                or not runtime.active
            ):
                raise PolicyUnavailableError("Native question rejection attempt differs")
            # A failed readback after admission is unknown, never a fabricated
            # pre-admission terminal marker. This includes every durable child.
            if (
                await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_received_delegation_inputs "
                    "WHERE receiving_generation=$1) OR EXISTS(SELECT 1 FROM "
                    "location_received_delegation_schedules WHERE receiving_generation=$1)",
                    pending.receiving,
                )
                is not False
            ):
                raise PolicyUnavailableError("Native question rejection admission is unknown")
            if (
                await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents "
                    "WHERE tool_generation=$1 AND receiving_session=$2 "
                    "AND tool_name='delegate_receive' AND module_name='core')",
                    tool.generation,
                    tool.session,
                )
                is not True
            ):
                raise PolicyUnavailableError("Native question rejection Tool differs")
            receipt = uuid4()
            await conn.execute(
                "INSERT INTO location_received_question_refusals "
                "(receiving_generation,tool_generation,receiving_incarnation,body_digest,"
                "result_digest,receipt_id) VALUES($1,$2,$3,$4,$5,$6)",
                pending.receiving,
                tool.generation,
                runtime.incarnation,
                pending.digest,
                _refused_digest(),
                receipt,
            )
    async with runtime.domain.acquire() as observed:
        row = await observed.fetchrow(
            "SELECT * FROM location_received_question_refusals WHERE receiving_generation=$1",
            pending.receiving,
        )
    if (
        row is None
        or row["tool_generation"] != tool.generation
        or row["receiving_incarnation"] != runtime.incarnation
        or row["body_digest"] != pending.digest
        or row["result_digest"] != _refused_digest()
        or row["receipt_id"] != receipt
        or not tool.active
        or not runtime.active
    ):
        raise PolicyUnavailableError("Committed native question rejection is unknown")
    failure.active = False
    tool.read_observed = True  # Never clears a previously recorded mixed input.


async def closed_rejected_question_tool(
    conn: Any, runtime: Any, schema: str, session: Any, plan: dict, attempt: dict
) -> bool:
    """Exact producer stage plus owning source floor and private result.

    No legacy backfill, same-name proxy or error text is a terminal witness.
    The caller compares the complete original attempt census for every Tool.
    """
    row = await conn.fetchrow(
        f"SELECT f.*,d.tool_generation,d.body_digest AS rejection_digest,"
        "d.receiving_incarnation AS rejection_incarnation,d.result_digest AS rejection_result,"
        "r.outcome,r.result_digest,r.exclusive_inputs,"
        "t.module_name,t.tool_name,t.receiving_session AS tool_session "
        f"FROM {schema}.location_received_question_refusals d "
        f"JOIN {schema}.location_received_delegation_floors f USING(receiving_generation) "
        f"JOIN {schema}.location_runtime_tool_results r USING(tool_generation) "
        f"JOIN {schema}.location_runtime_tool_intents t USING(tool_generation) "
        "WHERE d.receiving_generation=$1 AND NOT EXISTS(SELECT 1 FROM "
        f"{schema}.location_received_delegation_inputs i "
        "WHERE i.receiving_generation=d.receiving_generation) AND NOT EXISTS(SELECT 1 FROM "
        f"{schema}.location_received_delegation_schedules s "
        "WHERE s.receiving_generation=d.receiving_generation)",
        attempt["receiving_generation"],
    )
    return bool(
        row is not None
        and attempt["source_name"] is not None
        and attempt["receiving_incarnation"] == runtime.incarnation
        and row["receiving_incarnation"] == runtime.incarnation
        and row["rejection_incarnation"] == runtime.incarnation
        and row["ledger_id"] == attempt["ledger_id"]
        and row["source_name"] == attempt["source_name"]
        and row["body_digest"] == attempt["body_digest"] == row["rejection_digest"]
        and row["decision_id"] == UUID(str(plan["decision_id"]))
        and row["manifest_digest"] == bytes.fromhex(plan["manifest_digest"])
        and row["tool_generation"] == attempt["tool_generation"]
        and row["tool_session"] == attempt["receiving_session"] == session
        and attempt["server_request"] is None
        and row["module_name"] == "core"
        and row["tool_name"] == "delegate_receive"
        and row["outcome"] == "success"
        and row["exclusive_inputs"] is True
        and row["result_digest"] == row["rejection_result"] == _refused_digest()
    )
