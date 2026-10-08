"""Fixed owning answer-source admission before a return prompt is copied.

A public ledger locator or wake key selects canonical data, never lineage.
Only the source's actual immutable native answer birth and the receiver's
constructor-owned invocation can enroll a loan. Control responses contain
bounded generation/digest metadata; body delivery stays on the existing
public-ledger writer/reader path, with no peer-private SQL or credentials.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_delegation_answers import answer_bundle_digest
from butlers.chronicler.location_policy import PolicyUnavailableError


@dataclass
class _AnswerPending:
    ledger: UUID
    source: str
    wake_key: str
    receiving: UUID
    deadline: float
    tool: Any = None
    server: Any = None
    schedule: UUID | None = None


@dataclass
class _ReceivedAnswer:
    writer: Any
    generation: UUID
    ledger: UUID
    bundle_digest: bytes
    deadline: float
    tool: Any = None
    server: Any = None
    active: bool = True


def _receiving(admission: Any) -> Any:
    if (
        not isinstance(admission, _ReceivedAnswer)
        or not admission.active
        or not admission.writer.runtime.active
        or admission.deadline <= time.monotonic()
        or admission.writer.receiving_answers.get(admission.generation) is not admission
        or (admission.tool is not None and not admission.tool.active)
        or (admission.server is not None and not admission.server.active)
    ):
        raise PolicyUnavailableError("Native answer admitted lifetime differs")
    return admission.writer.runtime


def _pending(writer: Any, token: str) -> _AnswerPending:
    selected = writer.answer_pending.get(token)
    if (
        not writer.runtime.active
        or not isinstance(selected, _AnswerPending)
        or selected.deadline <= time.monotonic()
    ):
        raise PolicyUnavailableError("Native answer receiving lifetime differs")
    return selected


async def answer_challenge(writer: Any, token: str, body: dict) -> dict:
    """Answer only a current locally enrolled actual server or MCP invocation."""
    selected = _pending(writer, token)
    runtime = writer.runtime
    if body != {
        "op": "answer_challenge",
        "ledger_id": str(selected.ledger),
        "source": selected.source,
        "wake_key": selected.wake_key,
    }:
        raise PolicyUnavailableError("Native answer receiver challenge differs")
    if selected.schedule is not None:
        if selected.tool is not None or selected.server is not None:
            raise PolicyUnavailableError("Native answer scheduled producer differs")
    elif selected.tool is not None:
        from butlers.chronicler.location_tool_copies import _ToolCopy

        # The source callback is a distinct HTTP invocation. Its ContextVar
        # cannot inherit the original caller's MCP token. This pending cell
        # retains the actual enrolled tool's finite lifetime, never a copied
        # caller object or source-supplied session field.
        if (
            not isinstance(selected.tool, _ToolCopy)
            or not selected.tool.active
            or selected.tool.runtime is not runtime
            or selected.tool.module != "core"
            or selected.tool.name != "delegate_wake"
        ):
            raise PolicyUnavailableError("Native answer receiving tool differs")
    elif selected.server is not None:
        from butlers.chronicler.location_catalog_copies import _server_copy_scopes

        if (
            not selected.server.active
            or selected.server.target != runtime.name
            or _server_copy_scopes.get(selected.server.request) is not selected.server
        ):
            raise PolicyUnavailableError("Native answer receiving server differs")
    else:
        raise PolicyUnavailableError("Native answer receiving producer is unavailable")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            attempt = await conn.fetchrow(
                "SELECT * FROM location_received_answer_attempts WHERE receiving_generation=$1",
                selected.receiving,
            )
            if attempt is None or any(
                attempt[key] != value
                for key, value in {
                    "ledger_id": selected.ledger,
                    "source_name": selected.source,
                    "wake_key": selected.wake_key,
                    "receiving_incarnation": runtime.incarnation,
                    **(
                        {}
                        if selected.schedule is not None
                        else {
                            "receiving_session": selected.tool.session if selected.tool else None,
                            "tool_generation": selected.tool.generation if selected.tool else None,
                            "server_request": selected.server.request if selected.server else None,
                        }
                    ),
                }.items()
            ):
                raise PolicyUnavailableError("Native answer receiving attempt differs")
            if selected.schedule is not None:
                import hashlib

                stored = await conn.fetchrow(
                    "SELECT i.*,s.prompt_digest,t.prompt FROM location_received_answer_inputs i "
                    "JOIN location_received_answer_schedules s USING(receiving_generation) "
                    "JOIN scheduled_tasks t ON t.id=s.task_id "
                    "WHERE i.receiving_generation=$1 AND s.task_id=$2",
                    selected.receiving,
                    selected.schedule,
                )
                if (
                    stored is None
                    or stored["prompt_digest"] != hashlib.sha256(stored["prompt"].encode()).digest()
                    or await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_received_answer_floors "
                        "WHERE receiving_generation=$1)",
                        selected.receiving,
                    )
                ):
                    raise PolicyUnavailableError("Native answer scheduled input differs")
            if selected.tool is not None and not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents "
                "WHERE tool_generation=$1 AND receiving_session=$2 "
                "AND module_name='core' AND tool_name='delegate_wake')",
                selected.tool.generation,
                selected.tool.session,
            ):
                raise PolicyUnavailableError("Native answer registered tool differs")
    return {
        "receiver": runtime.name,
        "ledger_id": str(selected.ledger),
        "source": selected.source,
        "wake_key": selected.wake_key,
        "receiving_generation": str(selected.receiving),
        "receiving_incarnation": str(runtime.incarnation),
    }


async def _answer_source(writer: Any, conn: Any, ledger: UUID, receiver: str) -> dict:
    """Complete own source read under its actual policy-first transaction."""
    runtime = writer.runtime
    await runtime.lock_domain(conn)
    native = await conn.fetchrow(
        "SELECT * FROM location_native_delegation_answers WHERE ledger_id=$1", ledger
    )
    canonical = await conn.fetchrow(
        "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE", ledger
    )
    if (
        native is None
        or canonical is None
        or native["bundle_digest"] is None
        or canonical["asking_butler"] != receiver
        or canonical["answering_butler"] != runtime.name
        or native["body_digest"].hex() != canonical["answer_digest"]
        or answer_bundle_digest(canonical) != native["bundle_digest"]
        or type(native["parent_count"]) is not int
        or native["parent_count"] < 0
        or type(native["exclusive_input"]) is not bool
    ):
        raise PolicyUnavailableError("Native answer source body is unavailable")
    parents = await conn.fetch(
        "SELECT * FROM location_native_delegation_answer_parents WHERE answer_generation=$1",
        native["answer_generation"],
    )
    if len(parents) != native["parent_count"] or await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_delegation_answer_dispositions "
        "WHERE answer_generation=$1)",
        native["answer_generation"],
    ):
        raise PolicyUnavailableError("Native answer source cohort differs")
    from butlers.chronicler.location_delegation_receivers import receiving_question_fenced

    for parent in parents:
        if parent["parent_kind"] == "received_answer":
            refused = await conn.fetchval(
                "SELECT NOT EXISTS(SELECT 1 FROM location_received_answer_inputs "
                "WHERE receiving_generation=$1 AND bundle_digest=$2) OR EXISTS("
                "SELECT 1 FROM location_received_answer_floors WHERE receiving_generation=$1)",
                parent["parent_generation"],
                parent["parent_digest"],
            )
        elif parent["parent_kind"] == "received_question":
            valid = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_received_delegation_inputs "
                "WHERE receiving_generation=$1 AND body_digest=$2)",
                parent["parent_generation"],
                parent["parent_digest"],
            )
            refused = not valid or await receiving_question_fenced(
                conn, parent["parent_generation"]
            )
        elif parent["parent_kind"] == "catalog_loan":
            refused = await conn.fetchval(
                "SELECT NOT EXISTS(SELECT 1 FROM location_catalog_copy_loans l "
                "JOIN location_catalog_copy_lifetimes h USING(loan_id,body_digest) "
                "WHERE l.loan_id=$1 AND l.body_digest=$2 AND "
                "((h.holder_kind='unbound_processing' AND h.holder_id IN ($3,$4)) "
                "OR (h.holder_kind='runtime_session' AND h.holder_id=$5))) OR EXISTS("
                "SELECT 1 FROM location_catalog_copy_dispositions WHERE loan_id=$1)",
                parent["parent_generation"],
                parent["parent_digest"],
                native["context_generation"],
                native["tool_generation"],
                native["receiving_session"],
            )
        elif parent["parent_kind"] == "native_copy" and runtime.name == "chronicler":
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
            raise PolicyUnavailableError("Native answer owning parent differs")
        if refused is not False:
            raise PolicyUnavailableError("Native answer source parent is fenced")
    return dict(native, wake_key=canonical["wake_key"])


async def prepare_answer_source(writer: Any, token: str, body: dict) -> dict:
    runtime = writer.runtime
    ledger, receiver = UUID(body["ledger_id"]), body["receiver"]
    if not isinstance(receiver, str) or not receiver or not runtime.active:
        raise PolicyUnavailableError("Native answer source constructor differs")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            source = await _answer_source(writer, conn, ledger, receiver)
    # No network inside the business/policy transaction. The actual receiver
    # pending cell must still exist at its fixed registered endpoint.
    observed = await runtime.exchange(
        await runtime.endpoint(receiver),
        token,
        {
            "op": "answer_challenge",
            "ledger_id": str(ledger),
            "source": runtime.name,
            "wake_key": source["wake_key"],
        },
    )
    if any(
        observed.get(k) != v
        for k, v in {
            "receiver": receiver,
            "ledger_id": str(ledger),
            "source": runtime.name,
            "wake_key": source["wake_key"],
        }.items()
    ):
        raise PolicyUnavailableError("Native answer receiver binding differs")
    receiving = UUID(observed["receiving_generation"])
    incarnation = UUID(observed["receiving_incarnation"])
    loan = uuid4()
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            current = await _answer_source(writer, conn, ledger, receiver)
            if (
                current["answer_generation"] != source["answer_generation"]
                or current["bundle_digest"] != source["bundle_digest"]
                or current["wake_key"] != source["wake_key"]
            ):
                raise PolicyUnavailableError("Native answer source generation differs")
            await conn.execute(
                "INSERT INTO location_native_answer_loans "
                "(loan_id,answer_generation,receiving_generation,receiver_name,"
                "receiving_incarnation,bundle_digest) VALUES($1,$2,$3,$4,$5,$6) "
                "ON CONFLICT(receiving_generation) DO NOTHING",
                loan,
                source["answer_generation"],
                receiving,
                receiver,
                incarnation,
                source["bundle_digest"],
            )
            # A lost acknowledgement resumes the exact immutable receiver
            # attempt and committed loan, never a replacement generation.
            stored = await conn.fetchrow(
                "SELECT * FROM location_native_answer_loans WHERE receiving_generation=$1",
                receiving,
            )
            if stored is None or any(
                stored[k] != v
                for k, v in {
                    "answer_generation": source["answer_generation"],
                    "receiver_name": receiver,
                    "receiving_incarnation": incarnation,
                    "bundle_digest": source["bundle_digest"],
                }.items()
            ):
                raise PolicyUnavailableError("Native answer immutable loan differs")
            loan = stored["loan_id"]
    if not await runtime.domain.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_answer_loans WHERE loan_id=$1 "
        "AND answer_generation=$2 AND receiving_generation=$3 AND receiver_name=$4 "
        "AND receiving_incarnation=$5 AND bundle_digest=$6)",
        loan,
        source["answer_generation"],
        receiving,
        receiver,
        incarnation,
        source["bundle_digest"],
    ):
        raise PolicyUnavailableError("Committed native answer loan is unknown")
    return {
        **observed,
        "loan_id": str(loan),
        "answer_generation": str(source["answer_generation"]),
        "bundle_digest": source["bundle_digest"].hex(),
        "source_incarnation": str(runtime.incarnation),
        "parent_count": source["parent_count"],
        "exclusive_input": source["exclusive_input"],
    }


async def verify_answer_delivery(writer: Any, token: str, body: dict) -> dict:
    runtime = writer.runtime
    loan_id, receiver = UUID(body["loan_id"]), body["receiver"]
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            loan = await conn.fetchrow(
                "SELECT l.*,a.ledger_id FROM location_native_answer_loans l "
                "JOIN location_native_delegation_answers a USING(answer_generation) "
                "WHERE l.loan_id=$1 AND l.receiver_name=$2",
                loan_id,
                receiver,
            )
            if loan is None:
                raise PolicyUnavailableError("Native answer delivery is unavailable")
            source = await _answer_source(writer, conn, loan["ledger_id"], receiver)
    witness = await runtime.exchange(
        await runtime.endpoint(receiver),
        token,
        {
            "op": "answer_challenge",
            "ledger_id": str(loan["ledger_id"]),
            "source": runtime.name,
            "wake_key": source["wake_key"],
        },
    )
    if (
        witness.get("receiving_generation") != str(loan["receiving_generation"])
        or witness.get("receiving_incarnation") != str(loan["receiving_incarnation"])
        or witness.get("receiver") != receiver
        or witness.get("source") != runtime.name
        or witness.get("ledger_id") != str(loan["ledger_id"])
        or witness.get("wake_key") != source["wake_key"]
    ):
        raise PolicyUnavailableError("Native answer current receiver differs")
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            current = await _answer_source(writer, conn, loan["ledger_id"], receiver)
            if (
                current["answer_generation"] != loan["answer_generation"]
                or current["bundle_digest"] != loan["bundle_digest"]
            ):
                raise PolicyUnavailableError("Native answer current source differs")
    return {
        "loan_id": str(loan_id),
        "answer_generation": str(loan["answer_generation"]),
        "bundle_digest": loan["bundle_digest"].hex(),
        "source_incarnation": str(runtime.incarnation),
        "parent_count": current["parent_count"],
        "exclusive_input": current["exclusive_input"],
    }


async def reserve_received_answer(
    writer: Any, ledger: UUID, wake_key: str
) -> tuple[dict, _ReceivedAnswer]:
    """Reserve the actual receiver before canonical question/answer processing.

    This function receives locators from the native wake handler, not an
    authority-bearing body. The constructor reads minimal canonical metadata,
    commits its own attempt, challenges the registered answer owner, then
    checks the full returned canonical body against that owner's frozen birth.
    A caller cannot create an invocation by supplying a session or actor.
    """
    from butlers.chronicler.location_catalog_copies import _server_copy_scope, _server_copy_scopes
    from butlers.chronicler.location_tool_copies import current_tool_copy

    runtime = writer.runtime
    tool, server = current_tool_copy(runtime), _server_copy_scope.get()
    if not runtime.active or (tool is None and server is None):
        raise PolicyUnavailableError("Native answer receiving invocation is unavailable")
    if tool is not None and (tool.module != "core" or tool.name != "delegate_wake"):
        raise PolicyUnavailableError("Native answer receiving tool differs")
    if server is not None and (
        not server.active
        or server.target != runtime.name
        or _server_copy_scopes.get(server.request) is not server
    ):
        raise PolicyUnavailableError("Native answer receiving server differs")
    writer.receiving_answers = {
        key: value
        for key, value in writer.receiving_answers.items()
        if value.active and value.deadline > time.monotonic()
    }
    if len(writer.receiving_answers) >= 128:
        raise PolicyUnavailableError("Native admitted answer capacity is unavailable")
    writer.answer_pending = {
        key: value
        for key, value in writer.answer_pending.items()
        if value.deadline > time.monotonic()
    }
    if len(writer.answer_pending) >= 128:
        raise PolicyUnavailableError("Native answer receiver capacity is unavailable")
    receiving, nonce = uuid4(), secrets.token_urlsafe(32)
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            metadata = await conn.fetchrow(
                "SELECT id,status,asking_butler,target_butler,answering_butler,wake_key "
                "FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                ledger,
            )
            if (
                metadata is None
                or metadata["status"] != "answered"
                or metadata["asking_butler"] != runtime.name
                or metadata["wake_key"] != wake_key
                or metadata["answering_butler"] != metadata["target_butler"]
                or not isinstance(metadata["target_butler"], str)
            ):
                raise PolicyUnavailableError("Native answer canonical target differs")
            source = metadata["target_butler"]
            if tool is not None and not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_intents "
                "WHERE tool_generation=$1 AND receiving_session=$2 "
                "AND module_name='core' AND tool_name='delegate_wake')",
                tool.generation,
                tool.session,
            ):
                raise PolicyUnavailableError("Native answer registered receiving tool differs")
            await conn.execute(
                "INSERT INTO location_received_answer_attempts "
                "(receiving_generation,ledger_id,source_name,wake_key,receiving_incarnation,"
                "receiving_session,tool_generation,server_request) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                receiving,
                ledger,
                source,
                wake_key,
                runtime.incarnation,
                tool.session if tool else None,
                tool.generation if tool else None,
                server.request if server else None,
            )
    attempt = await runtime.domain.fetchrow(
        "SELECT * FROM location_received_answer_attempts WHERE receiving_generation=$1",
        receiving,
    )
    expected = {
        "receiving_generation": receiving,
        "ledger_id": ledger,
        "source_name": source,
        "wake_key": wake_key,
        "receiving_incarnation": runtime.incarnation,
        "receiving_session": tool.session if tool else None,
        "tool_generation": tool.generation if tool else None,
        "server_request": server.request if server else None,
    }
    if attempt is None or any(attempt[k] != v for k, v in expected.items()):
        raise PolicyUnavailableError("Committed native answer attempt is unknown")
    pending = _AnswerPending(
        ledger, source, wake_key, receiving, time.monotonic() + 30, tool=tool, server=server
    )
    writer.answer_pending[nonce] = pending
    try:
        prepared = await runtime.exchange(
            await runtime.endpoint(source),
            nonce,
            {"op": "answer_source", "ledger_id": str(ledger), "receiver": runtime.name},
        )
        expected_response = {
            "receiver": runtime.name,
            "ledger_id": str(ledger),
            "source": source,
            "wake_key": wake_key,
            "receiving_generation": str(receiving),
            "receiving_incarnation": str(runtime.incarnation),
        }
        if set(prepared) != set(expected_response) | {
            "loan_id",
            "answer_generation",
            "bundle_digest",
            "source_incarnation",
            "parent_count",
            "exclusive_input",
        } or any(prepared[k] != v for k, v in expected_response.items()):
            raise PolicyUnavailableError("Native answer prepared receiving binding differs")
        if (
            type(prepared["parent_count"]) is not int
            or prepared["parent_count"] < 0
            or type(prepared["exclusive_input"]) is not bool
        ):
            raise PolicyUnavailableError("Native answer source classification differs")
        loan, generation = UUID(prepared["loan_id"]), UUID(prepared["answer_generation"])
        source_incarnation = UUID(prepared["source_incarnation"])
        digest = bytes.fromhex(prepared["bundle_digest"])
        if len(digest) != 32:
            raise PolicyUnavailableError("Native answer prepared digest differs")
        verified = await runtime.exchange(
            await runtime.endpoint(source),
            nonce,
            {"op": "answer_delivery", "loan_id": str(loan), "receiver": runtime.name},
        )
        if verified != {
            "loan_id": str(loan),
            "answer_generation": str(generation),
            "bundle_digest": digest.hex(),
            "source_incarnation": str(source_incarnation),
            "parent_count": prepared["parent_count"],
            "exclusive_input": prepared["exclusive_input"],
        }:
            raise PolicyUnavailableError("Native answer current delivery differs")
        async with runtime.domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                if pending.deadline <= time.monotonic() or not runtime.active:
                    raise PolicyUnavailableError("Native answer input lifetime expired")
                # No copied processing or prompt construction precedes this
                # full native body match and own same-transaction reservation.
                canonical = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                    ledger,
                )
                if canonical is None or answer_bundle_digest(canonical) != digest:
                    raise PolicyUnavailableError("Native answer receiving body changed")
                await conn.execute(
                    "INSERT INTO location_received_answer_inputs "
                    "(receiving_generation,source_name,answer_generation,loan_id,bundle_digest,"
                    "source_incarnation,parent_count,exclusive_input) "
                    "VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
                    receiving,
                    source,
                    generation,
                    loan,
                    digest,
                    source_incarnation,
                    prepared["parent_count"],
                    prepared["exclusive_input"],
                )
        async with runtime.domain.acquire() as observed:
            admitted = await observed.fetchrow(
                "SELECT * FROM location_received_answer_inputs WHERE receiving_generation=$1",
                receiving,
            )
            current = await observed.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1",
                ledger,
            )
        if (
            admitted is None
            or any(
                admitted[k] != v
                for k, v in {
                    "receiving_generation": receiving,
                    "source_name": source,
                    "answer_generation": generation,
                    "loan_id": loan,
                    "bundle_digest": digest,
                    "source_incarnation": source_incarnation,
                    "parent_count": prepared["parent_count"],
                    "exclusive_input": prepared["exclusive_input"],
                }.items()
            )
            or current is None
            or answer_bundle_digest(current) != digest
        ):
            raise PolicyUnavailableError("Committed native answer input is unknown")
        admission = _ReceivedAnswer(
            writer, receiving, ledger, digest, pending.deadline, tool=tool, server=server
        )
        writer.receiving_answers[receiving] = admission
        return dict(current), admission
    finally:
        writer.answer_pending.pop(nonce, None)


async def answer_control(writer: Any, token: str, body: dict) -> dict:
    """Exact constructor-installed private operations, no caller endpoint/argv."""
    handlers = {
        "answer_challenge": ({"op", "ledger_id", "source", "wake_key"}, answer_challenge),
        "answer_source": ({"op", "ledger_id", "receiver"}, prepare_answer_source),
        "answer_delivery": ({"op", "loan_id", "receiver"}, verify_answer_delivery),
    }
    selected = handlers.get(body.get("op"))
    if selected is None or set(body) != selected[0]:
        raise PolicyUnavailableError("Native answer control fields differ")
    return await selected[1](writer, token, body)


async def schedule_received_answer(admission: Any, write: Any) -> dict:
    """Bind the actual return task in the same owning business transaction.

    The internal callback receives only the already checked canonical row and
    this configured connection. It cannot commit a task before its immutable
    input binding. A successful write is still UNKNOWN until a separate
    acquisition reads both exact prompt and binding; no result is returned on
    a lost acknowledgement or changed body.
    """
    import hashlib

    from butlers.core.delegation_wake import _build_return_task_prompt

    runtime = _receiving(admission)
    try:
        async with runtime.domain.acquire() as conn:
            async with conn.transaction():
                await runtime.lock_domain(conn)
                _receiving(admission)
                captured = await conn.fetchrow(
                    "SELECT i.*,a.ledger_id,a.wake_key,a.receiving_incarnation "
                    "FROM location_received_answer_inputs i "
                    "JOIN location_received_answer_attempts a USING(receiving_generation) "
                    "WHERE i.receiving_generation=$1",
                    admission.generation,
                )
                canonical = await conn.fetchrow(
                    "SELECT * FROM public.delegation_ledger WHERE id=$1 FOR UPDATE",
                    admission.ledger,
                )
                if (
                    captured is None
                    or captured["ledger_id"] != admission.ledger
                    or captured["bundle_digest"] != admission.bundle_digest
                    or captured["receiving_incarnation"] != runtime.incarnation
                    or canonical is None
                    or canonical["asking_butler"] != runtime.name
                    or canonical["wake_key"] != captured["wake_key"]
                    or answer_bundle_digest(canonical) != admission.bundle_digest
                ):
                    raise PolicyUnavailableError("Native return task input differs")
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_received_answer_floors "
                    "WHERE receiving_generation=$1)",
                    admission.generation,
                ):
                    raise PolicyUnavailableError("Native return task input is fenced")
                prompt = _build_return_task_prompt(
                    ledger_id=admission.ledger,
                    asking_butler=canonical["asking_butler"],
                    target_butler=canonical["target_butler"],
                    question=canonical["question"],
                    answer=canonical["answer"],
                    wake_key=canonical["wake_key"],
                    answer_digest=canonical["answer_digest"],
                )
                prompt_digest = hashlib.sha256(prompt.encode()).digest()
                result = await write(conn, dict(canonical))
                _receiving(admission)
                if result.get("status") != "ok":
                    return result  # Original conflict/error; no input-copy success.
                task = UUID(result["task_id"])
                # Footer-only equality cannot bless altered copied prose.
                if (
                    await conn.fetchval(
                        "SELECT prompt FROM scheduled_tasks WHERE id=$1 FOR UPDATE", task
                    )
                    != prompt
                ):
                    raise PolicyUnavailableError("Native return task prompt differs")
                await conn.execute(
                    "INSERT INTO location_received_answer_schedules "
                    "(receiving_generation,task_id,prompt_digest) VALUES($1,$2,$3) "
                    "ON CONFLICT(receiving_generation) DO NOTHING",
                    admission.generation,
                    task,
                    prompt_digest,
                )
                stored = await conn.fetchrow(
                    "SELECT * FROM location_received_answer_schedules "
                    "WHERE receiving_generation=$1",
                    admission.generation,
                )
                if (
                    stored is None
                    or stored["task_id"] != task
                    or stored["prompt_digest"] != prompt_digest
                ):
                    raise PolicyUnavailableError("Native return task immutable binding differs")
        async with runtime.domain.acquire() as observed:
            stored = await observed.fetchrow(
                "SELECT * FROM location_received_answer_schedules WHERE receiving_generation=$1",
                admission.generation,
            )
            body = await observed.fetchval("SELECT prompt FROM scheduled_tasks WHERE id=$1", task)
            canonical = await observed.fetchrow(
                "SELECT * FROM public.delegation_ledger WHERE id=$1", admission.ledger
            )
        if (
            stored is None
            or stored["task_id"] != task
            or stored["prompt_digest"] != prompt_digest
            or body != prompt
            or canonical is None
            or answer_bundle_digest(canonical) != admission.bundle_digest
        ):
            raise PolicyUnavailableError("Committed native return task is unknown")
        return result
    finally:
        admission.active = False
        admission.writer.receiving_answers.pop(admission.generation, None)
