"""Original-source receiving fence before the complete attempt census.

Only fixed owning MCP plan readers call this producer. A stored source selector
is not a principal; current canonical bytes and complete original lineage must
match the admitted source plan before the owning policy-first COMMIT.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from butlers.chronicler.location_delegation_copies import question_digest
from butlers.chronicler.location_policy import PolicyUnavailableError


async def seal_question_source_floor(runtime: Any, plan: dict, question: dict) -> bool:
    from butlers.core.delegation_source import _writers

    writer = _writers.get(runtime.domain)
    if not runtime.active or writer is None or writer.runtime is not runtime:
        raise PolicyUnavailableError("Native question census constructor differs")
    if question.get("complete_input") is not True or question.get("target_name") != runtime.name:
        return False  # Unknown/mixed original source never qualifies this fence.
    try:
        source = plan.get("source_name", "chronicler")
        binding = dict(
            source_name=source,
            ledger_id=UUID(question["ledger_id"]),
            question_generation=UUID(question["question_generation"]),
            body_digest=bytes.fromhex(question["body_digest"]),
            decision_id=UUID(str(plan["decision_id"])),
            manifest_digest=bytes.fromhex(plan["manifest_digest"]),
        )
        current_digest = bytes.fromhex(question["current_body_digest"])
        if (
            not isinstance(source, str)
            or not source
            or len(binding["body_digest"]) != 32
            or len(binding["manifest_digest"]) != 32
            or len(current_digest) != 32
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise PolicyUnavailableError("Native question census original binding differs") from None
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                binding["ledger_id"],
            )
            if (
                canonical is None
                or canonical["asking_butler"] != source
                or canonical["target_butler"] != runtime.name
                or question_digest(dict(canonical)) != current_digest
            ):
                raise PolicyUnavailableError("Native question census current body differs")
            await conn.execute(
                "INSERT INTO location_received_question_source_floors "
                "(source_name,ledger_id,question_generation,body_digest,"
                "decision_id,manifest_digest) "
                "VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
                *binding.values(),
            )
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_question_source_floors "
                "WHERE source_name=$1 AND ledger_id=$2",
                source,
                binding["ledger_id"],
            )
            if floor is None or any(floor[k] != v for k, v in binding.items()):
                raise PolicyUnavailableError("Native question census source floor differs")
    async with runtime.domain.acquire() as observed:
        row = await observed.fetchrow(
            "SELECT * FROM location_received_question_source_floors "
            "WHERE source_name=$1 AND ledger_id=$2",
            source,
            binding["ledger_id"],
        )
    if row is None or any(row[k] != v for k, v in binding.items()):
        raise PolicyUnavailableError("Committed native question census floor is unknown")
    return True


async def question_attempt_census(runtime: Any, plan: dict, question: dict) -> dict:
    """Every original known attempt remains pending until its own exact receipt.

    The source floor is an admission fence, never a terminal verdict. This
    owning read cannot hide unaccepted/legacy/current-incarnation siblings in
    an admitted JOIN or treat a caller's completion field as disposition.
    """
    source = plan.get("source_name", "chronicler")
    ledger, generation = UUID(question["ledger_id"]), UUID(question["question_generation"])
    digest = bytes.fromhex(question["body_digest"])
    decision, manifest = UUID(str(plan["decision_id"])), bytes.fromhex(plan["manifest_digest"])
    pending = any(p.ledger == ledger for p in runtime.delegation_writer.pending.values()) or any(
        a.ledger == ledger for a in runtime.delegation_writer.receiving.values()
    )
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            floor = await conn.fetchrow(
                "SELECT * FROM location_received_question_source_floors "
                "WHERE source_name=$1 AND ledger_id=$2",
                source,
                ledger,
            )
            if floor is None or any(
                floor[k] != v
                for k, v in dict(
                    question_generation=generation,
                    body_digest=digest,
                    decision_id=decision,
                    manifest_digest=manifest,
                ).items()
            ):
                raise PolicyUnavailableError("Native question attempt census floor differs")
            rows = await conn.fetch(
                "SELECT a.*,COALESCE(f.question_generation,r.question_generation) "
                "AS floor_question,"
                "f.loan_id,COALESCE(f.source_name,r.source_name) AS floor_source,"
                "COALESCE(f.body_digest,r.body_digest) AS floor_digest,"
                "COALESCE(f.receiving_incarnation,r.receiving_incarnation) AS floor_incarnation,"
                "COALESCE(f.decision_id,r.decision_id) AS decision_id,"
                "COALESCE(f.manifest_digest,r.manifest_digest) AS manifest_digest,"
                "((f.receiving_generation IS NOT NULL)::integer+"
                "(r.receiving_generation IS NOT NULL)::integer) AS binding_count,"
                "COALESCE(d.receipt_id,e.receipt_id) AS receipt_id "
                "FROM location_received_delegation_attempts a "
                "LEFT JOIN location_received_delegation_floors f USING(receiving_generation) "
                "LEFT JOIN location_received_delegation_dispositions d USING(receiving_generation) "
                "LEFT JOIN location_received_question_recoveries r USING(receiving_generation) "
                "LEFT JOIN location_received_question_recovery_dispositions e "
                "USING(receiving_generation) "
                "WHERE a.ledger_id=$1 AND (a.source_name=$2 OR a.source_name IS NULL) "
                "ORDER BY a.receiving_generation",
                ledger,
                source,
            )
            for row in rows:
                if (
                    row["binding_count"] != 1
                    or row["source_name"] != source
                    or row["body_digest"] != digest
                    or row["receiving_incarnation"] != runtime.incarnation
                    or row["floor_incarnation"] != runtime.incarnation
                    or row["floor_question"] != generation
                    or row["floor_source"] != source
                    or row["floor_digest"] != digest
                    or row["decision_id"] != decision
                    or row["manifest_digest"] != manifest
                    or row["receipt_id"] is None
                ):
                    pending = True
    return dict(
        source_name=source,
        question_generation=str(generation),
        ledger_id=str(ledger),
        body_digest=digest.hex(),
        decision_id=str(decision),
        manifest_digest=manifest.hex(),
        receiver_name=runtime.name,
        receiving_incarnation=str(runtime.incarnation),
        attempt_count=len(rows),
        pending=pending,
    )


def require_question_census(prepared: dict, plan: dict, question: dict, receiver: str) -> None:
    """Exact result of the fixed owning prepare route, not a caller verdict.

    Each original source header needs its matching receiving fence/census,
    including zero previously known loans. Root all-holder proof stays separate.
    """
    rows = prepared.get("source_census") if isinstance(prepared, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PolicyUnavailableError("Native question receiving census is unavailable")
    matches = [
        row for row in rows if row.get("question_generation") == question["question_generation"]
    ]
    if len(matches) != 1:
        raise PolicyUnavailableError("Native question receiving census is unavailable")
    row = matches[0]
    if (
        row.get("source_name") != plan.get("source_name", "chronicler")
        or row.get("ledger_id") != question["ledger_id"]
        or row.get("body_digest") != question["body_digest"]
        or row.get("decision_id") != str(plan["decision_id"])
        or row.get("manifest_digest") != plan["manifest_digest"]
        or row.get("receiver_name") != receiver
        or type(row.get("attempt_count")) is not int
        or row["attempt_count"] < 0
        or row.get("pending") is not False
    ):
        raise PolicyUnavailableError("Native question receiving census is pending")
    try:
        UUID(row["receiving_incarnation"])
    except (KeyError, TypeError, ValueError):
        raise PolicyUnavailableError(
            "Native question receiving census incarnation differs"
        ) from None


async def selected_question_source_plan(runtime: Any, decision: UUID, ledger: UUID) -> dict:
    """A locator selects canonical target/source, then the real owning native plan.

    The public source selector routes only: its independently registered owner
    must prove original generation/body and qualified root manifest. Neither a
    caller source string nor an absent receiving loan can qualify this census.
    """
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            canonical = await conn.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE", ledger
            )
            if (
                canonical is None
                or canonical["target_butler"] != runtime.name
                or not isinstance(canonical["asking_butler"], str)
                or not canonical["asking_butler"]
            ):
                raise PolicyUnavailableError("Native question census target is unavailable")
            source = canonical["asking_butler"]
    plan = await runtime.routed_tool(
        source, "location_retention_question_owner_plan", {"decision_id": str(decision)}
    )
    if plan.get("source_name") != source or plan.get("decision_id") != str(decision):
        raise PolicyUnavailableError("Native question census owning source differs")
    questions = [q for q in plan.get("question_cohort", ()) if q.get("ledger_id") == str(ledger)]
    if len(questions) != 1 or questions[0].get("target_name") != runtime.name:
        raise PolicyUnavailableError("Native question census original question differs")
    # The actual fence producer locks and rechecks CURRENT canonical body after
    # this network response, preserving its own transaction ordering.
    return {**plan, "question_cohort": questions}
