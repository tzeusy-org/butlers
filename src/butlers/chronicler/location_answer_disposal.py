"""Owning complete answer ancestry and terminal source-plan selection.

A fixed registered source plan selects stored generations. A receiver floor is
an owning prerequisite, never a substitute for this answer's own descendant
receipts. No source-name field authorizes peer SQL or copied body reconstruction.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_delegation_answers import answer_bundle_digest
from butlers.chronicler.location_policy import PolicyUnavailableError


async def _answer_parent_scope(runtime: Any, conn: Any, header: Any, parent: Any, plan: dict):
    """Return relevant/complete from the actual owning immutable parent binding."""
    kind, generation, digest = (
        parent["parent_kind"],
        parent["parent_generation"],
        parent["parent_digest"],
    )
    if kind == "native_copy":
        if runtime.name != "chronicler":
            return True, False  # No peer private birth lookup.
        births = await conn.fetch(
            "SELECT b.*,EXISTS(SELECT 1 FROM location_retention_plan_outputs p "
            "WHERE p.decision_id=$2 AND p.output_kind=b.output_kind "
            "AND p.output_id=b.output_id) AS selected "
            "FROM location_native_copy_births b WHERE b.copy_generation=$1 "
            "ORDER BY b.output_kind,b.output_id",
            generation,
            UUID(str(plan["decision_id"])),
        )
        if not births or any(
            row["input_digest"] != digest or row["lineage_known"] is not True for row in births
        ):
            return True, False
        return (
            any(row["selected"] is True for row in births),
            all(row["selected"] is True and row["exclusive_input"] is True for row in births),
        )
    if kind == "catalog_loan":
        loan = await conn.fetchrow(
            "SELECT * FROM location_catalog_copy_loans WHERE loan_id=$1", generation
        )
        declared = [
            row
            for row in plan.get("catalog_loans", ())
            if row.get("loan_id") == str(generation) and row.get("receiver_name") == runtime.name
        ]
        exact = (
            loan is not None
            and loan["body_digest"] == digest
            and len(declared) == 1
            and declared[0].get("complete_input") is True
            and declared[0].get("body_digest") == digest.hex()
            and declared[0].get("source_generation") == str(loan["source_generation"])
            and declared[0].get("receiving_incarnation") == str(loan["receiving_incarnation"])
            and loan["receiving_incarnation"] == runtime.incarnation
        )
        if exact:
            exact = (
                await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_catalog_copy_lifetimes "
                    "WHERE loan_id=$1 AND body_digest=$2 AND "
                    "((holder_kind='unbound_processing' AND holder_id IN ($3,$4)) "
                    "OR (holder_kind='runtime_session' AND holder_id=$5)))",
                    generation,
                    digest,
                    header["context_generation"],
                    header["tool_generation"],
                    header["receiving_session"],
                )
                is True
            )
        return True, exact
    if kind == "received_question":
        row = await conn.fetchrow(
            "SELECT i.*,f.decision_id,f.manifest_digest,f.body_digest AS floor_digest,"
            "f.question_generation AS floor_question,f.loan_id AS floor_loan,"
            "f.ledger_id AS floor_ledger,f.source_name AS floor_source,"
            "f.receiving_incarnation AS floor_incarnation "
            "FROM location_received_delegation_inputs i "
            "LEFT JOIN location_received_delegation_floors f USING(receiving_generation) "
            "WHERE i.receiving_generation=$1",
            generation,
        )
        if row is None:
            return True, False
        exact = (
            row["body_digest"] == digest
            and row["exclusive_input"] is True
            and row["decision_id"] == UUID(str(plan["decision_id"]))
            and row["manifest_digest"] == bytes.fromhex(plan["manifest_digest"])
            and row["receiving_incarnation"] == runtime.incarnation
            and row["floor_incarnation"] == runtime.incarnation
            and row["floor_digest"] == digest
            and row["floor_question"] == row["question_generation"]
            and row["floor_loan"] == row["loan_id"]
            and row["floor_ledger"] == row["ledger_id"]
            and row["floor_source"] == row["source_name"]
        )
        return True, exact
    if kind == "received_answer":
        row = await conn.fetchrow(
            "SELECT i.*,f.decision_id,f.manifest_digest,f.bundle_digest AS floor_digest,"
            "f.answer_generation AS floor_answer,f.loan_id AS floor_loan,"
            "f.source_name AS floor_source,f.source_incarnation AS floor_source_incarnation,"
            "f.receiving_incarnation AS floor_incarnation,EXISTS(SELECT 1 FROM "
            "location_received_answer_qualifications q "
            "WHERE q.receiving_generation=i.receiving_generation) AS floor_complete,"
            "a.receiving_incarnation, "
            "a.ledger_id,f.ledger_id AS floor_ledger "
            "FROM location_received_answer_inputs i "
            "JOIN location_received_answer_attempts a USING(receiving_generation) "
            "LEFT JOIN location_received_answer_floors f USING(receiving_generation) "
            "WHERE i.receiving_generation=$1",
            generation,
        )
        exact = (
            row is not None
            and row["bundle_digest"] == digest
            and row["exclusive_input"] is True
            and row["decision_id"] == UUID(str(plan["decision_id"]))
            and row["manifest_digest"] == bytes.fromhex(plan["manifest_digest"])
            and row["receiving_incarnation"] == runtime.incarnation
            and row["floor_incarnation"] == runtime.incarnation
            and row["floor_complete"] is True
            and row["floor_digest"] == digest
            and row["floor_answer"] == row["answer_generation"]
            and row["floor_loan"] == row["loan_id"]
            and row["floor_source"] == row["source_name"]
            and row["floor_source_incarnation"] == row["source_incarnation"]
            and row["floor_ledger"] == row["ledger_id"]
        )
        return True, exact
    raise PolicyUnavailableError("Native answer parent kind differs")


async def source_answer_cohort(runtime: Any, conn: Any, plan: dict) -> list[dict]:
    """Every declared parent participates; source dispositions do not close receivers."""
    await runtime.lock_domain(conn)
    result, cursor = [], None
    while True:
        headers = await conn.fetch(
            "SELECT * FROM location_native_delegation_answers "
            "WHERE ($1::uuid IS NULL OR answer_generation>$1) "
            "ORDER BY answer_generation LIMIT 64",
            cursor,
        )
        if not headers:
            return result
        for header in headers:
            parents = await conn.fetch(
                "SELECT * FROM location_native_delegation_answer_parents "
                "WHERE answer_generation=$1 ORDER BY parent_kind,parent_generation",
                header["answer_generation"],
            )
            complete = (
                header["exclusive_input"] is True
                and len(parents) == header["parent_count"]
                and header["parent_count"] > 0
            )
            relevant = len(parents) != header["parent_count"] or header["parent_count"] == 0
            if len({(p["parent_kind"], p["parent_generation"]) for p in parents}) != len(parents):
                raise PolicyUnavailableError("Native answer original parent set differs")
            for parent in parents:
                selected, exact = await _answer_parent_scope(runtime, conn, header, parent, plan)
                relevant = relevant or selected
                complete = complete and exact
            if not relevant:
                continue  # Only actual known nonselected native births establish this.
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 "
                "FOR UPDATE OF delegation_ledger",
                header["ledger_id"],
            )
            disposition = await conn.fetchrow(
                "SELECT * FROM location_native_delegation_answer_dispositions "
                "WHERE answer_generation=$1",
                header["answer_generation"],
            )
            if disposition is None:
                valid_body = (
                    canonical is not None
                    and header["bundle_digest"] is not None
                    and canonical["answering_butler"] == runtime.name
                    and answer_bundle_digest(canonical) == header["bundle_digest"]
                    and canonical["answer_digest"] == header["body_digest"].hex()
                )
            else:
                from butlers.chronicler.location_answer_sources import disposed_answer_matches

                valid_body = disposed_answer_matches(runtime, header, canonical, disposition, plan)
            if not valid_body:
                raise PolicyUnavailableError("Native terminal answer body changed")
            loans = await conn.fetch(
                "SELECT * FROM location_native_answer_loans WHERE answer_generation=$1 "
                "ORDER BY loan_id",
                header["answer_generation"],
            )
            result.append(
                dict(
                    answer_generation=str(header["answer_generation"]),
                    ledger_id=str(header["ledger_id"]),
                    source_name=runtime.name,
                    body_digest=header["body_digest"].hex(),
                    bundle_digest=header["bundle_digest"].hex(),
                    parent_count=header["parent_count"],
                    complete_input=complete,
                    loans=[
                        {
                            key: row[key].hex()
                            if isinstance(row[key], bytes)
                            else row[key]
                            if isinstance(row[key], bool)
                            else str(row[key])
                            for key in (
                                "loan_id",
                                "receiver_name",
                                "receiving_generation",
                                "receiving_incarnation",
                                "bundle_digest",
                                "source_incarnation",
                            )
                        }
                        for row in loans
                    ],
                )
            )
        cursor = headers[-1]["answer_generation"]


async def answer_source_plan(runtime: Any, decision: UUID) -> dict:
    """Actual owning registered reader; a locator cannot supply a source verdict."""
    if not runtime.active:
        raise PolicyUnavailableError("Native answer source constructor ended")
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
        raise PolicyUnavailableError("Native answer owning source plan differs") from None
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            cohort = await source_answer_cohort(runtime, conn, source)
    return {
        **source,
        "decision_id": str(decision),
        "manifest_digest": manifest.hex(),
        "source_name": runtime.name,
        "source_incarnation": str(runtime.incarnation),
        "answer_cohort": cohort,
    }


_FLOOR_KEYS = (
    "receiving_generation",
    "decision_id",
    "manifest_digest",
    "bundle_digest",
    "source_name",
    "answer_generation",
    "ledger_id",
    "loan_id",
    "source_incarnation",
    "receiving_incarnation",
)
_REDUCED_RETURN = "Delegated answer expired under the source retention policy."


def _answer_floor_binding(runtime: Any, plan: dict, answer: dict, loan: dict) -> dict:
    """A fixed owning plan binds every original locator and both incarnations."""
    try:
        binding = {
            "receiving_generation": UUID(loan["receiving_generation"]),
            "decision_id": UUID(str(plan["decision_id"])),
            "manifest_digest": bytes.fromhex(plan["manifest_digest"]),
            "bundle_digest": bytes.fromhex(answer["bundle_digest"]),
            "source_name": plan["source_name"],
            "answer_generation": UUID(answer["answer_generation"]),
            "ledger_id": UUID(answer["ledger_id"]),
            "loan_id": UUID(loan["loan_id"]),
            "source_incarnation": UUID(loan["source_incarnation"]),
            "receiving_incarnation": UUID(loan["receiving_incarnation"]),
        }
    except (KeyError, ValueError, TypeError):
        raise PolicyUnavailableError("Native answer receiving floor binding differs") from None
    if (
        answer.get("source_name") != binding["source_name"]
        or plan.get("source_incarnation") != str(binding["source_incarnation"])
        or loan.get("receiver_name") != runtime.name
        or binding["receiving_incarnation"] != runtime.incarnation
        or loan.get("bundle_digest") != answer.get("bundle_digest")
        or len(binding["manifest_digest"]) != 32
        or len(binding["bundle_digest"]) != 32
    ):
        raise PolicyUnavailableError("Native answer receiving floor cohort differs")
    return binding


async def _close_answer_receiver(runtime: Any, binding: dict, *, complete: bool) -> UUID | None:
    """Commit the exact floor before examining any copied processing or task.

    No old incarnation or missing input proves termination. A replay returns
    only the original immutable receipt after a fresh committed readback.
    Server completion attests only its server lifetime; a receiving MCP Tool
    still needs its independent owning context disposition. Every processing
    claim and reserved context participates, including incomplete contexts.
    """
    generation, receipt = binding["receiving_generation"], None
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            attempt = await conn.fetchrow(
                "SELECT * FROM location_received_answer_attempts "
                "WHERE receiving_generation=$1 FOR UPDATE",
                generation,
            )
            if attempt is None or any(
                attempt[key] != binding[key]
                for key in ("source_name", "ledger_id", "receiving_incarnation")
            ):
                raise PolicyUnavailableError("Native answer receiving attempt differs")
            await conn.execute(
                "INSERT INTO location_received_answer_floors "
                "(receiving_generation,decision_id,manifest_digest,bundle_digest,source_name,"
                "answer_generation,ledger_id,loan_id,source_incarnation,receiving_incarnation) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT DO NOTHING",
                *(binding[key] for key in _FLOOR_KEYS),
            )
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_answer_floors "
                "WHERE receiving_generation=$1 FOR UPDATE",
                generation,
            )
            if floor is None or any(floor[key] != binding[key] for key in _FLOOR_KEYS):
                raise PolicyUnavailableError("Native answer receiving floor differs")
            if complete:
                # Qualification can arrive after an earlier immutable floor.
                # It records this fixed source's complete current plan check,
                # never terminal receiver/descendant disposition.
                await conn.execute(
                    "INSERT INTO location_received_answer_qualifications "
                    "(receiving_generation,receipt_id) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    generation,
                    uuid4(),
                )
            receipt = await conn.fetchval(
                "SELECT receipt_id FROM location_received_answer_dispositions "
                "WHERE receiving_generation=$1",
                generation,
            )
            if receipt is None:
                writer = runtime.delegation_writer
                if (
                    not complete
                    or generation in writer.receiving_answers
                    or any(p.receiving == generation for p in writer.answer_pending.values())
                ):
                    return None
                if attempt["server_request"] is not None and not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_received_answer_server_finished "
                    "WHERE receiving_generation=$1 AND server_request=$2)",
                    generation,
                    attempt["server_request"],
                ):
                    return None
                if attempt["tool_generation"] is not None:
                    metadata_only = await metadata_wake_tool_finished(conn, attempt)
                    if not metadata_only and not await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents t "
                        "JOIN location_runtime_context_bindings b "
                        "ON b.receiving_session=t.receiving_session "
                        "JOIN location_runtime_context_dispositions d USING(input_generation) "
                        "WHERE t.tool_generation=$1 AND t.receiving_session=$2 "
                        "AND d.decision_id=$3 AND d.manifest_digest=$4)",
                        attempt["tool_generation"],
                        attempt["receiving_session"],
                        binding["decision_id"],
                        binding["manifest_digest"],
                    ):
                        return None
                admitted = await conn.fetchrow(
                    "SELECT * FROM location_received_answer_inputs WHERE receiving_generation=$1",
                    generation,
                )
                if admitted is not None and any(
                    admitted[key] != binding[key]
                    for key in (
                        "source_name",
                        "answer_generation",
                        "loan_id",
                        "bundle_digest",
                        "source_incarnation",
                    )
                ):
                    raise PolicyUnavailableError("Native answer admitted input differs")
                if admitted is not None and (
                    admitted["exclusive_input"] is not True or admitted["parent_count"] < 1
                ):
                    return None
                unresolved = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_received_answer_claim_parents p "
                    "JOIN location_received_answer_claims c USING(claim_generation) "
                    "WHERE p.receiving_generation=$1 AND (p.bundle_digest<>$2 "
                    "OR c.parent_count<>(SELECT count(*) "
                    "FROM location_received_answer_claim_parents x "
                    "WHERE x.claim_generation=c.claim_generation) "
                    "OR NOT EXISTS(SELECT 1 FROM location_received_answer_claims_ended e "
                    "WHERE e.claim_generation=c.claim_generation) "
                    "OR EXISTS(SELECT 1 FROM location_runtime_context_answer_intents i "
                    "WHERE i.claim_generation=c.claim_generation AND NOT EXISTS("
                    "SELECT 1 FROM location_runtime_context_dispositions d "
                    "WHERE d.input_generation=i.input_generation AND d.decision_id=$3 "
                    "AND d.manifest_digest=$4))))",
                    generation,
                    binding["bundle_digest"],
                    binding["decision_id"],
                    binding["manifest_digest"],
                )
                if unresolved is not False:
                    return None
                schedule = await conn.fetchrow(
                    "SELECT s.*,t.prompt,t.enabled FROM location_received_answer_schedules s "
                    "JOIN scheduled_tasks t ON t.id=s.task_id "
                    "WHERE s.receiving_generation=$1 FOR UPDATE OF t",
                    generation,
                )
                if schedule is not None:
                    if not await _closed_return_task_cohort(conn, runtime, schedule, binding):
                        return None
                    original = (
                        hashlib.sha256(schedule["prompt"].encode()).digest()
                        == schedule["prompt_digest"]
                    )
                    already_reduced = schedule["prompt"] == _REDUCED_RETURN and await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_received_answer_dispositions d "
                        "JOIN location_received_answer_floors f USING(receiving_generation) "
                        "JOIN location_received_answer_schedules s USING(receiving_generation) "
                        "WHERE d.task_id=$1 AND s.task_id=$1 AND s.prompt_digest=$2 "
                        "AND f.decision_id=$3 AND f.manifest_digest=$4 "
                        "AND d.reduced_prompt_digest=$5)",
                        schedule["task_id"],
                        schedule["prompt_digest"],
                        binding["decision_id"],
                        binding["manifest_digest"],
                        hashlib.sha256(_REDUCED_RETURN.encode()).digest(),
                    )
                    if not original and not already_reduced:
                        raise PolicyUnavailableError("Native answer return task changed")
                    await conn.execute(
                        "UPDATE scheduled_tasks SET enabled=false,prompt=$2 WHERE id=$1",
                        schedule["task_id"],
                        _REDUCED_RETURN,
                    )
                    if not await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM scheduled_tasks "
                        "WHERE id=$1 AND NOT enabled AND prompt=$2)",
                        schedule["task_id"],
                        _REDUCED_RETURN,
                    ):
                        raise PolicyUnavailableError("Native answer return reduction differs")
                receipt = uuid4()
                await conn.execute(
                    "INSERT INTO location_received_answer_dispositions "
                    "(receiving_generation,receipt_id,task_id,reduced_prompt_digest) "
                    "VALUES($1,$2,$3,$4)",
                    generation,
                    receipt,
                    schedule["task_id"] if schedule is not None else None,
                    hashlib.sha256(_REDUCED_RETURN.encode()).digest()
                    if schedule is not None
                    else None,
                )
    observed = await answer_receiver_status(runtime, binding["decision_id"], receipt)
    if any(
        observed[key]
        != (
            value.hex()
            if isinstance(value, bytes)
            else value
            if isinstance(value, bool)
            else str(value)
        )
        for key, value in binding.items()
    ):
        raise PolicyUnavailableError("Committed native answer disposition is unknown")
    return receipt


async def answer_receiver_status(runtime: Any, decision: UUID, receipt: UUID) -> dict:
    """Own committed floor, original receipt and exact task-reduction readback."""
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            row = await conn.fetchrow(
                "SELECT f.*,d.receipt_id,d.task_id,d.reduced_prompt_digest,t.prompt,t.enabled "
                "FROM location_received_answer_floors f "
                "JOIN location_received_answer_dispositions d USING(receiving_generation) "
                "LEFT JOIN scheduled_tasks t ON t.id=d.task_id "
                "WHERE f.decision_id=$1 AND d.receipt_id=$2",
                decision,
                receipt,
            )
            if row is None or row["receiving_incarnation"] != runtime.incarnation:
                raise PolicyUnavailableError("Native answer disposition is unavailable")
            if row["task_id"] is not None and (
                row["enabled"] is not False
                or row["prompt"] != _REDUCED_RETURN
                or row["reduced_prompt_digest"] != hashlib.sha256(_REDUCED_RETURN.encode()).digest()
            ):
                raise PolicyUnavailableError("Committed native answer task differs")
    return {
        key: row[key].hex()
        if isinstance(row[key], bytes)
        else row[key]
        if isinstance(row[key], bool)
        else str(row[key])
        for key in (*_FLOOR_KEYS, "receipt_id")
    }


async def prepare_answer_receiver(runtime: Any, decision: UUID, loan_id: UUID) -> dict:
    """Only persisted own attempts select fixed registered owning MCP sources.

    The loan UUID never supplies a source address or verdict. Each selected
    source must reconstruct its full current cohort, and its result must match
    the actual local attempt and both incarnations before any floor is written.
    """
    if not runtime.active:
        raise PolicyUnavailableError("Native answer receiver constructor ended")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            sources = await conn.fetch(
                "SELECT DISTINCT a.source_name FROM location_received_answer_attempts a "
                "JOIN location_received_answer_inputs i USING(receiving_generation) "
                "WHERE i.loan_id=$1 AND a.receiving_incarnation=$2 ORDER BY a.source_name",
                loan_id,
                runtime.incarnation,
            )
            if not sources:
                # Interrupted admission has no stored input/loan row. Only
                # actual local attempt sources may select the registered
                # owning reader; an arbitrary caller source stays impossible.
                sources = await conn.fetch(
                    "SELECT DISTINCT source_name FROM location_received_answer_attempts "
                    "WHERE receiving_incarnation=$1 ORDER BY source_name",
                    runtime.incarnation,
                )
    selected = []
    for source in sources:
        plan = await runtime.routed_tool(
            source["source_name"], "location_retention_answer_plan", {"decision_id": str(decision)}
        )
        if plan.get("source_name") != source["source_name"] or str(plan.get("decision_id")) != str(
            decision
        ):
            raise PolicyUnavailableError("Native answer routed source differs")
        for answer in plan.get("answer_cohort", ()):
            for loan in answer["loans"]:
                if (
                    loan.get("loan_id") == str(loan_id)
                    and loan.get("receiver_name") == runtime.name
                ):
                    selected.append((plan, answer, loan))
    if len(selected) != 1:
        raise PolicyUnavailableError("Native answer stored loan selection differs")
    plan, answer, loan = selected[0]
    binding = _answer_floor_binding(runtime, plan, answer, loan)
    receipt = await _close_answer_receiver(
        runtime, binding, complete=answer.get("complete_input") is True
    )
    if receipt is None and answer.get("complete_input") is True:
        from butlers.chronicler.location_delegation_contexts import dispose_core_answer_contexts

        await dispose_core_answer_contexts(runtime, binding)
        await dispose_memory_answer_contexts(runtime, binding, plan)
        receipt = await _close_answer_receiver(
            runtime, binding, complete=answer.get("complete_input") is True
        )
    return {
        "decision_id": str(decision),
        "loan_id": str(loan_id),
        "receipt_id": str(receipt) if receipt is not None else None,
    }


async def _closed_return_task_cohort(conn: Any, runtime: Any, schedule: Any, binding: dict) -> bool:
    """One physical return task can carry several independently admitted bindings."""
    rows = await conn.fetch(
        "SELECT s.receiving_generation AS declared_receiving,s.prompt_digest,i.*,"
        "a.receiving_incarnation,a.ledger_id,a.wake_key,a.server_request,"
        "a.tool_generation,a.receiving_session,f.decision_id,f.manifest_digest,"
        "f.bundle_digest AS floor_digest,f.loan_id AS floor_loan,"
        "f.answer_generation AS floor_answer,f.source_name AS floor_source,"
        "f.source_incarnation AS floor_source_incarnation,"
        "f.receiving_incarnation AS floor_incarnation,f.ledger_id AS floor_ledger,"
        "EXISTS(SELECT 1 FROM location_received_answer_qualifications q "
        "WHERE q.receiving_generation=s.receiving_generation) AS qualified "
        "FROM location_received_answer_schedules s "
        "LEFT JOIN location_received_answer_inputs i USING(receiving_generation) "
        "LEFT JOIN location_received_answer_attempts a USING(receiving_generation) "
        "LEFT JOIN location_received_answer_floors f USING(receiving_generation) "
        "WHERE s.task_id=$1 ORDER BY s.receiving_generation",
        schedule["task_id"],
    )
    exact = (
        bool(rows)
        and len({row["declared_receiving"] for row in rows}) == len(rows)
        and binding["receiving_generation"] in {row["declared_receiving"] for row in rows}
        and all(
            row["receiving_generation"] == row["declared_receiving"]
            and row["prompt_digest"] == schedule["prompt_digest"]
            and row["exclusive_input"] is True
            and row["parent_count"] is not None
            and row["parent_count"] > 0
            and row["qualified"] is True
            and row["decision_id"] == binding["decision_id"]
            and row["manifest_digest"] == binding["manifest_digest"]
            and row["floor_digest"] == row["bundle_digest"]
            and row["floor_loan"] == row["loan_id"]
            and row["floor_answer"] == row["answer_generation"]
            and row["floor_source"] == row["source_name"]
            and row["floor_source_incarnation"] == row["source_incarnation"]
            and row["floor_incarnation"] == runtime.incarnation
            and row["receiving_incarnation"] == runtime.incarnation
            and row["floor_ledger"] == row["ledger_id"]
            for row in rows
        )
    )

    if not exact:
        return False
    # A task claim may have frozen a smaller earlier binding set before a
    # later duplicate wake enrolled another receiver. Examine EVERY claim of
    # this physical task, not just claims naming the current receiver.
    unresolved = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_received_answer_claims c WHERE c.task_id=$1 "
        "AND (c.parent_count<>(SELECT count(*) FROM location_received_answer_claim_parents p "
        "WHERE p.claim_generation=c.claim_generation) "
        "OR NOT EXISTS(SELECT 1 FROM location_received_answer_claims_ended e "
        "WHERE e.claim_generation=c.claim_generation) OR EXISTS("
        "SELECT 1 FROM location_runtime_context_answer_intents i "
        "WHERE i.claim_generation=c.claim_generation AND NOT EXISTS(SELECT 1 "
        "FROM location_runtime_context_dispositions d WHERE d.input_generation=i.input_generation "
        "AND d.decision_id=$2 AND d.manifest_digest=$3))))",
        schedule["task_id"],
        binding["decision_id"],
        binding["manifest_digest"],
    )
    if unresolved is not False:
        return False
    for row in rows:
        if not await _answer_attempt_ended(conn, runtime, row, binding):
            return False
    return True


async def _answer_attempt_ended(conn: Any, runtime: Any, attempt: Any, binding: dict) -> bool:
    generation = attempt["receiving_generation"]
    writer = runtime.delegation_writer
    if generation in writer.receiving_answers or any(
        pending.receiving == generation for pending in writer.answer_pending.values()
    ):
        return False
    if attempt["server_request"] is None and attempt["tool_generation"] is None:
        return False  # Missing owning attempt lifetime cannot become absence proof.
    if (
        attempt["server_request"] is not None
        and await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_answer_server_finished "
            "WHERE receiving_generation=$1 AND server_request=$2)",
            generation,
            attempt["server_request"],
        )
        is not True
    ):
        return False
    if attempt["tool_generation"] is not None:
        metadata_only = await metadata_wake_tool_finished(conn, attempt)
        if (
            not metadata_only
            and await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents t "
                "JOIN location_runtime_context_bindings b "
                "ON b.receiving_session=t.receiving_session "
                "JOIN location_runtime_context_dispositions d USING(input_generation) "
                "WHERE t.tool_generation=$1 AND t.receiving_session=$2 "
                "AND d.decision_id=$3 AND d.manifest_digest=$4)",
                attempt["tool_generation"],
                attempt["receiving_session"],
                binding["decision_id"],
                binding["manifest_digest"],
            )
            is not True
        ):
            return False
    return True


async def closed_answer_context_input(
    conn: Any,
    schema: str,
    runtime: Any,
    frozen: Any,
    generation: UUID,
    binding: dict,
) -> bool:
    """Reconstruct ALL original claim parents and their own immutable floors.

    The schema is the actual owning constructor's configured domain. Missing
    births/intents/floors, an extra parent, any mixed source, an unfinished claim
    or a different decision preserves the context. No reduced JOIN cohort can
    redefine the original claim count or frozen prompt digest.
    """
    from butlers.chronicler.location_return_processing import _bundle

    if binding["receiving_incarnation"] != runtime.incarnation:
        return False
    captured = await conn.fetchrow(
        f"SELECT b.*,c.task_id,c.prompt_digest,c.bundle_digest AS original_bundle,"
        "c.parent_count,c.receiving_incarnation,c.exclusive_input,"
        "i.claim_generation AS reserved_claim,e.receipt_id AS ended_receipt "
        f"FROM {schema}.location_received_answer_contexts b "
        f"JOIN {schema}.location_received_answer_claims c USING(claim_generation) "
        f"LEFT JOIN {schema}.location_runtime_context_answer_intents i "
        "ON i.input_generation=b.input_generation "
        f"LEFT JOIN {schema}.location_received_answer_claims_ended e "
        "ON e.claim_generation=c.claim_generation "
        "WHERE b.input_generation=$1",
        generation,
    )
    if (
        captured is None
        or captured["reserved_claim"] != captured["claim_generation"]
        or captured["receiving_session"] != frozen["receiving_session"]
        or captured["bundle_digest"] != frozen["bundle_digest"]
        or captured["prompt_digest"] != frozen["prompt_digest"]
        or captured["claim_bundle_digest"] != captured["original_bundle"]
        or captured["exclusive_input"] is not True
        or captured["receiving_incarnation"] != runtime.incarnation
        or captured["ended_receipt"] is None
        or captured["parent_count"] < 1
    ):
        return False
    parents = await conn.fetch(
        "SELECT p.receiving_generation AS original_receiving,p.bundle_digest AS original_digest,"
        "s.receiving_generation AS declared_receiving,i.*,a.ledger_id,a.wake_key,"
        "a.receiving_incarnation,s.prompt_digest,t.prompt AS scheduled_prompt,"
        "f.decision_id,f.manifest_digest,f.bundle_digest AS floor_digest,"
        "f.source_name AS floor_source,f.answer_generation AS floor_answer,"
        "f.loan_id AS floor_loan,f.source_incarnation AS floor_source_incarnation,"
        "f.receiving_incarnation AS floor_incarnation,f.ledger_id AS floor_ledger,"
        f"EXISTS(SELECT 1 FROM {schema}.location_received_answer_qualifications q "
        "WHERE q.receiving_generation=p.receiving_generation) AS floor_complete "
        f"FROM {schema}.location_received_answer_claim_parents p "
        f"LEFT JOIN {schema}.location_received_answer_inputs i USING(receiving_generation) "
        f"LEFT JOIN {schema}.location_received_answer_attempts a USING(receiving_generation) "
        f"LEFT JOIN {schema}.location_received_answer_schedules s USING(receiving_generation) "
        f"LEFT JOIN {schema}.scheduled_tasks t ON t.id=s.task_id "
        f"LEFT JOIN {schema}.location_received_answer_floors f USING(receiving_generation) "
        "WHERE p.claim_generation=$1 ORDER BY p.receiving_generation",
        captured["claim_generation"],
    )
    if (
        len(parents) != captured["parent_count"]
        or len({row["original_receiving"] for row in parents}) != len(parents)
        or binding["receiving_generation"] not in {row["original_receiving"] for row in parents}
        or any(
            row["declared_receiving"] != row["original_receiving"]
            or row["original_digest"] != row["bundle_digest"]
            or row["exclusive_input"] is not True
            or row["parent_count"] is None
            or row["parent_count"] < 1
            or row["decision_id"] != binding["decision_id"]
            or row["manifest_digest"] != binding["manifest_digest"]
            or row["floor_digest"] != row["bundle_digest"]
            or row["floor_source"] != row["source_name"]
            or row["floor_answer"] != row["answer_generation"]
            or row["floor_loan"] != row["loan_id"]
            or row["floor_source_incarnation"] != row["source_incarnation"]
            or row["floor_incarnation"] != runtime.incarnation
            or row["receiving_incarnation"] != runtime.incarnation
            or row["floor_ledger"] != row["ledger_id"]
            or row["floor_complete"] is not True
            for row in parents
        )
    ):
        return False
    prompt = parents[0]["scheduled_prompt"]
    return isinstance(prompt, str) and _bundle(parents, prompt) == captured["original_bundle"]


async def dispose_memory_answer_contexts(runtime: Any, binding: dict, plan: dict) -> None:
    """Select exact own contexts, then invoke the configured full descendant engine."""
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_memory_context import context_writer, dispose_runtime_context

    if (
        not runtime.active
        or _runtimes.get(getattr(runtime, "memory", None)) is not runtime
        or context_writer(runtime.domain) is not runtime
    ):
        return
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_answer_floors "
                "WHERE receiving_generation=$1 FOR UPDATE",
                binding["receiving_generation"],
            )
            if floor is None or any(floor[key] != value for key, value in binding.items()):
                raise PolicyUnavailableError("Configured answer context floor differs")
            contexts = await conn.fetch(
                "SELECT i.input_generation FROM location_runtime_context_answer_intents i "
                "JOIN location_received_answer_claim_parents p USING(claim_generation) "
                "WHERE p.receiving_generation=$1 ORDER BY i.input_generation",
                binding["receiving_generation"],
            )
    for row in contexts:
        await dispose_runtime_context(
            runtime, row["input_generation"], plan, answer_binding=binding
        )


async def metadata_wake_tool_finished(conn: Any, attempt: Any) -> bool:
    """Exact successful locator-only wake execution ends only its Tool processing.

    The actual handler exposes no copied question/answer body to its caller.
    All recorded same-name calls must match every private input/result witness
    one-to-one; the selected input is exactly the two stored locators, and the
    selected successful result must contain only this ledger/task's fixed
    metadata. This neither reduces nor attests an independent model context.
    Session completion alone, caller arguments and generic MCP outcomes cannot
    establish this profile. Error/conflict/mixed/unknown outcomes stay held.
    """
    from butlers.chronicler.location_tool_copies import matched_tool_records
    from butlers.core.tool_call_capture import fingerprint_tool_call_payload

    session = await conn.fetchrow(
        "SELECT tool_calls,completed_at FROM sessions WHERE id=$1", attempt["receiving_session"]
    )
    if (
        session is None
        or session["completed_at"] is None
        or not isinstance(session["tool_calls"], list)
    ):
        return False
    witnesses = await conn.fetch(
        "SELECT t.*,r.outcome,r.result_digest,r.exclusive_inputs "
        "FROM location_runtime_tool_intents t "
        "LEFT JOIN location_runtime_tool_results r USING(tool_generation) "
        "WHERE t.receiving_session=$1 AND t.tool_name='delegate_wake' ORDER BY t.tool_generation",
        attempt["receiving_session"],
    )
    calls = [
        call
        for call in session["tool_calls"]
        if isinstance(call, dict) and call.get("name") == "delegate_wake"
    ]
    selected = [row for row in witnesses if row["tool_generation"] == attempt["tool_generation"]]
    if len(selected) != 1 or selected[0]["exclusive_inputs"] is not True:
        return False
    expected = fingerprint_tool_call_payload(
        {
            "ledger_id": str(attempt["ledger_id"]),
            "wake_key": attempt["wake_key"],
        }
    )
    row = selected[0]
    if row["module_name"] != "core" or row["input_digest"].hex() != expected:
        return False
    try:
        if not matched_tool_records(calls, witnesses):
            return False
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    matched = [
        call
        for call in calls
        if call.get("input_fingerprint") == expected
        and call.get("module") == "core"
        and bytes.fromhex(fingerprint_tool_call_payload(call.get("result"))) == row["result_digest"]
    ]
    if not matched:
        return False
    result = matched[0].get("result")
    if (
        not isinstance(result, dict)
        or not {"status", "ledger_id", "wake_state", "task_id"} <= result.keys()
        or set(result) - {"status", "ledger_id", "wake_state", "task_id", "reconciled"}
        or result["status"] != "ok"
        or result["wake_state"] != "task_created"
        or result["ledger_id"] != str(attempt["ledger_id"])
        or ("reconciled" in result and type(result["reconciled"]) is not bool)
    ):
        return False
    try:
        task = UUID(result["task_id"])
    except (TypeError, ValueError, AttributeError):
        return False
    return (
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_answer_schedules "
            "WHERE receiving_generation=$1 AND task_id=$2)",
            attempt["receiving_generation"],
            task,
        )
        is True
    )
