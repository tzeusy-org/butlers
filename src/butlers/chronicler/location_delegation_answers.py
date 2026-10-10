"""Own native answer birth on the actual atomic first-answer ledger writer.

Answer/ledger text supplies no receiving authority. Only the registered private
answering tool's full frozen context, original declared parents and committed
receiving inputs select the copied cohort. Duplicate/unaccepted writes leave
that first immutable birth unchanged; unknown committed readback never returns
an answer as successfully captured.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_policy import PolicyUnavailableError


def answer_bundle_digest(row: Any) -> bytes:
    """Frozen canonical reference bytes and wake identity, never authority.

    An answer text hash cannot bind the question, recipient or callback that
    the actual return-task producer will consume. Mutable wake disposition
    and task acknowledgements are not part of this immutable body identity.
    """
    from butlers.chronicler.location_delegation_copies import question_digest
    from butlers.core.delegation_ledger import compute_answer_digest, compute_wake_key
    from butlers.location_retention import content_digest

    fields = ("asking_butler", "target_butler", "question", "answer", "answering_butler")
    if (
        row["status"] != "answered"
        or any(not isinstance(row[key], str) or not row[key] for key in fields)
        or row["answering_butler"] != row["target_butler"]
        or row["answer_digest"] != compute_answer_digest(row["answer"])
        or row["wake_key"] != compute_wake_key(row["id"], row["answer_digest"])
    ):
        raise PolicyUnavailableError("Native answer canonical bundle differs")
    return content_digest(
        {
            "native_answer.v1": {
                "ledger_id": str(UUID(str(row["id"]))),
                "question_digest": question_digest(dict(row)).hex(),
                **{key: row[key] for key in fields},
                "answer_digest": row["answer_digest"],
                "wake_key": row["wake_key"],
            }
        }
    )


async def capture_answer(writer: Any, ledger: UUID, answering: str, answer: str, write: Any):
    from butlers.chronicler.location_tool_copies import current_tool_copy

    runtime = writer.runtime
    tool = current_tool_copy(runtime)
    if (
        tool is None
        or tool.module != "core"
        or tool.name != "delegate_answer"
        or answering != runtime.name
    ):
        raise PolicyUnavailableError("Native answer source producer differs")
    generation, digest = uuid4(), hashlib.sha256(answer.encode()).digest()
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            frozen, parents, exclusive = await writer._capture_input(conn, tool)
            # Original business SQL retains its assigned target/status guard,
            # atomic answer+wake identity, and unaccepted replay semantics.
            row = await write(conn)
            if row is None:
                return None
            if (
                UUID(str(row["id"])) != ledger
                or row["answering_butler"] != runtime.name
                or row["status"] != "answered"
                or row["answer"] != answer
                or row["answer_digest"] != digest.hex()
            ):
                raise PolicyUnavailableError("Native canonical answer differs")
            bundle = answer_bundle_digest(row)
            await conn.execute(
                "INSERT INTO location_native_delegation_answers "
                "(answer_generation,ledger_id,receiving_session,tool_generation,"
                "context_generation,body_digest,parent_count,exclusive_input,bundle_digest) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                generation,
                ledger,
                tool.session,
                tool.generation,
                frozen["input_generation"],
                digest,
                len(parents),
                exclusive,
                bundle,
            )
            for kind, selected, body in parents:
                await conn.execute(
                    "INSERT INTO location_native_delegation_answer_parents "
                    "(answer_generation,parent_kind,parent_generation,parent_digest) "
                    "VALUES($1,$2,$3,$4)",
                    generation,
                    kind,
                    selected,
                    body,
                )
    async with runtime.domain.acquire() as readback:
        receipt = await readback.fetchrow(
            "SELECT * FROM location_native_delegation_answers WHERE answer_generation=$1",
            generation,
        )
        actual = await readback.fetchrow(
            "SELECT * FROM public.delegation_ledger WHERE id=$1",
            ledger,
        )
        committed = await readback.fetch(
            "SELECT parent_kind,parent_generation,parent_digest "
            "FROM location_native_delegation_answer_parents WHERE answer_generation=$1",
            generation,
        )
    if (
        receipt is None
        or receipt["ledger_id"] != ledger
        or receipt["receiving_session"] != tool.session
        or receipt["tool_generation"] != tool.generation
        or receipt["context_generation"] != frozen["input_generation"]
        or receipt["body_digest"] != digest
        or receipt["bundle_digest"] != bundle
        or receipt["parent_count"] != len(parents)
        or receipt["exclusive_input"] is not exclusive
        or {(p["parent_kind"], p["parent_generation"], p["parent_digest"]) for p in committed}
        != set(parents)
        or actual is None
        or actual["answer"] != answer
        or actual["answer_digest"] != digest.hex()
        or actual["answering_butler"] != runtime.name
        or answer_bundle_digest(actual) != bundle
    ):
        raise PolicyUnavailableError("Committed native answer birth is unknown")
    tool.read_observed = True
    tool.mixed_inputs = tool.mixed_inputs or not exclusive
    return dict(row)


async def loan_answers_closed(
    conn: Any, loan: UUID, digest: bytes, decision: UUID, manifest: bytes
):
    """An answer copy remains in its actual owning catalog-loan frontier."""
    return not await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_delegation_answers q "
        "JOIN location_native_delegation_answer_parents p USING(answer_generation) "
        "WHERE p.parent_kind='catalog_loan' AND p.parent_generation=$1 "
        "AND p.parent_digest=$2 AND NOT EXISTS("
        "SELECT 1 FROM location_native_delegation_answer_dispositions d "
        "WHERE d.answer_generation=q.answer_generation AND d.decision_id=$3 "
        "AND d.manifest_digest=$4 AND d.body_digest=q.body_digest))",
        loan,
        digest,
        decision,
        manifest,
    )
