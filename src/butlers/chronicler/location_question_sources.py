"""Cross-owner answered-question observations over fixed registered tools.

The answer owner closes its child first. This question owner then freezes that
actual receipt and reduces only its own ledger question after every own source
and receiving lifetime closes. The answer owner separately observes the question
receipt before accepting a reduced question reference on future readbacks.
Network calls occur outside transactions; no peer-private schema is queried.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from butlers.chronicler.location_delegation_copies import question_digest
from butlers.chronicler.location_policy import PolicyUnavailableError


def answered_question_matches(
    canonical: Any, observed: Any, header: Any, plan: Any, *, owner_name: str = "chronicler"
) -> bool:
    """Own frozen answer identity plus the actual canonical reduced answer.

    This witness closes only this child. Metadata and mixed descendants remain
    separate; only the exact empty-metadata profile is reduced here.
    """
    from butlers.chronicler.location_answer_sources import _REDUCED_ANSWER

    return (
        canonical is not None
        and observed is not None
        and observed["question_generation"] == header["question_generation"]
        and observed["decision_id"] == plan["decision_id"]
        and observed["manifest_digest"] == plan["manifest_digest"]
        and canonical["asking_butler"] == owner_name
        and canonical["status"] == "answered"
        and canonical["metadata"] in (None, {})
        and canonical.get("reason") in (None, "location_retention_expired")
        and canonical["target_butler"] == observed["answer_owner"]
        and canonical["answering_butler"] == observed["answer_owner"]
        and canonical["answer_digest"] == observed["answer_body_digest"].hex()
        and canonical["answer"] == _REDUCED_ANSWER
        and canonical["wake_key"] == observed["wake_key"]
    )


async def question_disposition_profile(conn: Any, header: Any, canonical: Any, plan: Any) -> bool:
    from butlers.chronicler.location_delegation_disposal import unanswered_source_question

    if unanswered_source_question(canonical):
        return True
    observed = await conn.fetchrow(
        "SELECT * FROM location_native_question_answer_observations WHERE question_generation=$1",
        header["question_generation"],
    )
    return answered_question_matches(canonical, observed, header, plan)


async def reconcile_answered_questions(
    domain: Any, decision: UUID, *, owning_plan: dict | None = None
) -> None:
    """Capture actual answer-owner receipts before this question's reduction.

    The constructor selects the source and the original canonical target selects
    the answer owner. Successful wake metadata is not a receipt. Each selected
    status has complete original ancestry and is rechecked against our immutable
    question under our policy-first transaction before a separate readback.
    """
    from butlers.chronicler.location_delegation_disposal import (
        dispose_source_questions,
        source_question_cohort,
    )
    from butlers.core.delegation_source import _writers

    writer = _writers.get(domain)
    if writer is None or not writer.runtime.active:
        return
    runtime = writer.runtime
    if owning_plan is None and runtime.name != "chronicler":
        return
    if owning_plan is not None and (
        owning_plan.get("source_name") != runtime.name
        or owning_plan.get("source_incarnation") != str(runtime.incarnation)
        or str(owning_plan.get("decision_id")) != str(decision)
    ):
        raise PolicyUnavailableError("Native recursive question owning plan differs")
    await acknowledge_reduced_questions(runtime, decision)
    async with domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            if owning_plan is None:
                plan = await conn.fetchrow(
                    "SELECT * FROM location_retention_plans WHERE decision_id=$1", decision
                )
                if plan is None or plan["state"] != "holder_pending":
                    return
                cohort = await source_question_cohort(conn, decision)
            else:
                from butlers.chronicler.location_question_recursive import question_owner_cohort

                # The fixed root reader selected this immutable decision;
                # actual own parent floors are rechecked in this transaction.
                plan = {
                    **owning_plan,
                    "decision_id": decision,
                    "manifest_digest": bytes.fromhex(owning_plan["manifest_digest"]),
                }
                cohort = await question_owner_cohort(runtime, conn, owning_plan)
    for question in cohort:
        if question["complete_input"] is not True:
            continue
        generation = UUID(question["question_generation"])
        async with domain.acquire() as read:
            header = await read.fetchrow(
                "SELECT * FROM location_native_delegation_inputs WHERE question_generation=$1",
                generation,
            )
            canonical = await read.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1", header["ledger_id"]
            )
            observed = await read.fetchrow(
                "SELECT * FROM location_native_question_answer_observations "
                "WHERE question_generation=$1",
                generation,
            )
        if observed is not None:
            # Resume a lost acknowledgement without rereading an original body
            # which has already been lawfully reduced by this owning writer.
            if not answered_question_matches(
                canonical, observed, header, plan, owner_name=runtime.name
            ):
                raise PolicyUnavailableError("Native observed source answer changed")
            continue
        if canonical is None or canonical["status"] != "answered":
            continue
        if question_digest(dict(canonical)) != header["body_digest"]:
            raise PolicyUnavailableError("Native original source question changed")
        owner = canonical["target_butler"]
        if owner != canonical["answering_butler"] or not isinstance(owner, str) or not owner:
            raise PolicyUnavailableError("Native source answer owner differs")
        closed = await runtime.routed_tool(
            owner, "location_retention_close_source_answers", {"decision_id": str(decision)}
        )
        if closed.get("decision_id") != str(decision) or not isinstance(
            closed.get("receipt_ids"), list
        ):
            raise PolicyUnavailableError("Native source answer closure differs")
        matches = []
        for locator in closed["receipt_ids"]:
            receipt = UUID(locator)
            result = await runtime.routed_tool(
                owner,
                "location_retention_source_answer_status",
                {"decision_id": str(decision), "receipt_id": str(receipt)},
            )
            if result.get("ledger_id") == str(header["ledger_id"]):
                if (
                    result.get("source_name") != owner
                    or result.get("decision_id") != str(decision)
                    or result.get("manifest_digest") != plan["manifest_digest"].hex()
                    or result.get("question_digest") != header["body_digest"].hex()
                    or result.get("receipt_id") != str(receipt)
                ):
                    raise PolicyUnavailableError("Native source answer observation differs")
                matches.append(result)
        if not matches:
            continue
        if len(matches) != 1:
            raise PolicyUnavailableError("Native source answer observation is ambiguous")
        result = matches[0]
        async with domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                if owning_plan is None:
                    current_plan = await conn.fetchrow(
                        "SELECT * FROM location_retention_plans WHERE decision_id=$1", decision
                    )
                else:
                    from butlers.chronicler.location_question_recursive import question_owner_cohort

                    # No peer schema or postcommit copied DTO supplies this
                    # binding: every original parent must still qualify here.
                    current_cohort = await question_owner_cohort(runtime, conn, owning_plan)
                    selected = [
                        q for q in current_cohort if q["question_generation"] == str(generation)
                    ]
                    if len(selected) != 1 or selected[0]["complete_input"] is not True:
                        raise PolicyUnavailableError("Native recursive answer ancestry changed")
                    current_plan = plan
                current = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 "
                    "FOR UPDATE OF delegation_ledger",
                    header["ledger_id"],
                )
                current_header = await conn.fetchrow(
                    "SELECT * FROM location_native_delegation_inputs WHERE question_generation=$1",
                    generation,
                )
                values = dict(
                    question_generation=generation,
                    decision_id=decision,
                    manifest_digest=plan["manifest_digest"],
                    answer_owner=owner,
                    answer_generation=UUID(result["answer_generation"]),
                    answer_receipt=UUID(result["receipt_id"]),
                    answer_body_digest=bytes.fromhex(result["body_digest"]),
                    answer_bundle_digest=bytes.fromhex(result["bundle_digest"]),
                    wake_key=result["wake_key"],
                )
                if (
                    current is None
                    or current_plan is None
                    or (owning_plan is None and current_plan["state"] != "holder_pending")
                    or current_header is None
                    or current_header["body_digest"] != header["body_digest"]
                    or question_digest(dict(current)) != header["body_digest"]
                    or not answered_question_matches(
                        current, values, current_header, current_plan, owner_name=runtime.name
                    )
                ):
                    raise PolicyUnavailableError("Native source answer committed binding differs")
                await conn.execute(
                    "INSERT INTO location_native_question_answer_observations "
                    "(question_generation,decision_id,manifest_digest,answer_owner,"
                    "answer_generation,answer_receipt,answer_body_digest,answer_bundle_digest,"
                    "wake_key) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING",
                    *values.values(),
                )
        async with domain.acquire() as readback:
            stored = await readback.fetchrow(
                "SELECT * FROM location_native_question_answer_observations "
                "WHERE question_generation=$1",
                generation,
            )
        if stored is None or any(stored[key] != value for key, value in values.items()):
            raise PolicyUnavailableError("Committed source answer observation is unknown")
    if owning_plan is None:
        await dispose_source_questions(domain, decision)
        await acknowledge_reduced_questions(runtime, decision)


async def acknowledge_reduced_questions(runtime: Any, decision: UUID) -> None:
    """Resume the same two owning receipts after an interrupted remote ack.

    This happens before source answer reads, since their original question may
    already be reduced. No duplicate receipt is created or original ref refilled.
    """
    async with runtime.domain.acquire() as read:
        rows = await read.fetch(
            "SELECT o.*,d.receipt_id AS question_receipt,q.ledger_id,q.body_digest "
            "FROM location_native_question_answer_observations o "
            "JOIN location_native_delegation_dispositions d USING(question_generation) "
            "JOIN location_native_delegation_inputs q USING(question_generation) "
            "WHERE o.decision_id=$1 AND d.decision_id=$1 "
            "AND o.manifest_digest=d.manifest_digest ORDER BY o.question_generation",
            decision,
        )
    for row in rows:
        result = await runtime.routed_tool(
            row["answer_owner"],
            "location_retention_observe_source_question",
            {
                "decision_id": str(decision),
                "answer_receipt": str(row["answer_receipt"]),
                "question_receipt": str(row["question_receipt"]),
            },
        )
        if (
            result.get("receipt_id") != str(row["answer_receipt"])
            or result.get("answer_generation") != str(row["answer_generation"])
            or result.get("body_digest") != row["answer_body_digest"].hex()
            or result.get("bundle_digest") != row["answer_bundle_digest"].hex()
            or result.get("source_name") != row["answer_owner"]
            or result.get("decision_id") != str(decision)
            or result.get("manifest_digest") != row["manifest_digest"].hex()
            or result.get("ledger_id") != str(row["ledger_id"])
            or result.get("question_digest") != row["body_digest"].hex()
            or result.get("wake_key") != row["wake_key"]
        ):
            raise PolicyUnavailableError("Native answer question acknowledgement differs")


async def source_question_tool_finished(
    conn: Any, header: Any, session: Any, canonical: Any
) -> bool:
    """The exact successful private ask input/result, never a same-name sibling."""
    from butlers.chronicler.location_tool_copies import matched_tool_records
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    if not isinstance(session["tool_calls"], list):
        return False
    witnesses = await conn.fetch(
        "SELECT t.*,r.outcome,r.result_digest,r.exclusive_inputs "
        "FROM location_runtime_tool_intents t "
        "LEFT JOIN location_runtime_tool_results r USING(tool_generation) "
        "WHERE t.receiving_session=$1 AND t.tool_name='delegate_ask' "
        "ORDER BY t.tool_generation",
        header["receiving_session"],
    )
    calls = [
        c for c in session["tool_calls"] if isinstance(c, dict) and c.get("name") == "delegate_ask"
    ]
    selected = [w for w in witnesses if w["tool_generation"] == header["tool_generation"]]
    expected = fingerprint_tool_call_payload({"question": canonical["question"]})
    if (
        len(selected) != 1
        or selected[0]["module_name"] != "core"
        or selected[0]["outcome"] != "success"
        or selected[0]["exclusive_inputs"] is not True
        or selected[0]["input_digest"].hex() != expected
    ):
        return False
    try:
        if not matched_tool_records(calls, witnesses):
            return False
        applicable = [
            c
            for c in calls
            if c.get("input_fingerprint") == expected
            and c.get("module") == "core"
            and bytes.fromhex(fingerprint_tool_call_payload(c.get("result")))
            == selected[0]["result_digest"]
        ]
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    if not applicable:
        return False
    result = applicable[0].get("result")
    return (
        isinstance(result, dict)
        and set(result) == {"status", "ledger_id", "target_butler"}
        and result["status"] == "routed"
        and result["ledger_id"] == str(header["ledger_id"])
        and result["target_butler"] == canonical["target_butler"]
    )
