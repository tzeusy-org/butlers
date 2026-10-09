"""Complete owning nested-question ancestry and fixed root-plan admission.

Borrowed inputs select only this runtime's private births/loans/receiving floors.
The Chronicle root plan comes over the registered Switchboard reader. No peer
schema, actor string or a missing birth can establish a smaller source cohort.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID
from weakref import WeakKeyDictionary

from butlers.chronicler.location_delegation_copies import question_digest
from butlers.chronicler.location_policy import PolicyUnavailableError

_closing: WeakKeyDictionary = WeakKeyDictionary()


async def question_owner_cohort(runtime: Any, conn: Any, plan: dict) -> list[dict]:
    """All original parents, never INNER JOIN-derived smaller ancestry."""
    from butlers.chronicler.location_answer_disposal import _answer_parent_scope
    from butlers.chronicler.location_delegation_disposal import _REDUCED_QUESTION, _REDUCED_REASON

    result, cursor = [], None
    while True:
        headers = await conn.fetch(
            "SELECT * FROM location_native_delegation_inputs "
            "WHERE ($1::uuid IS NULL OR question_generation>$1) "
            "ORDER BY question_generation LIMIT 64",
            cursor,
        )
        if not headers:
            return result
        for header in headers:
            parents = await conn.fetch(
                "SELECT * FROM location_native_delegation_parents "
                "WHERE question_generation=$1 ORDER BY parent_kind,parent_generation",
                header["question_generation"],
            )
            complete = (
                header["exclusive_input"] is True
                and header["parent_count"] > 0
                and len(parents) == header["parent_count"]
            )
            relevant = not complete
            if len({(p["parent_kind"], p["parent_generation"]) for p in parents}) != len(parents):
                raise PolicyUnavailableError("Native nested question parent set differs")
            for parent in parents:
                selected, exact = await _answer_parent_scope(runtime, conn, header, parent, plan)
                relevant = relevant or selected
                complete = complete and exact
            if not relevant:
                continue  # Every actual parent is positively known nonselected.
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 "
                "FOR UPDATE OF delegation_ledger",
                header["ledger_id"],
            )
            disposed = await conn.fetchrow(
                "SELECT * FROM location_native_delegation_dispositions "
                "WHERE question_generation=$1",
                header["question_generation"],
            )
            valid = canonical is not None and canonical["asking_butler"] == runtime.name
            if disposed is None:
                valid = valid and question_digest(dict(canonical)) == header["body_digest"]
            else:
                valid = valid and (
                    disposed["decision_id"] == UUID(str(plan["decision_id"]))
                    and disposed["manifest_digest"] == bytes.fromhex(plan["manifest_digest"])
                    and disposed["body_digest"] == header["body_digest"]
                    and disposed["reduced_question_digest"] is not None
                    and canonical["question"] == _REDUCED_QUESTION
                    and canonical["reason"] == _REDUCED_REASON
                    and question_digest(dict(canonical)) == disposed["reduced_question_digest"]
                )
                if valid:
                    valid = await own_question_profile(conn, runtime, header, canonical, plan)
            if not valid:
                raise PolicyUnavailableError("Native nested question original body differs")
            loans = await conn.fetch(
                "SELECT * FROM location_native_delegation_loans WHERE question_generation=$1 "
                "ORDER BY loan_id",
                header["question_generation"],
            )
            result.append(
                dict(
                    source_name=runtime.name,
                    question_generation=str(header["question_generation"]),
                    ledger_id=str(header["ledger_id"]),
                    body_digest=header["body_digest"].hex(),
                    parent_count=header["parent_count"],
                    complete_input=complete,
                    loans=[
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
                )
            )
        cursor = headers[-1]["question_generation"]


def own_unanswered_question(runtime: Any, canonical: Any) -> bool:
    return (
        canonical is not None
        and canonical["asking_butler"] == runtime.name
        and canonical["status"] in {"pending", "routed", "failed", "unroutable"}
        and canonical["metadata"] in (None, {})
        and canonical["wake_state"] == "not_applicable"
        and all(
            canonical[key] is None
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


async def own_question_profile(
    conn: Any, runtime: Any, header: Any, canonical: Any, plan: dict, *, schema=None
) -> bool:
    from butlers.chronicler.location_question_sources import answered_question_matches

    if own_unanswered_question(runtime, canonical):
        return True
    prefix = "" if schema is None else schema + "."
    observed = await conn.fetchrow(
        f"SELECT * FROM {prefix}location_native_question_answer_observations "
        "WHERE question_generation=$1",
        header["question_generation"],
    )
    return answered_question_matches(
        canonical,
        observed,
        header,
        dict(
            decision_id=UUID(str(plan["decision_id"])),
            manifest_digest=bytes.fromhex(plan["manifest_digest"]),
        ),
        owner_name=runtime.name,
    )


async def question_owner_plan(runtime: Any, decision: UUID) -> dict:
    from butlers.core.delegation_source import _writers

    writer = _writers.get(runtime.domain)
    if not runtime.active or writer is None or writer.runtime is not runtime:
        raise PolicyUnavailableError("Native nested question constructor ended")
    source = await runtime.routed_tool(
        "chronicler", "chronicler_location_retention_status", {"decision_id": str(decision)}
    )
    try:
        if str(source["decision_id"]) != str(decision):
            raise ValueError
        manifest = bytes.fromhex(source["manifest_digest"])
        if len(manifest) != 32:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise PolicyUnavailableError("Native nested question source plan differs") from None
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            cohort = await question_owner_cohort(runtime, conn, source)
    return {
        **source,
        "decision_id": str(decision),
        "manifest_digest": manifest.hex(),
        "source_name": runtime.name,
        "source_incarnation": str(runtime.incarnation),
        "question_cohort": cohort,
    }


async def prepare_question_loan(
    runtime: Any, decision: UUID, loan_id: UUID, receiving_generation: UUID | None = None
) -> dict:
    """An own admitted loan selects the actual original source, never a request name."""
    from butlers.chronicler.location_delegation_contexts import (
        dispose_core_question_contexts,
        dispose_memory_question_contexts,
    )
    from butlers.chronicler.location_delegation_disposal import (
        _close_question_receiver,
        _floor_binding,
    )

    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            own = await conn.fetchrow(
                "SELECT i.*,a.body_digest AS attempt_digest,a.ledger_id AS attempt_ledger,"
                "a.receiving_incarnation AS attempt_incarnation "
                "FROM location_received_delegation_inputs i "
                "JOIN location_received_delegation_attempts a USING(receiving_generation) "
                "WHERE i.loan_id=$1",
                loan_id,
            )
            admitted = own is not None
            if own is None and receiving_generation is not None:
                attempt = await conn.fetchrow(
                    "SELECT * FROM location_received_delegation_attempts "
                    "WHERE receiving_generation=$1",
                    receiving_generation,
                )
                if attempt is not None and attempt.get("source_name"):
                    own = dict(attempt)
            if own is None:
                # Legacy NULL or absent source is unknown, never refilled from
                # caller fields/public current ledger or inferred absence.
                return {"decision_id": str(decision), "loan_id": str(loan_id), "receipt_id": None}
            if (
                own["receiving_incarnation"] != runtime.incarnation
                or (
                    receiving_generation is not None
                    and own["receiving_generation"] != receiving_generation
                )
            ) or (
                admitted
                and (
                    own["ledger_id"] != own["attempt_ledger"]
                    or own["body_digest"] != own["attempt_digest"]
                    or own["attempt_incarnation"] != runtime.incarnation
                )
            ):
                raise PolicyUnavailableError("Native nested question receiving attempt differs")
    plan = await runtime.routed_tool(
        own["source_name"],
        "location_retention_question_owner_plan",
        {"decision_id": str(decision)},
    )
    if (
        plan.get("decision_id") != str(decision)
        or plan.get("source_name") != own["source_name"]
        or (admitted and plan.get("source_incarnation") != str(own["source_incarnation"]))
    ):
        raise PolicyUnavailableError("Native nested question owning plan differs")
    matched = [
        (q, loan)
        for q in plan.get("question_cohort", ())
        for loan in q["loans"]
        if loan["loan_id"] == str(loan_id)
    ]
    if len(matched) != 1:
        raise PolicyUnavailableError("Native nested question owning loan differs")
    question, loan = matched[0]
    if (
        (admitted and question["question_generation"] != str(own["question_generation"]))
        or question["ledger_id"] != str(own["ledger_id"])
        or question["body_digest"] != own["body_digest"].hex()
        or loan["receiving_generation"] != str(own["receiving_generation"])
    ):
        raise PolicyUnavailableError("Native nested question original binding differs")
    binding = _floor_binding(runtime, plan, question, loan)
    receipt = await _close_question_receiver(runtime, binding)
    if receipt is None:
        # Nested children close before this context; their parent only needs
        # the already-qualified permanent input floor, not this terminal ack.
        await close_owned_questions(runtime, decision)
        await dispose_core_question_contexts(runtime, binding)
        await dispose_memory_question_contexts(runtime, binding, plan)
        receipt = await _close_question_receiver(runtime, binding)
    return {
        "decision_id": str(decision),
        "loan_id": str(loan_id),
        "receipt_id": str(receipt) if receipt is not None else None,
    }


async def observe_question_loans(runtime: Any, plan: dict) -> None:
    """Every original own loan needs its own fixed receiver status and COMMIT."""
    for question in plan["question_cohort"]:
        if question["complete_input"] is not True:
            continue
        for loan in question["loans"]:
            prepared = await runtime.routed_tool(
                loan["receiver_name"],
                "location_retention_prepare_question_loan",
                {
                    "decision_id": plan["decision_id"],
                    "loan_id": loan["loan_id"],
                    "receiving_generation": loan["receiving_generation"],
                },
            )
            if (
                prepared.get("decision_id") != plan["decision_id"]
                or prepared.get("loan_id") != loan["loan_id"]
            ):
                raise PolicyUnavailableError("Native nested question preparation differs")
            receipt = prepared.get("receipt_id")
            if receipt is None:
                continue
            receipt_id = UUID(receipt)
            result = await runtime.routed_tool(
                loan["receiver_name"],
                "location_retention_question_status",
                {"decision_id": plan["decision_id"], "receipt_id": str(receipt_id)},
            )
            if (
                result.get("source_name") != runtime.name
                or result.get("decision_id") != plan["decision_id"]
                or result.get("manifest_digest") != plan["manifest_digest"]
                or result.get("question_generation") != question["question_generation"]
                or result.get("ledger_id") != question["ledger_id"]
                or result.get("receipt_id") != str(receipt_id)
                or any(
                    result.get(key) != loan[key]
                    for key in (
                        "loan_id",
                        "body_digest",
                        "receiving_generation",
                        "receiving_incarnation",
                    )
                )
            ):
                raise PolicyUnavailableError("Native nested question receiving status differs")
            async with runtime.domain.acquire() as conn:
                async with conn.transaction():
                    await runtime.lock_domain(conn)
                    row = await conn.fetchrow(
                        "SELECT l.*,q.body_digest AS question_digest,q.ledger_id "
                        "FROM location_native_delegation_loans l "
                        "JOIN location_native_delegation_inputs q USING(question_generation) "
                        "WHERE l.loan_id=$1",
                        UUID(loan["loan_id"]),
                    )
                    if (
                        row is None
                        or str(row["question_generation"]) != question["question_generation"]
                        or str(row["ledger_id"]) != question["ledger_id"]
                        or row["question_digest"].hex() != question["body_digest"]
                        or any(
                            str(row[key]) != loan[key]
                            for key in (
                                "receiving_generation",
                                "receiving_incarnation",
                                "receiver_name",
                            )
                        )
                        or row["body_digest"].hex() != loan["body_digest"]
                    ):
                        raise PolicyUnavailableError("Native nested question own loan changed")
                    await conn.execute(
                        "INSERT INTO location_native_question_loan_observations "
                        "(loan_id,decision_id,manifest_digest,receiver_receipt) "
                        "VALUES($1,$2,$3,$4) "
                        "ON CONFLICT DO NOTHING",
                        UUID(loan["loan_id"]),
                        UUID(plan["decision_id"]),
                        bytes.fromhex(plan["manifest_digest"]),
                        receipt_id,
                    )
            async with runtime.domain.acquire() as read:
                observed = await read.fetchrow(
                    "SELECT * FROM location_native_question_loan_observations WHERE loan_id=$1",
                    UUID(loan["loan_id"]),
                )
            if (
                observed is None
                or observed["decision_id"] != UUID(plan["decision_id"])
                or observed["manifest_digest"] != bytes.fromhex(plan["manifest_digest"])
                or observed["receiver_receipt"] != receipt_id
            ):
                raise PolicyUnavailableError("Committed nested question observation is unknown")


async def source_question_lifetime(conn: Any, runtime: Any, header: Any):
    import hashlib

    if any(
        p.question == header["question_generation"]
        for p in runtime.delegation_writer.pending.values()
    ):
        return None
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
        return None
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
        return None
    session = await conn.fetchrow(
        "SELECT * FROM sessions WHERE id=$1 FOR UPDATE OF sessions",
        header["receiving_session"],
    )
    if (
        session is None
        or session["completed_at"] is None
        or not isinstance(session["prompt"], str)
        or not isinstance(session["effective_system_prompt"], str)
        or hashlib.sha256(session["prompt"].encode()).digest() != frozen["prompt_digest"]
        or hashlib.sha256(session["effective_system_prompt"].encode()).digest()
        != frozen["system_digest"]
    ):
        return None
    return session


async def dispose_owned_question_children(runtime: Any, plan: dict) -> list[str]:
    """Own borrowed ledger child closes before the separate composed context."""
    from uuid import uuid4

    from butlers.chronicler.location_delegation_disposal import _REDUCED_QUESTION, _REDUCED_REASON
    from butlers.chronicler.location_question_sources import source_question_tool_finished

    decision, manifest = UUID(plan["decision_id"]), bytes.fromhex(plan["manifest_digest"])
    receipts = []
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            for question in await question_owner_cohort(runtime, conn, plan):
                if question["complete_input"] is not True:
                    continue
                generation = UUID(question["question_generation"])
                prior = await conn.fetchrow(
                    "SELECT * FROM location_native_delegation_dispositions "
                    "WHERE question_generation=$1",
                    generation,
                )
                if prior is not None:
                    if prior["decision_id"] != decision or prior["manifest_digest"] != manifest:
                        raise PolicyUnavailableError("Native nested question disposition differs")
                    receipts.append(str(prior["receipt_id"]))
                    continue
                all_closed = True
                for loan in question["loans"]:
                    observed = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM "
                        "location_native_question_loan_observations "
                        "WHERE loan_id=$1 AND decision_id=$2 AND manifest_digest=$3)",
                        UUID(loan["loan_id"]),
                        decision,
                        manifest,
                    )
                    if observed is not True:
                        all_closed = False
                        break
                if not all_closed:
                    continue
                header = await conn.fetchrow(
                    "SELECT * FROM location_native_delegation_inputs WHERE question_generation=$1",
                    generation,
                )
                canonical = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 "
                    "FOR UPDATE OF delegation_ledger",
                    header["ledger_id"],
                )
                session = await source_question_lifetime(conn, runtime, header)
                if (
                    session is None
                    or not await own_question_profile(conn, runtime, header, canonical, plan)
                    or not await source_question_tool_finished(conn, header, session, canonical)
                ):
                    continue
                if question_digest(dict(canonical)) != header["body_digest"]:
                    raise PolicyUnavailableError("Native nested question changed before reduction")
                receipt = uuid4()
                await conn.execute(
                    "UPDATE public.delegation_ledger SET question=$2,status=$3,reason=$4 "
                    "WHERE id=$1",
                    header["ledger_id"],
                    _REDUCED_QUESTION,
                    "failed" if own_unanswered_question(runtime, canonical) else "answered",
                    _REDUCED_REASON,
                )
                await conn.execute(
                    "INSERT INTO location_native_delegation_dispositions "
                    "(question_generation,decision_id,manifest_digest,body_digest,receipt_id,"
                    "reduced_question_digest) VALUES($1,$2,$3,$4,$5,$6)",
                    generation,
                    decision,
                    manifest,
                    header["body_digest"],
                    receipt,
                    question_digest({**dict(canonical), "question": _REDUCED_QUESTION}),
                )
                receipts.append(str(receipt))
    for receipt in receipts:
        await owned_question_status(runtime, decision, UUID(receipt), plan=plan)
    return receipts


async def owned_question_status(runtime: Any, decision: UUID, receipt: UUID, *, plan=None) -> dict:
    from butlers.chronicler.location_delegation_disposal import _REDUCED_QUESTION, _REDUCED_REASON

    if plan is None:
        plan = await question_owner_plan(runtime, decision)
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            row = await conn.fetchrow(
                "SELECT d.*,q.ledger_id FROM location_native_delegation_dispositions d "
                "JOIN location_native_delegation_inputs q USING(question_generation) "
                "WHERE d.decision_id=$1 AND d.receipt_id=$2",
                decision,
                receipt,
            )
            if (
                row is None
                or row["manifest_digest"] != bytes.fromhex(plan["manifest_digest"])
                or row["reduced_question_digest"] is None
            ):
                raise PolicyUnavailableError("Native nested question receipt is unavailable")
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 "
                "FOR UPDATE OF delegation_ledger",
                row["ledger_id"],
            )
            if (
                canonical is None
                or canonical["question"] != _REDUCED_QUESTION
                or canonical["reason"] != _REDUCED_REASON
                or not await own_question_profile(conn, runtime, row, canonical, plan)
                or question_digest(dict(canonical)) != row["reduced_question_digest"]
            ):
                raise PolicyUnavailableError("Committed nested question body is unknown")
            selected = [
                q
                for q in await question_owner_cohort(runtime, conn, plan)
                if q["question_generation"] == str(row["question_generation"])
            ]
            if len(selected) != 1 or selected[0]["complete_input"] is not True:
                raise PolicyUnavailableError("Native nested question ancestry is unknown")
            answered = await conn.fetchrow(
                "SELECT * FROM location_native_question_answer_observations "
                "WHERE question_generation=$1",
                row["question_generation"],
            )
    return dict(
        source_name=runtime.name,
        decision_id=str(decision),
        manifest_digest=plan["manifest_digest"],
        receipt_id=str(receipt),
        ledger_id=str(row["ledger_id"]),
        question_generation=str(row["question_generation"]),
        body_digest=row["body_digest"].hex(),
        reduced_question_digest=row["reduced_question_digest"].hex(),
        answer_generation=str(answered["answer_generation"]) if answered is not None else None,
        answer_receipt=str(answered["answer_receipt"]) if answered is not None else None,
    )


async def closed_owned_question_tools(conn: Any, runtime: Any, context: UUID, plan: dict):
    """Full own immutable child profiles; no same-name or smaller-ancestry shortcut.

    Namespace comes only from the constructor's own configured identity. These
    receipts close ledger children, while recorded Tool matching and context
    lifetimes remain independent requirements of the calling owning writer.
    """
    from butlers.chronicler.location_delegation_disposal import _REDUCED_QUESTION, _REDUCED_REASON
    from butlers.chronicler.location_memory_context import _own_schema

    schema = _own_schema(runtime)
    headers = await conn.fetch(
        f"SELECT * FROM {schema}.location_native_delegation_inputs "
        "WHERE context_generation=$1 ORDER BY question_generation",
        context,
    )
    closed, blocked = set(), set()
    for header in headers:
        parents = await conn.fetch(
            f"SELECT * FROM {schema}.location_native_delegation_parents "
            "WHERE question_generation=$1",
            header["question_generation"],
        )
        receipt = await conn.fetchrow(
            f"SELECT * FROM {schema}.location_native_delegation_dispositions "
            "WHERE question_generation=$1",
            header["question_generation"],
        )
        canonical = await conn.fetchrow(
            "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE OF delegation_ledger",
            header["ledger_id"],
        )
        complete = (
            header["exclusive_input"] is True
            and header["parent_count"] > 0
            and len(parents) == header["parent_count"]
            and len({(p["parent_kind"], p["parent_generation"]) for p in parents}) == len(parents)
        )
        valid = (
            complete
            and receipt is not None
            and canonical is not None
            and receipt["body_digest"] == header["body_digest"]
            and receipt["decision_id"] == UUID(str(plan["decision_id"]))
            and receipt["manifest_digest"] == bytes.fromhex(plan["manifest_digest"])
            and receipt["reduced_question_digest"] is not None
            and canonical["question"] == _REDUCED_QUESTION
            and canonical["reason"] == _REDUCED_REASON
            and question_digest(dict(canonical)) == receipt["reduced_question_digest"]
            and await own_question_profile(conn, runtime, header, canonical, plan, schema=schema)
        )
        (closed if valid else blocked).add(header["tool_generation"])
    return [{"tool_generation": generation} for generation in sorted(closed - blocked)]


async def close_owned_questions(runtime: Any, decision: UUID) -> dict:
    """Bounded owning progress: reentrant/cyclic requests remain pending.

    The constructor-owned in-process set is never a receipt or caller verdict.
    Independent later invocations recompute the full immutable cohort. This
    avoids deadlocking reciprocal registered requests and preserves every
    unfinished receiver/context until a genuine terminal readback exists.
    """
    from butlers.core.delegation_source import _writers

    writer = _writers.get(runtime.domain)
    if writer is None or writer.runtime is not runtime or not runtime.active:
        raise PolicyUnavailableError("Native nested question constructor ended")
    active = _closing.setdefault(writer, set())
    if decision in active:
        return {"decision_id": str(decision), "receipt_ids": []}
    active.add(decision)
    try:
        plan = await question_owner_plan(runtime, decision)
        await observe_question_loans(runtime, plan)
        from butlers.chronicler.location_question_sources import (
            acknowledge_reduced_questions,
            reconcile_answered_questions,
        )

        await reconcile_answered_questions(runtime.domain, decision, owning_plan=plan)
        receipts = await dispose_owned_question_children(runtime, plan)
        await acknowledge_reduced_questions(runtime, decision)
        return {"decision_id": str(decision), "receipt_ids": receipts}
    finally:
        active.remove(decision)
        if not active:
            _closing.pop(writer, None)
