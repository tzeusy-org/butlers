"""Fixed owning question-source/receiver challenge, with no peer private SQL.

The public ledger selects bytes only. Both constructors lock their own real
pool/identity; an unpredictable bounded receiver pending cell is served only
at its registry-selected native control endpoint. The durable delivery birth
is not a terminal receipt, source proxy or permission derived from a locator.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_delegation_copies import question_digest
from butlers.chronicler.location_policy import PolicyUnavailableError


@dataclass
class _ReceivedQuestion:
    writer: Any
    generation: UUID
    ledger: UUID
    digest: bytes
    deadline: float
    active: bool = True


@dataclass
class _QuestionPending:
    ledger: UUID
    source: str
    digest: bytes
    deadline: float
    receiving: UUID
    tool: Any
    question: UUID | None = None
    source_incarnation: UUID | None = None
    loan: UUID | None = None
    schedule: UUID | None = None
    server: Any = None


async def question_challenge(writer: Any, token: str, body: dict) -> dict:
    runtime = writer.runtime
    pending = writer.pending.get(token)
    if (
        not runtime.active
        or pending is None
        or pending.deadline <= time.monotonic()
        or (
            pending.tool is not None
            and (not pending.tool.active or pending.tool.runtime is not runtime)
        )
        or body
        != {
            "op": "question_challenge",
            "ledger_id": str(pending.ledger),
            "source": pending.source,
            "body_digest": pending.digest.hex(),
        }
    ):
        raise PolicyUnavailableError("Native question receiver challenge differs")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            if pending.tool is None and pending.server is not None:
                from butlers.chronicler.location_catalog_copies import _server_copy_scopes

                if (
                    not pending.server.active
                    or pending.server.target != runtime.name
                    or _server_copy_scopes.get(pending.server.request) is not pending.server
                ):
                    raise PolicyUnavailableError("Native receiving server lifetime differs")
            elif pending.tool is None:
                # A scheduler challenge is minted only from this constructor's
                # actual immutable receiving/schedule pair, never its locator.
                current = await conn.fetchrow(
                    "SELECT i.*,s.task_id,s.prompt_digest "
                    "FROM location_received_delegation_inputs i "
                    "JOIN location_received_delegation_schedules s USING(receiving_generation) "
                    "JOIN scheduled_tasks t ON t.id=s.task_id "
                    "WHERE i.receiving_generation=$1 AND s.task_id=$2",
                    pending.receiving,
                    pending.schedule,
                )
                if (
                    current is None
                    or current["ledger_id"] != pending.ledger
                    or current["source_name"] != pending.source
                    or current["body_digest"] != pending.digest
                    or current["receiving_incarnation"] != runtime.incarnation
                ):
                    raise PolicyUnavailableError("Native scheduled receiving challenge differs")
    return {
        "ledger_id": str(pending.ledger),
        "source": pending.source,
        "body_digest": pending.digest.hex(),
        "receiver": runtime.name,
        "receiving_incarnation": str(runtime.incarnation),
        "receiving_generation": str(pending.receiving),
    }


async def receiving_question_fenced(conn: Any, receiving: UUID) -> bool:
    return (
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_received_delegation_floors "
            "WHERE receiving_generation=$1) OR EXISTS("
            "SELECT 1 FROM location_received_delegation_claims c "
            "JOIN location_received_delegation_contexts q USING(claim_generation) "
            "JOIN location_runtime_context_dispositions d USING(input_generation) "
            "WHERE c.receiving_generation=$1)",
            receiving,
        )
        is True
    )


async def _load_source_question(writer: Any, conn: Any, ledger: UUID, receiver: str):
    runtime = writer.runtime
    await runtime.lock_domain(conn)
    source = await conn.fetchrow(
        "SELECT * FROM location_native_delegation_inputs WHERE ledger_id=$1",
        ledger,
    )
    canonical = await conn.fetchrow(
        "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
        ledger,
    )
    if source is None and canonical is not None and canonical["target_butler"] == receiver:
        from butlers.chronicler.location_ordinary_delegation import load_ordinary_question

        return await load_ordinary_question(writer, conn, canonical)
    if (
        source is None
        or canonical is None
        or canonical["asking_butler"] != runtime.name
        or canonical["target_butler"] != receiver
        or canonical["status"] not in {"pending", "routed"}
        or question_digest(dict(canonical)) != source["body_digest"]
    ):
        raise PolicyUnavailableError("Native question source body is unavailable")
    parents = await conn.fetch(
        "SELECT * FROM location_native_delegation_parents WHERE question_generation=$1",
        source["question_generation"],
    )
    if len(parents) != source["parent_count"]:
        raise PolicyUnavailableError("Native question source parent cohort differs")
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_delegation_dispositions "
        "WHERE question_generation=$1)",
        source["question_generation"],
    ):
        raise PolicyUnavailableError("Native question source is disposed")
    for parent in parents:
        if parent["parent_kind"] == "catalog_loan":
            refused = await conn.fetchval(
                "SELECT NOT EXISTS(SELECT 1 FROM location_catalog_copy_loans "
                "WHERE loan_id=$1 AND body_digest=$2) OR EXISTS("
                "SELECT 1 FROM location_catalog_copy_dispositions WHERE loan_id=$1)",
                parent["parent_generation"],
                parent["parent_digest"],
            )
        elif parent["parent_kind"] == "received_question":
            refused = not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_received_delegation_inputs "
                "WHERE receiving_generation=$1 AND body_digest=$2)",
                parent["parent_generation"],
                parent["parent_digest"],
            )
        elif runtime.name == "chronicler":
            refused = await conn.fetchval(
                "SELECT NOT EXISTS(SELECT 1 FROM location_native_copy_births "
                "WHERE copy_generation=$1 AND input_digest=$2) OR EXISTS("
                "SELECT 1 FROM location_native_copy_dispositions WHERE copy_generation=$1) "
                "OR EXISTS(SELECT 1 FROM location_native_copy_births b "
                "JOIN location_retention_plan_outputs o USING(output_kind,output_id) "
                "WHERE b.copy_generation=$1)",
                parent["parent_generation"],
                parent["parent_digest"],
            )
        else:
            raise PolicyUnavailableError("Native question owning parent differs")
        if parent["parent_kind"] == "received_question":
            refused = refused or await receiving_question_fenced(conn, parent["parent_generation"])
        if refused:
            raise PolicyUnavailableError("Native question source parent is fenced")
    return source


async def prepare_question_source(writer: Any, token: str, body: dict) -> dict:
    runtime = writer.runtime
    ledger = UUID(body["ledger_id"])
    receiver = body["receiver"]
    if not isinstance(receiver, str) or not receiver or not runtime.active:
        raise PolicyUnavailableError("Native question source constructor differs")

    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            source = await _load_source_question(writer, conn, ledger, receiver)
    # Network is outside both policy transactions. The fixed receiver must
    # possess the constructor pending cell before any delivery is enrolled.
    witness = await runtime.exchange(
        await runtime.endpoint(receiver),
        token,
        {
            "op": "question_challenge",
            "ledger_id": str(ledger),
            "source": runtime.name,
            "body_digest": source["body_digest"].hex(),
        },
    )
    if (
        witness.get("receiver") != receiver
        or witness.get("source") != runtime.name
        or witness.get("ledger_id") != str(ledger)
        or witness.get("body_digest") != source["body_digest"].hex()
    ):
        raise PolicyUnavailableError("Native question receiver binding differs")
    incarnation = UUID(witness["receiving_incarnation"])
    receiving = UUID(witness["receiving_generation"])
    loan = uuid4()
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            current = await _load_source_question(writer, conn, ledger, receiver)
            if current["question_generation"] != source["question_generation"]:
                raise PolicyUnavailableError("Native question source generation differs")
            if source.get("classification") == "ordinary":
                if current.get("classification") != "ordinary":
                    raise PolicyUnavailableError("Ordinary question classification changed")
                return {
                    **witness,
                    "source_incarnation": str(runtime.incarnation),
                    "question_generation": str(source["question_generation"]),
                    "parent_count": 0,
                    "exclusive_input": False,
                    "classification": "ordinary",
                }
            await conn.execute(
                "INSERT INTO location_native_delegation_loans "
                "(loan_id,question_generation,receiver_name,receiving_incarnation,"
                "receiving_generation,body_digest) VALUES($1,$2,$3,$4,$5,$6)",
                loan,
                source["question_generation"],
                receiver,
                incarnation,
                receiving,
                source["body_digest"],
            )
    async with runtime.domain.acquire() as readback:
        committed = await readback.fetchrow(
            "SELECT * FROM location_native_delegation_loans WHERE loan_id=$1",
            loan,
        )
    if (
        committed is None
        or committed["question_generation"] != source["question_generation"]
        or committed["receiver_name"] != receiver
        or committed["receiving_incarnation"] != incarnation
        or committed["receiving_generation"] != receiving
        or committed["body_digest"] != source["body_digest"]
    ):
        raise PolicyUnavailableError("Committed native question delivery is unknown")
    return {
        "ledger_id": str(ledger),
        "source": runtime.name,
        "source_incarnation": str(runtime.incarnation),
        "question_generation": str(source["question_generation"]),
        "loan_id": str(loan),
        "body_digest": source["body_digest"].hex(),
        "receiving_incarnation": str(incarnation),
        "receiving_generation": str(receiving),
        "parent_count": source["parent_count"],
        "exclusive_input": source["exclusive_input"],
    }


async def verify_question_delivery(writer: Any, token: str, body: dict) -> dict:
    """Current source-owned loan, checked against the live receiving constructor."""
    runtime = writer.runtime
    if not runtime.active or not isinstance(body["receiver"], str):
        raise PolicyUnavailableError("Native question delivery constructor differs")
    loan_id = UUID(body["loan_id"])
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            loan = await conn.fetchrow(
                "SELECT * FROM location_native_delegation_loans WHERE loan_id=$1", loan_id
            )
            if loan is None or loan["receiver_name"] != body["receiver"]:
                raise PolicyUnavailableError("Native question delivery is unavailable")
            header = await conn.fetchrow(
                "SELECT * FROM location_native_delegation_inputs WHERE question_generation=$1",
                loan["question_generation"],
            )
            if header is None:
                raise PolicyUnavailableError("Native question delivery source is unavailable")
            source = await _load_source_question(
                writer, conn, header["ledger_id"], loan["receiver_name"]
            )
            if source["body_digest"] != loan["body_digest"]:
                raise PolicyUnavailableError("Native question delivery body differs")
    witness = await runtime.exchange(
        await runtime.endpoint(loan["receiver_name"]),
        token,
        {
            "op": "question_challenge",
            "ledger_id": str(header["ledger_id"]),
            "source": runtime.name,
            "body_digest": loan["body_digest"].hex(),
        },
    )
    if witness != {
        "ledger_id": str(header["ledger_id"]),
        "source": runtime.name,
        "body_digest": loan["body_digest"].hex(),
        "receiver": loan["receiver_name"],
        "receiving_incarnation": str(loan["receiving_incarnation"]),
        "receiving_generation": str(loan["receiving_generation"]),
    }:
        raise PolicyUnavailableError("Native question current delivery differs")
    # Network never holds the source policy transaction. Recheck its exact
    # owning source and immutable loan after the online receiver challenge.
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            current = await _load_source_question(
                writer, conn, header["ledger_id"], loan["receiver_name"]
            )
            if current["question_generation"] != loan["question_generation"]:
                raise PolicyUnavailableError("Native question current source differs")
    return {
        "loan_id": str(loan_id),
        "question_generation": str(loan["question_generation"]),
        "body_digest": loan["body_digest"].hex(),
        "source_incarnation": str(runtime.incarnation),
    }


async def reserve_received_question(writer: Any, canonical: dict) -> _ReceivedQuestion | None:
    from butlers.chronicler.location_tool_copies import current_tool_copy

    runtime = writer.runtime
    tool = current_tool_copy(runtime)
    from butlers.chronicler.location_catalog_copies import _server_copy_scope, _server_copy_scopes

    server = _server_copy_scope.get()
    if server is not None and (
        not server.active
        or server.target != runtime.name
        or _server_copy_scopes.get(server.request) is not server
    ):
        raise PolicyUnavailableError("Native question receiving server differs")
    if (tool is None and server is None) or (
        tool is not None and (tool.module != "core" or tool.name != "delegate_receive")
    ):
        raise PolicyUnavailableError("Native question receiving invocation is unavailable")
    if canonical["target_butler"] != runtime.name or canonical["status"] != "pending":
        raise PolicyUnavailableError("Native question receiving target differs")
    writer.pending = {k: p for k, p in writer.pending.items() if p.deadline > time.monotonic()}
    if not runtime.active or len(writer.pending) >= 128:
        raise PolicyUnavailableError("Native question receiver capacity is unavailable")
    ledger, digest = UUID(str(canonical["id"])), question_digest(canonical)
    source = canonical["asking_butler"]
    token = secrets.token_urlsafe(32)
    receiving = uuid4()
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            if tool is not None and not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents "
                "WHERE tool_generation=$1 AND receiving_session=$2 "
                "AND tool_name='delegate_receive' AND module_name='core')",
                tool.generation,
                tool.session,
            ):
                raise PolicyUnavailableError("Native question registered receiving input differs")
            await conn.execute(
                "INSERT INTO location_received_delegation_attempts "
                "(receiving_generation,ledger_id,body_digest,receiving_incarnation,"
                "receiving_session,tool_generation,server_request) VALUES($1,$2,$3,$4,$5,$6,$7)",
                receiving,
                ledger,
                digest,
                runtime.incarnation,
                tool.session if tool else None,
                tool.generation if tool else None,
                server.request if server else None,
            )
    if not await runtime.domain.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_received_delegation_attempts "
        "WHERE receiving_generation=$1 AND ledger_id=$2 AND body_digest=$3 "
        "AND receiving_incarnation=$4)",
        receiving,
        ledger,
        digest,
        runtime.incarnation,
    ):
        raise PolicyUnavailableError("Committed receiving attempt is unknown")
    if server is not None:
        server.questions.append((runtime, receiving, digest))
    pending = _QuestionPending(
        ledger, source, digest, time.monotonic() + 30, receiving, tool, server=server
    )
    writer.pending[token] = pending
    try:
        prepared = await runtime.exchange(
            await runtime.endpoint(source),
            token,
            {"op": "question_source", "ledger_id": str(ledger), "receiver": runtime.name},
        )
        if (
            pending.deadline <= time.monotonic()
            or prepared.get("ledger_id") != str(ledger)
            or prepared.get("source") != source
            or prepared.get("body_digest") != digest.hex()
            or prepared.get("receiving_generation") != str(pending.receiving)
            or prepared.get("receiving_incarnation") != str(runtime.incarnation)
            or type(prepared.get("parent_count")) is not int
            or prepared["parent_count"] < 0
            or type(prepared.get("exclusive_input")) is not bool
        ):
            raise PolicyUnavailableError("Native prepared question input differs")
        if prepared.get("classification") == "ordinary":
            # Positive fixed-source classification arrived through this
            # receiver's actual pending challenge, not a caller label or the
            # absence of native ancestry. Recheck the real lifetime/body
            # after the online exchange before ordinary schedule admission.
            if prepared["parent_count"] != 0 or prepared["exclusive_input"] is not False:
                raise PolicyUnavailableError("Ordinary question input classification differs")
            UUID(prepared["source_incarnation"])
            UUID(prepared["question_generation"])
            async with runtime.domain.acquire() as conn:
                async with conn.transaction():
                    await runtime.lock_domain(conn)
                    current = await conn.fetchrow(
                        "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                        ledger,
                    )
                    if (
                        pending.deadline <= time.monotonic()
                        or not runtime.active
                        or (tool is not None and not tool.active)
                        or (server is not None and not server.active)
                        or current is None
                        or question_digest(dict(current)) != digest
                        or current["status"] != "pending"
                        or current["target_butler"] != runtime.name
                        or current["asking_butler"] != source
                    ):
                        raise PolicyUnavailableError("Ordinary receiving question changed")
            return None
        pending.question = UUID(prepared["question_generation"])
        pending.loan = UUID(prepared["loan_id"])
        pending.source_incarnation = UUID(prepared["source_incarnation"])
        async with runtime.domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                if (
                    pending.deadline <= time.monotonic()
                    or not runtime.active
                    or (tool is not None and not tool.active)
                    or (server is not None and not server.active)
                ):
                    raise PolicyUnavailableError("Native receiving input lifetime expired")
                if await receiving_question_fenced(conn, pending.receiving):
                    raise PolicyUnavailableError("Native receiving input was fenced")
                current = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                    ledger,
                )
                if current is None or question_digest(dict(current)) != digest:
                    raise PolicyUnavailableError("Native receiving question body changed")
                await conn.execute(
                    "INSERT INTO location_received_delegation_inputs "
                    "(receiving_generation,ledger_id,source_name,source_incarnation,question_generation,"
                    "loan_id,body_digest,receiving_incarnation,parent_count,exclusive_input,"
                    "receiving_session,tool_generation,server_request) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
                    pending.receiving,
                    ledger,
                    source,
                    pending.source_incarnation,
                    pending.question,
                    pending.loan,
                    digest,
                    runtime.incarnation,
                    prepared["parent_count"],
                    prepared["exclusive_input"],
                    tool.session if tool is not None else None,
                    tool.generation if tool is not None else None,
                    server.request if server is not None else None,
                )
        async with runtime.domain.acquire() as observed:
            committed = await observed.fetchrow(
                "SELECT * FROM location_received_delegation_inputs WHERE receiving_generation=$1",
                pending.receiving,
            )
        if (
            committed is None
            or committed["ledger_id"] != ledger
            or committed["source_name"] != source
            or committed["source_incarnation"] != pending.source_incarnation
            or committed["question_generation"] != pending.question
            or committed["loan_id"] != pending.loan
            or committed["body_digest"] != digest
            or committed["receiving_incarnation"] != runtime.incarnation
            or committed["receiving_session"] != (tool.session if tool is not None else None)
            or committed["tool_generation"] != (tool.generation if tool is not None else None)
            or committed["server_request"] != (server.request if server is not None else None)
            or committed["parent_count"] != prepared["parent_count"]
            or committed["exclusive_input"] is not prepared["exclusive_input"]
        ):
            raise PolicyUnavailableError("Committed native receiving question is unknown")
        admission = _ReceivedQuestion(writer, pending.receiving, ledger, digest, pending.deadline)
        writer.receiving[pending.receiving] = admission
        return admission
    finally:
        writer.pending.pop(token, None)


async def schedule_received_question(admission: Any, prompt: str, write: Any) -> Any:
    import hashlib

    if (
        not isinstance(admission, _ReceivedQuestion)
        or not admission.active
        or admission.deadline <= time.monotonic()
        or admission.writer.receiving.get(admission.generation) is not admission
        or not admission.writer.runtime.active
    ):
        raise PolicyUnavailableError("Native question schedule lifetime differs")
    runtime = admission.writer.runtime
    digest = hashlib.sha256(prompt.encode()).digest()
    try:
        async with runtime.domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                if (
                    admission.deadline <= time.monotonic()
                    or not admission.active
                    or not runtime.active
                ):
                    raise PolicyUnavailableError("Native question schedule lifetime differs")
                if await receiving_question_fenced(conn, admission.generation):
                    raise PolicyUnavailableError("Native receiving schedule was fenced")
                receiving = await conn.fetchrow(
                    "SELECT * FROM location_received_delegation_inputs "
                    "WHERE receiving_generation=$1",
                    admission.generation,
                )
                canonical = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                    admission.ledger,
                )
                if (
                    receiving is None
                    or receiving["body_digest"] != admission.digest
                    or receiving["receiving_incarnation"] != runtime.incarnation
                    or canonical is None
                    or canonical["target_butler"] != runtime.name
                    or question_digest(dict(canonical)) != admission.digest
                ):
                    raise PolicyUnavailableError("Native scheduled question source differs")
                task = await write(conn)
                if (
                    await conn.fetchval("SELECT prompt FROM scheduled_tasks WHERE id=$1", task)
                    != prompt
                ):
                    raise PolicyUnavailableError("Native scheduled question prompt differs")
                await conn.execute(
                    "INSERT INTO location_received_delegation_schedules "
                    "(receiving_generation,task_id,prompt_digest) VALUES($1,$2,$3)",
                    admission.generation,
                    task,
                    digest,
                )
        async with runtime.domain.acquire() as observed:
            body = await observed.fetchval("SELECT prompt FROM scheduled_tasks WHERE id=$1", task)
            bound = await observed.fetchrow(
                "SELECT * FROM location_received_delegation_schedules "
                "WHERE receiving_generation=$1",
                admission.generation,
            )
        if (
            bound is None
            or bound["task_id"] != task
            or bound["prompt_digest"] != digest
            or body != prompt
        ):
            raise PolicyUnavailableError("Committed native question schedule is unknown")
        return task
    finally:
        admission.active = False
        admission.writer.receiving.pop(admission.generation, None)


async def finish_received_server(runtime: Any, receiving: UUID, digest: bytes, server: UUID):
    """Native ASGI completion attests ONLY this server copy, never its recipient."""
    receipt = uuid4()
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            if not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_received_delegation_attempts "
                "WHERE receiving_generation=$1 AND body_digest=$2 AND server_request=$3)",
                receiving,
                digest,
                server,
            ):
                raise PolicyUnavailableError("Native receiving server copy differs")
            await conn.execute(
                "INSERT INTO location_received_delegation_server_finished "
                "(receiving_generation,server_request,body_digest,receipt_id) VALUES($1,$2,$3,$4)",
                receiving,
                server,
                digest,
                receipt,
            )
    if (
        await runtime.domain.fetchval(
            "SELECT receipt_id FROM location_received_delegation_server_finished "
            "WHERE receiving_generation=$1 AND server_request=$2 AND body_digest=$3",
            receiving,
            server,
            digest,
        )
        != receipt
    ):
        raise PolicyUnavailableError("Committed receiving server lifetime is unknown")
