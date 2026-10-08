"""Owning immutable question/answer census and registered terminal protocol.

Plan locators select actual stored source rows. The source's cohort never
proxies a receiver disposal receipt, and a ledger status never closes a copy.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_delegation_copies import question_digest
from butlers.chronicler.location_policy import PolicyUnavailableError


async def source_question_cohort(conn: Any, decision: UUID) -> list[dict]:
    """Full owning source parents, including missing/changed ancestry blockers.

    This is called inside the qualified Chronicler policy-first transaction.
    It does not replace receiver admission, processing or terminal readback.
    """
    selected = {
        (row["output_kind"], row["output_id"])
        for row in await conn.fetch(
            "SELECT output_kind,output_id FROM location_retention_plan_outputs "
            "WHERE decision_id=$1 ORDER BY output_kind,output_id",
            decision,
        )
    }
    result = []
    cursor = None
    while True:
        headers = await conn.fetch(
            "SELECT * FROM location_native_delegation_inputs "
            "WHERE ($1::uuid IS NULL OR question_generation>$1) "
            "ORDER BY question_generation LIMIT 64",
            cursor,
        )
        if not headers:
            break
        for header in headers:
            parents = await conn.fetch(
                "SELECT * FROM location_native_delegation_parents "
                "WHERE question_generation=$1 ORDER BY parent_kind,parent_generation",
                header["question_generation"],
            )
            complete = header["exclusive_input"] is True and len(parents) == header["parent_count"]
            # A missing declared parent cannot silently shrink this cohort.
            relevant = len(parents) != header["parent_count"]
            if len({(p["parent_kind"], p["parent_generation"]) for p in parents}) != len(parents):
                raise PolicyUnavailableError("Native question parent set differs")
            for parent in parents:
                if parent["parent_kind"] != "native_copy":
                    complete = False  # Requires its owning recursive dependency protocol.
                    continue
                births = await conn.fetch(
                    "SELECT * FROM location_native_copy_births WHERE copy_generation=$1 "
                    "ORDER BY output_kind,output_id",
                    parent["parent_generation"],
                )
                exact = bool(births) and all(
                    row["input_digest"] == parent["parent_digest"]
                    and row["lineage_known"] is True
                    and row["exclusive_input"] is True
                    for row in births
                )
                relevant = (
                    relevant
                    or not exact
                    or any((row["output_kind"], row["output_id"]) in selected for row in births)
                )
                complete = (
                    complete
                    and exact
                    and all((row["output_kind"], row["output_id"]) in selected for row in births)
                )
            if not relevant:
                continue
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                header["ledger_id"],
            )
            disposed = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_native_delegation_dispositions "
                "WHERE question_generation=$1 AND decision_id=$2 AND body_digest=$3 "
                "AND manifest_digest=(SELECT manifest_digest FROM location_retention_plans "
                "WHERE decision_id=$2))",
                header["question_generation"],
                decision,
                header["body_digest"],
            )
            if disposed:
                receipt = await conn.fetchrow(
                    "SELECT * FROM location_native_delegation_dispositions "
                    "WHERE question_generation=$1",
                    header["question_generation"],
                )
                if (
                    canonical is None
                    or receipt is None
                    or receipt["reduced_question_digest"] is None
                    or question_digest(dict(canonical)) != receipt["reduced_question_digest"]
                    or canonical["asking_butler"] != "chronicler"
                    or canonical["reason"] != _REDUCED_REASON
                    or not await _committed_question_profile(conn, receipt, canonical)
                ):
                    raise PolicyUnavailableError("Native reduced source question body changed")
            if not disposed and (
                canonical is None
                or question_digest(dict(canonical)) != header["body_digest"]
                or canonical["asking_butler"] != "chronicler"
            ):
                raise PolicyUnavailableError("Native source question body changed")
            loans = await conn.fetch(
                "SELECT * FROM location_native_delegation_loans "
                "WHERE question_generation=$1 ORDER BY loan_id",
                header["question_generation"],
            )
            result.append(
                {
                    "question_generation": str(header["question_generation"]),
                    "ledger_id": str(header["ledger_id"]),
                    "body_digest": header["body_digest"].hex(),
                    "parent_count": header["parent_count"],
                    "complete_input": complete,
                    "loans": [
                        {
                            key: row[key].hex() if isinstance(row[key], bytes) else str(row[key])
                            for key in (
                                "loan_id",
                                "receiver_name",
                                "receiving_generation",
                                "receiving_incarnation",
                                "body_digest",
                            )
                        }
                        for row in loans
                    ],
                }
            )
        cursor = headers[-1]["question_generation"]
    return result


_REDUCED_TASK = "Delegated input expired under the source retention policy."


def _floor_binding(runtime: Any, plan: dict, question: dict, loan: dict) -> dict:
    if (
        str(plan.get("decision_id")) == "None"
        or question.get("complete_input") is not True
        or loan.get("receiver_name") != runtime.name
        or loan.get("receiving_incarnation") != str(runtime.incarnation)
        or loan.get("body_digest") != question.get("body_digest")
    ):
        raise PolicyUnavailableError("Native receiving disposal cohort differs")
    try:
        binding = {
            "receiving_generation": UUID(loan["receiving_generation"]),
            "decision_id": UUID(str(plan["decision_id"])),
            "manifest_digest": bytes.fromhex(plan["manifest_digest"]),
            "source_name": "chronicler",
            "question_generation": UUID(question["question_generation"]),
            "ledger_id": UUID(question["ledger_id"]),
            "loan_id": UUID(loan["loan_id"]),
            "body_digest": bytes.fromhex(question["body_digest"]),
            "receiving_incarnation": runtime.incarnation,
        }
    except (ValueError, TypeError, KeyError):
        raise PolicyUnavailableError("Native receiving disposal binding differs") from None
    if len(binding["manifest_digest"]) != 32 or len(binding["body_digest"]) != 32:
        raise PolicyUnavailableError("Native receiving disposal digest differs")
    return binding


async def _close_question_receiver(runtime: Any, binding: dict) -> UUID | None:
    """Fence first, then dispose only this owning, fully reconstructed copy.

    The source plan came from the constructor's registered owning MCP route.
    An absent admitted row is terminal only after its exact durable attempt and
    finished own lifetime are observed behind the current-generation floor. An
    old incarnation is not evidence that its process died.
    Processing/context/descendant rows must already have their own receipts.
    """
    generation = binding["receiving_generation"]
    receipt = None
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_received_delegation_floors "
                "(receiving_generation,decision_id,manifest_digest,source_name,"
                "question_generation,ledger_id,loan_id,body_digest,receiving_incarnation) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING",
                *binding.values(),
            )
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_delegation_floors "
                "WHERE receiving_generation=$1 FOR UPDATE",
                generation,
            )
            if floor is None or any(floor[key] != value for key, value in binding.items()):
                raise PolicyUnavailableError("Native receiving floor differs")
            previous = await conn.fetchval(
                "SELECT receipt_id FROM location_received_delegation_dispositions "
                "WHERE receiving_generation=$1",
                generation,
            )
            if previous is not None:
                receipt = previous
            else:
                attempt = await conn.fetchrow(
                    "SELECT * FROM location_received_delegation_attempts "
                    "WHERE receiving_generation=$1",
                    generation,
                )
                # Missing durable attempt is unknown; source loan absence is
                # never a substitute for this receiver's actual lifetime.
                if attempt is None:
                    return None
                if (
                    attempt["ledger_id"] != binding["ledger_id"]
                    or attempt["body_digest"] != binding["body_digest"]
                    or attempt["receiving_incarnation"] != binding["receiving_incarnation"]
                ):
                    raise PolicyUnavailableError("Native receiving attempt differs")
                if generation in runtime.delegation_writer.receiving or any(
                    pending.receiving == generation
                    for pending in runtime.delegation_writer.pending.values()
                ):
                    return None
                if attempt["server_request"] is not None:
                    finished = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM "
                        "location_received_delegation_server_finished "
                        "WHERE receiving_generation=$1 AND server_request=$2 "
                        "AND body_digest=$3)",
                        generation,
                        attempt["server_request"],
                        binding["body_digest"],
                    )
                else:
                    # CLI receiving is itself a copied composed context.
                    # A Tool intent or terminal session flag is insufficient.
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
                admitted = await conn.fetchrow(
                    "SELECT * FROM location_received_delegation_inputs "
                    "WHERE receiving_generation=$1",
                    generation,
                )
                if admitted is not None:
                    if any(
                        admitted[key] != binding[key]
                        for key in (
                            "source_name",
                            "question_generation",
                            "ledger_id",
                            "loan_id",
                            "body_digest",
                            "receiving_incarnation",
                        )
                    ):
                        raise PolicyUnavailableError("Native receiving committed input differs")
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
                        return None
                    schedule = await conn.fetchrow(
                        "SELECT s.*,t.prompt,t.enabled "
                        "FROM location_received_delegation_schedules s "
                        "JOIN scheduled_tasks t ON t.id=s.task_id "
                        "WHERE s.receiving_generation=$1 FOR UPDATE OF t",
                        generation,
                    )
                    if schedule is not None:
                        if (
                            hashlib.sha256(schedule["prompt"].encode()).digest()
                            != schedule["prompt_digest"]
                        ):
                            raise PolicyUnavailableError("Native receiving task body changed")
                        await conn.execute(
                            "UPDATE scheduled_tasks SET enabled=false,prompt=$2 WHERE id=$1",
                            schedule["task_id"],
                            _REDUCED_TASK,
                        )
                        if (
                            await conn.fetchval(
                                "SELECT EXISTS(SELECT 1 FROM scheduled_tasks "
                                "WHERE id=$1 AND NOT enabled AND prompt=$2)",
                                schedule["task_id"],
                                _REDUCED_TASK,
                            )
                            is not True
                        ):
                            raise PolicyUnavailableError("Native receiving reduction differs")
                receipt = uuid4()
                await conn.execute(
                    "INSERT INTO location_received_delegation_dispositions "
                    "(receiving_generation,receipt_id) VALUES($1,$2)",
                    generation,
                    receipt,
                )
    # A lost ACK cannot become success. Reuse the immutable generation/floor
    # on retry; this separate acquisition observes the actual outer COMMIT.
    observed = await question_receiver_status(runtime, binding["decision_id"], receipt)
    if any(
        observed[key] != (value.hex() if isinstance(value, bytes) else str(value))
        for key, value in binding.items()
    ):
        raise PolicyUnavailableError("Committed native receiver disposition is unknown")
    return receipt


async def prepare_question_receivers(runtime: Any, decision: UUID) -> dict:
    """Locators select the actual Chronicle source plan over Switchboard MCP."""
    if not runtime.active:
        raise PolicyUnavailableError("Native question runtime ended")
    plan = await runtime.routed_tool(
        "chronicler",
        "chronicler_location_retention_status",
        {"decision_id": str(decision)},
    )
    if str(plan.get("decision_id")) != str(decision):
        raise PolicyUnavailableError("Native question source plan differs")
    receipts = []
    for question in plan.get("question_cohort", ()):
        for loan in question["loans"]:
            if loan.get("receiver_name") != runtime.name:
                continue
            binding = _floor_binding(runtime, plan, question, loan)
            receipt = await _close_question_receiver(runtime, binding)
            if receipt is None:
                from butlers.chronicler.location_delegation_contexts import (
                    dispose_core_question_contexts,
                    dispose_memory_question_contexts,
                )

                await dispose_core_question_contexts(runtime, binding)
                await dispose_memory_question_contexts(runtime, binding, plan)
                receipt = await _close_question_receiver(runtime, binding)
            if receipt is not None:
                receipts.append(str(receipt))
    return {"decision_id": str(decision), "receipt_ids": receipts}


async def question_receiver_status(runtime: Any, decision: UUID, receipt: UUID) -> dict:
    """Own exact floor+receipt, observed under the current configured identity."""
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            row = await conn.fetchrow(
                "SELECT f.*,d.receipt_id FROM location_received_delegation_floors f "
                "JOIN location_received_delegation_dispositions d USING(receiving_generation) "
                "WHERE f.decision_id=$1 AND d.receipt_id=$2",
                decision,
                receipt,
            )
    if row is None:
        raise PolicyUnavailableError("Native receiving receipt is unavailable")
    return {
        key: row[key].hex() if isinstance(row[key], bytes) else str(row[key])
        for key in (
            "receiving_generation",
            "decision_id",
            "manifest_digest",
            "source_name",
            "question_generation",
            "ledger_id",
            "loan_id",
            "body_digest",
            "receiving_incarnation",
            "receipt_id",
        )
    }


async def reconcile_question_receivers(domain: Any, decision: UUID) -> None:
    """Observe exact owning receipts through Switchboard; never proxy erasure.

    The observation closes only the matched receiver generation. The source
    ledger/context/answer/return descendants still need their own dispositions.
    """
    from butlers.chronicler.location_retention import plan_status
    from butlers.core.delegation_source import _writers

    writer = _writers.get(domain)
    if writer is None or not writer.runtime.active or writer.runtime.name != "chronicler":
        return  # The actual source frontier remains incomplete, not an empty success.
    runtime = writer.runtime
    plan = await plan_status(domain, decision)
    expected = {
        loan["loan_id"]: (question, loan)
        for question in plan["question_cohort"]
        for loan in question["loans"]
    }
    for name in sorted({loan["receiver_name"] for _, loan in expected.values()}):
        prepared = await runtime.routed_tool(
            name,
            "location_retention_prepare_questions",
            {"decision_id": str(decision)},
        )
        if prepared.get("decision_id") != str(decision):
            raise PolicyUnavailableError("Native receiving preparation differs")
        for receipt in prepared.get("receipt_ids", ()):
            result = await runtime.routed_tool(
                name,
                "location_retention_question_status",
                {"decision_id": str(decision), "receipt_id": receipt},
            )
            selected = expected.get(result.get("loan_id"))
            if selected is None:
                raise PolicyUnavailableError("Native receiving receipt loan differs")
            question, loan = selected
            if (
                question["complete_input"] is not True
                or loan["receiver_name"] != name
                or result.get("source_name") != "chronicler"
                or result.get("decision_id") != str(decision)
                or result.get("manifest_digest") != plan["manifest_digest"]
                or result.get("question_generation") != question["question_generation"]
                or result.get("receipt_id") != receipt
                or any(
                    result.get(key) != loan[key]
                    for key in (
                        "receiving_generation",
                        "receiving_incarnation",
                        "body_digest",
                    )
                )
            ):
                raise PolicyUnavailableError("Native receiving receipt binding differs")
            async with domain.acquire() as conn:
                async with conn.transaction():
                    await runtime.lock_domain(conn)
                    current = await conn.fetchrow(
                        "SELECT l.*,q.body_digest AS question_digest,p.manifest_digest "
                        "FROM location_native_delegation_loans l "
                        "JOIN location_native_delegation_inputs q USING(question_generation) "
                        "CROSS JOIN location_retention_plans p "
                        "WHERE l.loan_id=$1 AND p.decision_id=$2",
                        UUID(loan["loan_id"]),
                        decision,
                    )
                    if current is None or (
                        current["manifest_digest"].hex() != plan["manifest_digest"]
                        or current["question_digest"].hex() != question["body_digest"]
                        or any(
                            str(current[key]) != loan[key]
                            for key in (
                                "receiving_generation",
                                "receiving_incarnation",
                                "receiver_name",
                            )
                        )
                        or current["body_digest"].hex() != loan["body_digest"]
                    ):
                        raise PolicyUnavailableError("Native source loan readback differs")
                    await conn.execute(
                        "INSERT INTO location_retention_holder_receipts "
                        "(decision_id,owning_butler,holder_kind,holder_generation,"
                        "source_digest,receipt_id) VALUES($1,$2,'question_consumer',$3,$4,$5) "
                        "ON CONFLICT DO NOTHING",
                        decision,
                        name,
                        UUID(loan["loan_id"]),
                        bytes.fromhex(loan["body_digest"]),
                        UUID(receipt),
                    )
            committed = await domain.fetchval(
                "SELECT receipt_id FROM location_retention_holder_receipts "
                "WHERE decision_id=$1 AND owning_butler=$2 AND holder_kind='question_consumer' "
                "AND holder_generation=$3 AND source_digest=$4",
                decision,
                name,
                UUID(loan["loan_id"]),
                bytes.fromhex(loan["body_digest"]),
            )
            if committed != UUID(receipt):
                raise PolicyUnavailableError("Committed receiving observation is unknown")


_REDUCED_QUESTION = "Delegated location input forgotten under the source retention policy."
_REDUCED_REASON = "location_retention_expired"


def unanswered_source_question(row: Any) -> bool:
    """Only the owning unanswered ledger profile, never arbitrary metadata.

    A failed/routed status alone is no disposal witness. This profile merely
    selects the business row; complete source and receiving lifetimes must
    still close under the owning policy-first transaction.
    """
    return (
        row["asking_butler"] == "chronicler"
        and row["status"] in {"pending", "routed", "failed", "unroutable"}
        and row["metadata"] in (None, {})
        and row["wake_state"] == "not_applicable"
        and all(
            row[key] is None
            for key in (
                "answer",
                "answer_digest",
                "answered_at",
                "answering_butler",
                "wake_key",
                "wake_task_id",
                "wake_task_name",
                "wake_updated_at",
            )
        )
    )


async def dispose_source_questions(domain: Any, decision: UUID) -> None:
    """Dispose exact source ledger copies after every receiving disposition.

    Child/ledger disposal precedes source context disposal to avoid requiring
    each to attest the other. The immutable question receipt closes ONLY the
    source ledger and its observed receiver generations. The runtime context
    and its tool records remain an independent frontier until their own receipt.
    Answer and return copies use their separate protocol, never this profile.
    """
    from butlers.core.delegation_source import _writers

    writer = _writers.get(domain)
    if writer is None or not writer.runtime.active or writer.runtime.name != "chronicler":
        return
    runtime = writer.runtime
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            plan = await conn.fetchrow(
                "SELECT * FROM location_retention_plans WHERE decision_id=$1 FOR UPDATE",
                decision,
            )
            if plan is None or plan["state"] != "holder_pending":
                return
            cohort = await source_question_cohort(conn, decision)
            for question in cohort:
                if question["complete_input"] is not True:
                    continue
                generation = UUID(question["question_generation"])
                digest = bytes.fromhex(question["body_digest"])
                header = await conn.fetchrow(
                    "SELECT * FROM location_native_delegation_inputs WHERE question_generation=$1",
                    generation,
                )
                prior = await conn.fetchrow(
                    "SELECT * FROM location_native_delegation_dispositions "
                    "WHERE question_generation=$1",
                    generation,
                )
                if prior is not None:
                    if (
                        prior["decision_id"] != decision
                        or prior["manifest_digest"] != plan["manifest_digest"]
                        or prior["body_digest"] != digest
                    ):
                        raise PolicyUnavailableError("Native source disposition binding differs")
                    continue
                if any(pending.question == generation for pending in writer.pending.values()):
                    continue
                receivers_closed = True
                for loan in question["loans"]:
                    if not await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_retention_holder_receipts "
                        "WHERE decision_id=$1 AND owning_butler=$2 "
                        "AND holder_kind='question_consumer' AND holder_generation=$3 "
                        "AND source_digest=$4)",
                        decision,
                        loan["receiver_name"],
                        UUID(loan["loan_id"]),
                        bytes.fromhex(loan["body_digest"]),
                    ):
                        receivers_closed = False
                        break
                if not receivers_closed:
                    continue
                frozen = await conn.fetchrow(
                    "SELECT b.*,i.server_request,e.receipt_id AS ended_receipt "
                    "FROM location_runtime_context_bindings b "
                    "JOIN location_runtime_context_intents i USING(input_generation) "
                    "LEFT JOIN location_runtime_context_ended e USING(input_generation) "
                    "WHERE b.input_generation=$1 AND b.receiving_session=$2",
                    header["context_generation"],
                    header["receiving_session"],
                )
                if (
                    frozen is None
                    or frozen["exclusive_input"] is not True
                    or frozen["ended_receipt"] is None
                ):
                    continue
                if frozen["server_request"] is not None and not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_context_server_finished "
                    "WHERE input_generation=$1 AND server_request=$2)",
                    header["context_generation"],
                    frozen["server_request"],
                ):
                    continue
                if not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_results r "
                    "JOIN location_runtime_tool_intents t USING(tool_generation) "
                    "WHERE t.tool_generation=$1 AND t.receiving_session=$2 "
                    "AND t.tool_name='delegate_ask' AND t.module_name='core' "
                    "AND r.outcome='success' AND r.exclusive_inputs)",
                    header["tool_generation"],
                    header["receiving_session"],
                ):
                    continue
                session = await conn.fetchrow(
                    "SELECT * FROM sessions WHERE id=$1 FOR UPDATE OF sessions",
                    header["receiving_session"],
                )
                if (
                    session is None
                    or session["completed_at"] is None
                    or not isinstance(session["prompt"], str)
                    or not isinstance(session["effective_system_prompt"], str)
                    or hashlib.sha256(session["prompt"].encode()).digest()
                    != frozen["prompt_digest"]
                    or hashlib.sha256(session["effective_system_prompt"].encode()).digest()
                    != frozen["system_digest"]
                ):
                    continue
                canonical = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                    header["ledger_id"],
                )
                if canonical is None or question_digest(dict(canonical)) != digest:
                    raise PolicyUnavailableError("Native source ledger changed before disposal")
                from butlers.chronicler.location_question_sources import (
                    question_disposition_profile,
                )

                if not await question_disposition_profile(conn, header, canonical, plan):
                    continue
                from butlers.chronicler.location_question_sources import (
                    source_question_tool_finished,
                )

                if not await source_question_tool_finished(conn, header, session, canonical):
                    continue
                receipt = uuid4()
                await conn.execute(
                    "UPDATE public.delegation_ledger SET question=$2,status=$3,reason=$4 "
                    "WHERE id=$1",
                    header["ledger_id"],
                    _REDUCED_QUESTION,
                    "failed" if unanswered_source_question(canonical) else "answered",
                    _REDUCED_REASON,
                )
                await conn.execute(
                    "INSERT INTO location_native_delegation_dispositions "
                    "(question_generation,decision_id,manifest_digest,body_digest,receipt_id,"
                    "reduced_question_digest) VALUES($1,$2,$3,$4,$5,$6)",
                    generation,
                    decision,
                    plan["manifest_digest"],
                    digest,
                    receipt,
                    question_digest({**dict(canonical), "question": _REDUCED_QUESTION}),
                )
    # Observe the actual outer commit. An immutable receipt with a changed
    # public body is not success; retries reuse the same generation and receipt.
    async with domain.acquire() as committed:
        rows = await committed.fetch(
            "SELECT d.*,q.ledger_id FROM location_native_delegation_dispositions d "
            "JOIN location_native_delegation_inputs q USING(question_generation) "
            "WHERE d.decision_id=$1 ORDER BY d.question_generation",
            decision,
        )
        for row in rows:
            canonical = await committed.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1", row["ledger_id"]
            )
            if (
                canonical is None
                or canonical["question"] != _REDUCED_QUESTION
                or canonical["reason"] != _REDUCED_REASON
                or not await _committed_question_profile(committed, row, canonical)
                or row["reduced_question_digest"] is None
                or question_digest(dict(canonical)) != row["reduced_question_digest"]
            ):
                raise PolicyUnavailableError("Committed native source reduction is unknown")


async def source_question_status(runtime: Any, decision: UUID, receipt: UUID) -> dict:
    """Actual question owner's original reference plus full reduced profile.

    Locators select only own immutable receipts under the live constructor.
    This is a ledger child receipt, not a receiving/context/answer disposition.
    Prototype NULL reduced profiles stay unknown and are never backfilled.
    """
    if not runtime.active or runtime.name != "chronicler":
        raise PolicyUnavailableError("Native source question constructor differs")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            row = await conn.fetchrow(
                "SELECT d.*,q.ledger_id,q.body_digest AS original_digest,"
                "p.manifest_digest AS plan_manifest "
                "FROM location_native_delegation_dispositions d "
                "JOIN location_native_delegation_inputs q USING(question_generation) "
                "JOIN location_retention_plans p USING(decision_id) "
                "WHERE d.decision_id=$1 AND d.receipt_id=$2",
                decision,
                receipt,
            )
            if row is None or (
                row["body_digest"] != row["original_digest"]
                or row["manifest_digest"] != row["plan_manifest"]
                or row["reduced_question_digest"] is None
            ):
                raise PolicyUnavailableError("Native source question receipt is unavailable")
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 "
                "FOR UPDATE OF delegation_ledger",
                row["ledger_id"],
            )
            if (
                canonical is None
                or canonical["question"] != _REDUCED_QUESTION
                or canonical["reason"] != _REDUCED_REASON
                or not await _committed_question_profile(conn, row, canonical)
                or question_digest(dict(canonical)) != row["reduced_question_digest"]
            ):
                raise PolicyUnavailableError("Committed native source question is unknown")
            selected = [
                q
                for q in await source_question_cohort(conn, decision)
                if q["question_generation"] == str(row["question_generation"])
            ]
            if len(selected) != 1 or selected[0]["complete_input"] is not True:
                raise PolicyUnavailableError("Native source question ancestry is unknown")
            observed_answer = await conn.fetchrow(
                "SELECT * FROM location_native_question_answer_observations "
                "WHERE question_generation=$1",
                row["question_generation"],
            )
    return {
        "source_name": runtime.name,
        "decision_id": str(decision),
        "manifest_digest": row["manifest_digest"].hex(),
        "receipt_id": str(receipt),
        "ledger_id": str(row["ledger_id"]),
        "question_generation": str(row["question_generation"]),
        "body_digest": row["body_digest"].hex(),
        "reduced_question_digest": row["reduced_question_digest"].hex(),
        "answer_generation": str(observed_answer["answer_generation"])
        if observed_answer is not None
        else None,
        "answer_receipt": str(observed_answer["answer_receipt"])
        if observed_answer is not None
        else None,
    }


async def _committed_question_profile(conn: Any, row: Any, canonical: Any) -> bool:
    if canonical["status"] == "failed" and unanswered_source_question(canonical):
        return True
    from butlers.chronicler.location_question_sources import answered_question_matches

    observation = await conn.fetchrow(
        "SELECT * FROM location_native_question_answer_observations WHERE question_generation=$1",
        row["question_generation"],
    )
    return answered_question_matches(canonical, observation, row, row)
