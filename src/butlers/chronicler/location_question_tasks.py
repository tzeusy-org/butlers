"""Owning scheduled-copy disposition before the receiving caller context.

A permanent input floor qualifies this copy, but is not its terminal receipt.
The actual scheduled writer closes all spawned claims/contexts before reducing
its own task, then commits and separately reads the full original/reduced pair.
No peer role or a caller's completed verdict can attest that disposition.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from butlers.chronicler.location_policy import PolicyUnavailableError


async def prepare_question_task(runtime: Any, binding: dict) -> bool:
    from butlers.chronicler.location_delegation_disposal import _REDUCED_TASK

    generation = binding["receiving_generation"]
    receipt = None
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_delegation_floors "
                "WHERE receiving_generation=$1 FOR UPDATE",
                generation,
            )
            if floor is None or any(floor[k] != v for k, v in binding.items()):
                raise PolicyUnavailableError("Native question task floor differs")
            admitted = await conn.fetchrow(
                "SELECT * FROM location_received_delegation_inputs WHERE receiving_generation=$1",
                generation,
            )
            if binding["receiving_incarnation"] != runtime.incarnation:
                raise PolicyUnavailableError("Native question task incarnation differs")
            if admitted is None:
                return False  # Unaccepted attempts have no claimed scheduled child.
            if admitted["exclusive_input"] is not True or admitted["parent_count"] < 1:
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
                raise PolicyUnavailableError("Native question task input differs")
            attempt = await conn.fetchrow(
                "SELECT * FROM location_received_delegation_attempts WHERE receiving_generation=$1",
                generation,
            )
            if attempt is None or any(
                attempt[k] != binding[k]
                for k in (
                    "ledger_id",
                    "body_digest",
                    "receiving_incarnation",
                )
            ):
                return False
            if generation in runtime.delegation_writer.receiving or any(
                p.receiving == generation for p in runtime.delegation_writer.pending.values()
            ):
                return False
            if attempt["server_request"] is not None:
                finished = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_received_delegation_server_finished "
                    "WHERE receiving_generation=$1 AND server_request=$2 AND body_digest=$3)",
                    generation,
                    attempt["server_request"],
                    binding["body_digest"],
                )
            else:
                # The handler's actual private result ends the Tool processing
                # copy only; its caller context still needs its own disposition.
                finished = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents t "
                    "JOIN location_runtime_tool_results r USING(tool_generation) "
                    "WHERE t.tool_generation=$1 AND t.receiving_session=$2 "
                    "AND t.module_name='core' AND t.tool_name='delegate_receive' "
                    "AND r.outcome='success' AND r.exclusive_inputs IS TRUE)",
                    attempt["tool_generation"],
                    attempt["receiving_session"],
                )
            if finished is not True:
                return False
            # Every spawned processing/input intent, including unaccepted
            # contexts, must close. Zero rows are meaningful only after the
            # original floor prevents subsequent claim/session admission.
            unresolved = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_received_delegation_claims c "
                "WHERE c.receiving_generation=$1 AND (NOT EXISTS("
                "SELECT 1 FROM location_received_delegation_claims_ended e "
                "WHERE e.claim_generation=c.claim_generation) OR EXISTS("
                "SELECT 1 FROM location_runtime_context_question_intents i "
                "WHERE i.claim_generation=c.claim_generation AND NOT EXISTS("
                "SELECT 1 FROM location_runtime_context_dispositions d "
                "WHERE d.input_generation=i.input_generation))))",
                generation,
            )
            if unresolved is not False:
                return False
            task = await conn.fetchrow(
                "SELECT s.*,t.prompt,t.enabled FROM location_received_delegation_schedules s "
                "JOIN scheduled_tasks t ON t.id=s.task_id "
                "WHERE s.receiving_generation=$1 FOR UPDATE OF t",
                generation,
            )
            if task is None:
                return False  # Missing schedule is not a scheduled-copy receipt.
            previous = await conn.fetchrow(
                "SELECT * FROM location_received_question_task_dispositions "
                "WHERE receiving_generation=$1",
                generation,
            )
            reduced_digest = hashlib.sha256(_REDUCED_TASK.encode()).digest()
            if previous is not None:
                if (
                    previous["task_id"] != task["task_id"]
                    or previous["decision_id"] != binding["decision_id"]
                    or previous["manifest_digest"] != binding["manifest_digest"]
                    or previous["original_prompt_digest"] != task["prompt_digest"]
                    or previous["reduced_prompt_digest"] != reduced_digest
                    or task["enabled"] is not False
                    or task["prompt"] != _REDUCED_TASK
                ):
                    raise PolicyUnavailableError("Native question task disposition differs")
                receipt = previous["receipt_id"]
            else:
                if (
                    not isinstance(task["prompt"], str)
                    or hashlib.sha256(task["prompt"].encode()).digest() != task["prompt_digest"]
                ):
                    raise PolicyUnavailableError("Native question task original body differs")
                receipt = uuid4()
                await conn.execute(
                    "UPDATE scheduled_tasks SET enabled=false,prompt=$2 WHERE id=$1",
                    task["task_id"],
                    _REDUCED_TASK,
                )
                await conn.execute(
                    "INSERT INTO location_received_question_task_dispositions "
                    "(receiving_generation,task_id,decision_id,manifest_digest,original_prompt_digest,"
                    "reduced_prompt_digest,receipt_id) VALUES($1,$2,$3,$4,$5,$6,$7)",
                    generation,
                    task["task_id"],
                    binding["decision_id"],
                    binding["manifest_digest"],
                    task["prompt_digest"],
                    reduced_digest,
                    receipt,
                )
    async with runtime.domain.acquire() as observed:
        row = await observed.fetchrow(
            "SELECT d.*,s.prompt_digest,t.prompt,t.enabled "
            "FROM location_received_question_task_dispositions d "
            "JOIN location_received_delegation_schedules s USING(receiving_generation,task_id) "
            "JOIN scheduled_tasks t ON t.id=d.task_id WHERE d.receiving_generation=$1",
            generation,
        )
    if (
        row is None
        or row["receipt_id"] != receipt
        or row["decision_id"] != binding["decision_id"]
        or row["manifest_digest"] != binding["manifest_digest"]
        or row["original_prompt_digest"] != row["prompt_digest"]
        or row["reduced_prompt_digest"] != hashlib.sha256(_REDUCED_TASK.encode()).digest()
        or row["enabled"] is not False
        or row["prompt"] != _REDUCED_TASK
    ):
        raise PolicyUnavailableError("Committed native question task is unknown")
    return True


async def closed_received_question_tools(
    conn: Any, runtime: Any, schema: str, session: Any, plan: dict
) -> list[dict]:
    """Every original own attempt of a Tool needs its exact scheduled-copy receipt.

    This breaks only the task/context receipt cycle. A floor alone, smaller
    admitted JOIN, current placeholder or another same-name Tool cannot qualify.
    Only a producer-recorded pre-admission refusal has a separate closed profile.
    Other unaccepted/error/ordinary/mixed attempts remain unresolved.
    """
    from uuid import UUID

    from butlers.chronicler.location_delegation_disposal import _REDUCED_TASK
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    attempts = await conn.fetch(
        f"SELECT * FROM {schema}.location_received_delegation_attempts "
        "WHERE receiving_session=$1 ORDER BY receiving_generation",
        session,
    )
    good, blocked = set(), set()
    for attempt in attempts:
        tool = attempt["tool_generation"]
        row = await conn.fetchrow(
            f"SELECT i.*,s.task_id,s.prompt_digest,d.decision_id,d.manifest_digest,"
            "d.original_prompt_digest,d.reduced_prompt_digest,t.prompt,t.enabled,"
            "f.source_name AS floor_source,f.body_digest AS floor_digest,"
            "f.question_generation AS floor_question,f.loan_id AS floor_loan,"
            "f.ledger_id AS floor_ledger,f.decision_id AS floor_decision,"
            "f.manifest_digest AS floor_manifest,f.receiving_incarnation AS floor_incarnation,"
            "r.outcome,r.result_digest,r.exclusive_inputs,"
            "n.module_name,n.tool_name,n.receiving_session AS tool_session "
            f"FROM {schema}.location_received_delegation_inputs i "
            f"JOIN {schema}.location_received_delegation_schedules s USING(receiving_generation) "
            f"JOIN {schema}.location_received_question_task_dispositions d "
            "USING(receiving_generation,task_id) "
            f"JOIN {schema}.location_received_delegation_floors f USING(receiving_generation) "
            f"JOIN {schema}.scheduled_tasks t ON t.id=s.task_id "
            f"JOIN {schema}.location_runtime_tool_results r ON r.tool_generation=i.tool_generation "
            f"JOIN {schema}.location_runtime_tool_intents n ON n.tool_generation=i.tool_generation "
            "WHERE i.receiving_generation=$1",
            attempt["receiving_generation"],
        )
        if row is None:
            from butlers.chronicler.location_question_refusals import closed_rejected_question_tool

            if await closed_rejected_question_tool(conn, runtime, schema, session, plan, attempt):
                good.add(tool)
            else:
                blocked.add(tool)
            continue
        if (
            attempt["receiving_incarnation"] != runtime.incarnation
            or row["receiving_incarnation"] != runtime.incarnation
            or row["receiving_session"] != session
            or row["tool_generation"] != tool
            or row["exclusive_input"] is not True
            or row["parent_count"] < 1
            or row["decision_id"] != UUID(str(plan["decision_id"]))
            or row["manifest_digest"] != bytes.fromhex(plan["manifest_digest"])
            or row["floor_decision"] != row["decision_id"]
            or row["floor_manifest"] != row["manifest_digest"]
            or row["floor_incarnation"] != runtime.incarnation
            or row["source_name"] != row["floor_source"]
            or row["question_generation"] != row["floor_question"]
            or row["loan_id"] != row["floor_loan"]
            or row["ledger_id"] != row["floor_ledger"]
            or row["ledger_id"] != attempt["ledger_id"]
            or row["body_digest"] != row["floor_digest"]
            or row["body_digest"] != attempt["body_digest"]
            or row["original_prompt_digest"] != row["prompt_digest"]
            or row["reduced_prompt_digest"] != hashlib.sha256(_REDUCED_TASK.encode()).digest()
            or row["prompt"] != _REDUCED_TASK
            or row["enabled"] is not False
            or row["module_name"] != "core"
            or row["tool_name"] != "delegate_receive"
            or row["tool_session"] != session
            or row["outcome"] != "success"
            or row["exclusive_inputs"] is not True
            or row["result_digest"]
            != bytes.fromhex(
                fingerprint_tool_call_payload(
                    dict(
                        status="scheduled",
                        ledger_id=str(row["ledger_id"]),
                        task_id=str(row["task_id"]),
                    )
                )
            )
        ):
            blocked.add(tool)
        else:
            good.add(tool)
    return [dict(tool_generation=tool) for tool in good - blocked]
