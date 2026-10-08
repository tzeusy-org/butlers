"""Answer-owning terminal writes after fixed routed receiver observations.

Receiver status closes only its exact stored loan. The own canonical answer,
Tool records and source runtime context remain separate copies. No peer schema
is read; routes are constructor-fixed Switchboard operations, outside SQL.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_delegation_answers import answer_bundle_digest
from butlers.chronicler.location_delegation_copies import question_digest
from butlers.chronicler.location_policy import PolicyUnavailableError

_REDUCED_ANSWER = "Delegated answer forgotten under the source retention policy."
_REDUCED_DIGEST = hashlib.sha256(_REDUCED_ANSWER.encode()).digest()


def disposed_answer_matches(runtime: Any, header: Any, canonical: Any, receipt: Any, plan: dict):
    """Read the immutable original reference and actual reduced canonical profile.

    NULL prototype history never supplies an original body witness. This profile
    preserves the question/wake identity; a later question reduction needs its
    own separate original-question disposition rather than a guessed bundle.
    """
    return (
        canonical is not None
        and receipt is not None
        and receipt["answer_generation"] == header["answer_generation"]
        and receipt["decision_id"] == UUID(str(plan["decision_id"]))
        and receipt["manifest_digest"] == bytes.fromhex(plan["manifest_digest"])
        and receipt["body_digest"] == header["body_digest"]
        and receipt["bundle_digest"] == header["bundle_digest"]
        and header["bundle_digest"] is not None
        and receipt["question_digest"] == question_digest(dict(canonical))
        and receipt["wake_key"] == canonical["wake_key"]
        and isinstance(receipt["wake_key"], str)
        and canonical["id"] == header["ledger_id"]
        and canonical["status"] == "answered"
        and canonical["target_butler"] == runtime.name
        and canonical["answering_butler"] == runtime.name
        and canonical["answer_digest"] == header["body_digest"].hex()
        and canonical["answer"] == _REDUCED_ANSWER
        and receipt["reduced_digest"] == _REDUCED_DIGEST
    )


def receiver_observation_matches(runtime: Any, plan: dict, answer: dict, loan: dict, result: dict):
    """Exact own persisted loan/current constructor comparison, never a caller verdict."""
    return (
        answer.get("complete_input") is True
        and answer.get("source_name") == runtime.name
        and plan.get("source_name") == runtime.name
        and loan.get("source_incarnation") == str(runtime.incarnation)
        and loan.get("bundle_digest") == answer.get("bundle_digest")
        and result.get("source_name") == runtime.name
        and result.get("source_incarnation") == str(runtime.incarnation)
        and result.get("decision_id") == str(plan["decision_id"])
        and result.get("manifest_digest") == plan["manifest_digest"]
        and result.get("answer_generation") == answer["answer_generation"]
        and result.get("ledger_id") == answer["ledger_id"]
        and all(
            result.get(key) == loan[key]
            for key in ("loan_id", "receiving_generation", "receiving_incarnation", "bundle_digest")
        )
    )


async def reconcile_answer_receivers(runtime: Any, plan: dict) -> None:
    """Observe receivers through actual registered tools before own private COMMIT.

    A successful prepare with no terminal receipt is still pending. Status is
    reread on the owning route; the source then rechecks its stored loan/header
    under its own policy lock, commits only this observation and reads it back
    from a separate acquisition. Remote invocation never occurs in a DB txn.
    """
    for answer in plan["answer_cohort"]:
        if answer["complete_input"] is not True:
            continue
        for loan in answer["loans"]:
            if loan.get("source_incarnation") != str(runtime.incarnation):
                raise PolicyUnavailableError("Native answer source loan incarnation differs")
            prepared = await runtime.routed_tool(
                loan["receiver_name"],
                "location_retention_prepare_answer",
                {"decision_id": str(plan["decision_id"]), "loan_id": loan["loan_id"]},
            )
            if (
                prepared.get("decision_id") != str(plan["decision_id"])
                or prepared.get("loan_id") != loan["loan_id"]
            ):
                raise PolicyUnavailableError("Native answer receiving preparation differs")
            receipt = prepared.get("receipt_id")
            if receipt is None:
                continue
            try:
                receipt_id = UUID(receipt)
            except (TypeError, ValueError, AttributeError):
                raise PolicyUnavailableError("Native answer receiving receipt differs") from None
            result = await runtime.routed_tool(
                loan["receiver_name"],
                "location_retention_answer_status",
                {"decision_id": str(plan["decision_id"]), "receipt_id": str(receipt_id)},
            )
            if result.get("receipt_id") != str(receipt_id) or not receiver_observation_matches(
                runtime, plan, answer, loan, result
            ):
                raise PolicyUnavailableError("Native answer receiving observation differs")
            loan_id, decision = UUID(loan["loan_id"]), UUID(str(plan["decision_id"]))
            manifest = bytes.fromhex(plan["manifest_digest"])
            async with runtime.domain.acquire() as conn:
                async with conn.transaction():
                    await runtime.lock_domain(conn)
                    current = await conn.fetchrow(
                        "SELECT l.*,a.ledger_id,a.body_digest,a.bundle_digest AS answer_bundle "
                        "FROM location_native_answer_loans l "
                        "JOIN location_native_delegation_answers a USING(answer_generation) "
                        "WHERE l.loan_id=$1 FOR UPDATE OF l,a",
                        loan_id,
                    )
                    if current is None or (
                        str(current["answer_generation"]) != answer["answer_generation"]
                        or str(current["ledger_id"]) != answer["ledger_id"]
                        or current["body_digest"].hex() != answer["body_digest"]
                        or current["answer_bundle"].hex() != answer["bundle_digest"]
                        or current["bundle_digest"].hex() != loan["bundle_digest"]
                        or current["receiver_name"] != loan["receiver_name"]
                        or current["source_incarnation"] != runtime.incarnation
                        or any(
                            str(current[key]) != loan[key]
                            for key in ("receiving_generation", "receiving_incarnation")
                        )
                    ):
                        raise PolicyUnavailableError("Native answer own loan readback differs")
                    await conn.execute(
                        "INSERT INTO location_native_answer_observations "
                        "(loan_id,decision_id,manifest_digest,receiver_receipt) "
                        "VALUES($1,$2,$3,$4) "
                        "ON CONFLICT DO NOTHING",
                        loan_id,
                        decision,
                        manifest,
                        receipt_id,
                    )
            async with runtime.domain.acquire() as readback:
                observed = await readback.fetchrow(
                    "SELECT * FROM location_native_answer_observations WHERE loan_id=$1", loan_id
                )
            if observed is None or (
                observed["decision_id"] != decision
                or observed["manifest_digest"] != manifest
                or observed["receiver_receipt"] != receipt_id
            ):
                raise PolicyUnavailableError("Committed native answer observation is unknown")


async def source_answer_tool_finished(conn: Any, runtime: Any, header: Any, canonical: Any):
    """Original copied input/result witnesses plus the actual source context lifetime.

    The context remains a separate copy until its descendant engine closes.
    Its actual finished lifetime suffices to reduce this child first, avoiding
    an answer/context mutual-receipt cycle. All full source input bytes still
    match the frozen binding here; no session-completed-only authority.
    """
    from butlers.chronicler.location_tool_copies import matched_tool_records
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    frozen = await conn.fetchrow(
        "SELECT b.*,i.server_request,e.receipt_id AS ended_receipt "
        "FROM location_runtime_context_bindings b "
        "JOIN location_runtime_context_intents i USING(input_generation) "
        "LEFT JOIN location_runtime_context_ended e USING(input_generation) "
        "WHERE b.input_generation=$1 AND b.receiving_session=$2",
        header["context_generation"],
        header["receiving_session"],
    )
    if frozen is None or frozen["exclusive_input"] is not True or frozen["ended_receipt"] is None:
        return False
    if (
        frozen["server_request"] is not None
        and await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_runtime_context_server_finished "
            "WHERE input_generation=$1 AND server_request=$2)",
            header["context_generation"],
            frozen["server_request"],
        )
        is not True
    ):
        return False
    session = await conn.fetchrow(
        "SELECT * FROM sessions WHERE id=$1 FOR UPDATE OF sessions", header["receiving_session"]
    )
    if (
        session is None
        or session["completed_at"] is None
        or not isinstance(session["prompt"], str)
        or not isinstance(session["effective_system_prompt"], str)
        or hashlib.sha256(session["prompt"].encode()).digest() != frozen["prompt_digest"]
        or hashlib.sha256(session["effective_system_prompt"].encode()).digest()
        != frozen["system_digest"]
        or not isinstance(session["tool_calls"], list)
    ):
        return False
    witnesses = await conn.fetch(
        "SELECT t.*,r.outcome,r.result_digest,r.exclusive_inputs "
        "FROM location_runtime_tool_intents t "
        "LEFT JOIN location_runtime_tool_results r USING(tool_generation) "
        "WHERE t.receiving_session=$1 AND t.tool_name='delegate_answer' "
        "ORDER BY t.tool_generation",
        header["receiving_session"],
    )
    selected = [row for row in witnesses if row["tool_generation"] == header["tool_generation"]]
    expected = fingerprint_tool_call_payload(
        {"ledger_id": str(header["ledger_id"]), "answer": canonical["answer"]}
    )
    if (
        len(selected) != 1
        or selected[0]["module_name"] != "core"
        or selected[0]["exclusive_inputs"] is not True
        or selected[0]["input_digest"].hex() != expected
    ):
        return False
    calls = [
        call
        for call in session["tool_calls"]
        if isinstance(call, dict) and call.get("name") == "delegate_answer"
    ]
    try:
        if not matched_tool_records(calls, witnesses):
            return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    applicable = [
        call
        for call in calls
        if call.get("input_fingerprint") == expected
        and call.get("module") == "core"
        and bytes.fromhex(fingerprint_tool_call_payload(call.get("result")))
        == selected[0]["result_digest"]
    ]
    if not applicable:
        return False
    result = applicable[0].get("result")
    return (
        isinstance(result, dict)
        and set(result) == {"status", "ledger_id", "answer_recorded"}
        and result["status"] == "ok"
        and result["ledger_id"] == str(header["ledger_id"])
        and result["answer_recorded"] is True
    )


async def dispose_source_answers(runtime: Any, plan: dict) -> list[str]:
    """Reduce only exact source answers after every loan observation has committed."""
    from butlers.chronicler.location_answer_disposal import source_answer_cohort

    decision, manifest = UUID(str(plan["decision_id"])), bytes.fromhex(plan["manifest_digest"])
    receipts = []
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            # Recompute all current original parents and loans under the writer
            # lock. A stale pre-network cohort cannot authorize a smaller set.
            for answer in await source_answer_cohort(runtime, conn, plan):
                if answer["complete_input"] is not True:
                    continue
                generation = UUID(answer["answer_generation"])
                header = await conn.fetchrow(
                    "SELECT * FROM location_native_delegation_answers WHERE answer_generation=$1",
                    generation,
                )
                canonical = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 "
                    "FOR UPDATE OF delegation_ledger",
                    header["ledger_id"],
                )
                prior = await conn.fetchrow(
                    "SELECT * FROM location_native_delegation_answer_dispositions "
                    "WHERE answer_generation=$1",
                    generation,
                )
                if prior is not None:
                    if not disposed_answer_matches(runtime, header, canonical, prior, plan):
                        raise PolicyUnavailableError("Native source answer disposition differs")
                    receipts.append(str(prior["receipt_id"]))
                    continue
                closed = True
                for loan in answer["loans"]:
                    if (
                        loan.get("source_incarnation") != str(runtime.incarnation)
                        or await conn.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM location_native_answer_observations "
                            "WHERE loan_id=$1 AND decision_id=$2 AND manifest_digest=$3)",
                            UUID(loan["loan_id"]),
                            decision,
                            manifest,
                        )
                        is not True
                    ):
                        closed = False
                        break
                if not closed or not await source_answer_tool_finished(
                    conn, runtime, header, canonical
                ):
                    continue
                if answer_bundle_digest(canonical) != header["bundle_digest"]:
                    raise PolicyUnavailableError("Native source answer changed before reduction")
                receipt = uuid4()
                await conn.execute(
                    "UPDATE public.delegation_ledger SET answer=$2 WHERE id=$1",
                    header["ledger_id"],
                    _REDUCED_ANSWER,
                )
                await conn.execute(
                    "INSERT INTO location_native_delegation_answer_dispositions "
                    "(answer_generation,decision_id,manifest_digest,body_digest,receipt_id,"
                    "bundle_digest,question_digest,wake_key,reduced_digest) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                    generation,
                    decision,
                    manifest,
                    header["body_digest"],
                    receipt,
                    header["bundle_digest"],
                    question_digest(dict(canonical)),
                    canonical["wake_key"],
                    _REDUCED_DIGEST,
                )
                receipts.append(str(receipt))
    # No success on the write acquisition alone, including original-ID replay.
    for receipt in receipts:
        await source_answer_status(runtime, decision, UUID(receipt), plan=plan)
    return receipts


async def source_answer_status(
    runtime: Any, decision: UUID, receipt: UUID, *, plan: dict | None = None
):
    """Separate owning committed reduced-body/original-reference readback."""
    if plan is None:
        from butlers.chronicler.location_answer_disposal import answer_source_plan

        plan = await answer_source_plan(runtime, decision)
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            row = await conn.fetchrow(
                "SELECT * FROM location_native_delegation_answer_dispositions "
                "WHERE decision_id=$1 AND receipt_id=$2",
                decision,
                receipt,
            )
            if row is None:
                raise PolicyUnavailableError("Native source answer receipt is unavailable")
            header = await conn.fetchrow(
                "SELECT * FROM location_native_delegation_answers WHERE answer_generation=$1",
                row["answer_generation"],
            )
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 "
                "FOR UPDATE OF delegation_ledger",
                header["ledger_id"],
            )
            if not disposed_answer_matches(runtime, header, canonical, row, plan):
                raise PolicyUnavailableError("Committed native source answer is unknown")
            from butlers.chronicler.location_answer_disposal import source_answer_cohort

            selected = [
                answer
                for answer in await source_answer_cohort(runtime, conn, plan)
                if answer["answer_generation"] == str(header["answer_generation"])
            ]
            if len(selected) != 1 or selected[0]["complete_input"] is not True:
                raise PolicyUnavailableError("Committed native source answer ancestry is unknown")
    return {
        "decision_id": str(decision),
        "manifest_digest": plan["manifest_digest"],
        "source_name": runtime.name,
        "answer_generation": str(header["answer_generation"]),
        "ledger_id": str(header["ledger_id"]),
        "body_digest": header["body_digest"].hex(),
        "bundle_digest": header["bundle_digest"].hex(),
        "receipt_id": str(receipt),
    }


async def close_source_answers(runtime: Any, decision: UUID) -> dict:
    from butlers.chronicler.location_answer_disposal import answer_source_plan

    plan = await answer_source_plan(runtime, decision)
    await reconcile_answer_receivers(runtime, plan)
    receipts = await dispose_source_answers(runtime, plan)
    return {"decision_id": str(decision), "receipt_ids": receipts}


async def reconcile_answer_sources(domain: Any, decision: UUID) -> None:
    """Actual scheduled source invocation selects owners from frozen native lineage."""
    from butlers.chronicler.location_retention import plan_status
    from butlers.core.delegation_source import _writers

    writer = _writers.get(domain)
    if writer is None or not writer.runtime.active or writer.runtime.name != "chronicler":
        return  # No fabricated source constructor; census remains incomplete.
    plan = await plan_status(domain, decision)
    names = {loan["receiver_name"] for q in plan["question_cohort"] for loan in q["loans"]}
    names.add(writer.runtime.name)  # Own directly source-derived answers also participate.
    for name in sorted(names):
        result = await writer.runtime.routed_tool(
            name, "location_retention_close_source_answers", {"decision_id": str(decision)}
        )
        if result.get("decision_id") != str(decision):
            raise PolicyUnavailableError("Native answer owning source reconciliation differs")
        # This wake is not a receiver/source/context receipt. Each actual owning
        # reducer separately commits its own complete body-bound observations.


async def closed_source_answer_tools(
    conn: Any, runtime: Any, schema: str, context: UUID, plan: dict
):
    """Every original source child of each Tool must have this exact reduced profile.

    The schema comes only from the validated own constructor. The LEFT JOIN
    retains missing dispositions; a successful sibling cannot bless another
    still-live answer. These rows select the full recorded Tool witnesses;
    they never erase an independent runtime context by proxy.
    """
    headers = await conn.fetch(
        f"SELECT * FROM {schema}.location_native_delegation_answers "
        "WHERE context_generation=$1 ORDER BY answer_generation",
        context,
    )
    closed, blocked = set(), set()
    for header in headers:
        disposition = await conn.fetchrow(
            f"SELECT * FROM {schema}.location_native_delegation_answer_dispositions "
            "WHERE answer_generation=$1",
            header["answer_generation"],
        )
        canonical = await conn.fetchrow(
            "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE OF delegation_ledger",
            header["ledger_id"],
        )
        parents = await conn.fetch(
            f"SELECT parent_kind,parent_generation,parent_digest "
            f"FROM {schema}.location_native_delegation_answer_parents "
            "WHERE answer_generation=$1",
            header["answer_generation"],
        )
        complete = (
            header["exclusive_input"] is True
            and header["parent_count"] > 0
            and len(parents) == header["parent_count"]
            and len({(p["parent_kind"], p["parent_generation"]) for p in parents}) == len(parents)
        )
        if complete and disposed_answer_matches(runtime, header, canonical, disposition, plan):
            closed.add(header["tool_generation"])
        else:
            blocked.add(header["tool_generation"])
    return [{"tool_generation": generation} for generation in sorted(closed - blocked)]
