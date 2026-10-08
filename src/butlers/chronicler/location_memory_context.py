"""Private pre-context runtime input and native descendant lifetime.

The fixed daemon/Memory constructors supply the runtime and pools. A returned
session UUID, caller context or remote ACK never creates this binding. The
stored intent precedes any borrowed catalog body; session admission commits
its exact composed body and complete input digest on the session writer.
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_retention import PolicyUnavailableError
from butlers.location_retention import content_digest


@dataclass
class _RuntimeContext:
    runtime: Any
    generation: UUID
    session: UUID
    generated_prompt: bool
    server_request: UUID | None = None
    loans: list[tuple[UUID, bytes]] = field(default_factory=list)
    local_rows: set[tuple[str, UUID]] = field(default_factory=set)
    known_context: bool = True
    context_bytes: int = 0
    context_digest: bytes | None = None
    system_digest: bytes | None = None
    prompt_digest: bytes | None = None
    admitted: bool = False
    active: bool = True


_current_runtime_context: ContextVar[_RuntimeContext | None] = ContextVar(
    "native_location_runtime_context", default=None
)


def current_runtime_context(pool: Any = None):
    binding = _current_runtime_context.get()
    if binding is None:
        return None
    if not binding.active or (pool is not None and binding.runtime.domain is not pool):
        raise PolicyUnavailableError("Native runtime context lifetime differs")
    return binding


async def begin_runtime_context(pool: Any, spawner: Any):
    from butlers.chronicler.location_input_binding import (
        _current_dispatch_input,
        registered_dispatcher,
    )

    runtime = context_writer(pool)
    if runtime is not None and not runtime.active:
        raise PolicyUnavailableError("Native context constructor lifetime ended")
    if runtime is None:
        return None
    if not registered_dispatcher(pool, spawner):
        raise PolicyUnavailableError("Native context constructor differs")
    from butlers.chronicler.location_delegation_processing import current_scheduled_question

    scheduled = current_scheduled_question(pool)
    dispatch = _current_dispatch_input.get()
    session = dispatch.session_id if dispatch is not None and dispatch.active else uuid4()
    binding = _RuntimeContext(
        runtime,
        uuid4(),
        session,
        (dispatch is not None and dispatch.active) or (scheduled is not None),
    )
    if scheduled is not None and not scheduled.exclusive:
        binding.known_context = False
    from butlers.chronicler.location_catalog_copies import _server_copy_scope

    server = _server_copy_scope.get()
    if server is not None and server.active:
        binding.server_request = server.request
        server.contexts.append((runtime, binding.generation))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_runtime_context_intents "
                "(input_generation,receiving_session,server_request) VALUES($1,$2,$3)",
                binding.generation,
                session,
                binding.server_request,
            )
            if scheduled is not None:
                await conn.execute(
                    "INSERT INTO location_runtime_context_question_intents "
                    "(input_generation,claim_generation) VALUES($1,$2)",
                    binding.generation,
                    scheduled.generation,
                )
    if (
        await pool.fetchval(
            "SELECT receiving_session FROM location_runtime_context_intents "
            "WHERE input_generation=$1",
            binding.generation,
        )
        != session
    ):
        raise PolicyUnavailableError("Committed pre-context reservation is unknown")
    if (
        scheduled is not None
        and await pool.fetchval(
            "SELECT claim_generation FROM location_runtime_context_question_intents "
            "WHERE input_generation=$1",
            binding.generation,
        )
        != scheduled.generation
    ):
        raise PolicyUnavailableError("Committed question pre-context reservation is unknown")
    return binding, _current_runtime_context.set(binding)


def observe_context_rows(pool: Any, table: str, rows: list[dict]) -> None:
    """Actual context compiler reports its selected full section inputs."""
    binding = current_runtime_context()
    if binding is None or getattr(binding.runtime, "memory", None) is not pool:
        return
    for row in rows:
        identity = row.get("id")
        if identity is None:
            binding.known_context = False
        elif (table, UUID(str(identity))) not in binding.local_rows:
            binding.known_context = False


def capture_context_prompt(memory_context: str | None, system_prompt: str) -> None:
    binding = current_runtime_context()
    if binding is not None:
        binding.context_bytes = len((memory_context or "").encode())
        binding.context_digest = hashlib.sha256((memory_context or "").encode()).digest()
        binding.system_digest = hashlib.sha256(system_prompt.encode()).digest()
        # Nonempty context without captured native inputs is independent.
        if memory_context and not binding.loans and not binding.local_rows:
            binding.known_context = False


async def bind_context_session(conn: Any, pool: Any, session: UUID, prompt: str) -> None:
    binding = current_runtime_context(pool)
    if binding is None:
        return
    if (
        binding.session != session
        or binding.system_digest is None
        or binding.context_digest is None
    ):
        raise PolicyUnavailableError("Native context composed input is unavailable")
    row = await conn.fetchrow(
        "SELECT prompt,effective_system_prompt FROM sessions WHERE id=$1", session
    )
    if (
        row is None
        or row["prompt"] != prompt
        or hashlib.sha256(row["effective_system_prompt"].encode()).digest() != binding.system_digest
    ):
        raise PolicyUnavailableError("Native runtime composed body changed")
    binding.prompt_digest = hashlib.sha256(prompt.encode()).digest()
    for loan, digest in binding.loans:
        if (
            await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_catalog_copy_lifetimes "
                "WHERE loan_id=$1 AND holder_id=$2 AND body_digest=$3 "
                "AND holder_kind='unbound_processing')",
                loan,
                binding.generation,
                digest,
            )
            is not True
        ):
            raise PolicyUnavailableError("Native context loan input differs")
    await conn.execute(
        "INSERT INTO location_runtime_context_bindings "
        "(input_generation,receiving_session,bundle_digest,context_digest,system_digest,"
        "prompt_digest,exclusive_input,context_bytes) VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
        binding.generation,
        session,
        content_digest(
            {
                "loans": [[str(loan), digest.hex()] for loan, digest in sorted(binding.loans)],
                "context": binding.context_digest.hex(),
                "system": binding.system_digest.hex(),
                "prompt": binding.prompt_digest.hex(),
            }
        ),
        binding.context_digest,
        binding.system_digest,
        binding.prompt_digest,
        binding.generated_prompt and binding.known_context,
        binding.context_bytes,
    )
    from butlers.chronicler.location_delegation_processing import bind_question_context

    await bind_question_context(conn, binding, prompt)
    binding.admitted = True


async def verify_context_session(pool: Any, session: UUID) -> None:
    binding = current_runtime_context(pool)
    if binding is None:
        return
    if (
        await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_runtime_context_bindings "
            "WHERE input_generation=$1 AND receiving_session=$2 AND system_digest=$3 "
            "AND prompt_digest=$4)",
            binding.generation,
            session,
            binding.system_digest,
            binding.prompt_digest,
        )
        is not True
    ):
        raise PolicyUnavailableError("Committed native context admission is unknown")

    from butlers.chronicler.location_delegation_processing import current_scheduled_question

    scheduled = current_scheduled_question(pool)
    if scheduled is not None and not await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_received_delegation_contexts q "
        "JOIN location_runtime_context_bindings b "
        "USING(input_generation,receiving_session,bundle_digest) "
        "WHERE q.input_generation=$1 AND q.claim_generation=$2 AND q.receiving_session=$3)",
        binding.generation,
        scheduled.generation,
        session,
    ):
        raise PolicyUnavailableError("Committed native question context is unknown")


async def end_runtime_context(handle: Any) -> None:
    if handle is None:
        return
    binding, token = handle
    binding.active = False
    _current_runtime_context.reset(token)
    runtime = binding.runtime
    receipt = uuid4()
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            await conn.execute(
                "INSERT INTO location_runtime_context_ended(input_generation,receipt_id) "
                "VALUES($1,$2)",
                binding.generation,
                receipt,
            )
    if (
        await runtime.domain.fetchval(
            "SELECT receipt_id FROM location_runtime_context_ended WHERE input_generation=$1",
            binding.generation,
        )
        != receipt
    ):
        raise PolicyUnavailableError("Committed native context lifetime is unknown")


def _own_schema(runtime: Any) -> str:
    # Constructor captured a canonical namespace, never a caller identifier.
    return '"' + runtime.identity[0].replace('"', '""') + '"'


async def _lock_memory_context(conn: Any, binding: _RuntimeContext) -> None:
    runtime = binding.runtime
    if (
        await conn.fetchval("SELECT current_schema()"),
        await conn.fetchval("SELECT current_user"),
    ) != runtime.memory_identity:
        raise PolicyUnavailableError("Configured context Memory role differs")
    if conn.is_closed():
        raise PolicyUnavailableError("Configured context writer is unavailable")
    # For Chronicler's configured chronicler_mem writer this is its already
    # adopted own-domain bridge, not a peer role/namespace or new grant.
    if runtime.name == "chronicler":
        from butlers.chronicler.location_memory_copies import _lock

        await _lock(conn, *runtime.memory_identity)
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
        "location:catalog-copy:" + runtime.name,
    )


async def context_episode_writer(pool: Any, conn: Any) -> None:
    binding = current_runtime_context()
    if binding is None or not (binding.loans or binding.local_rows or binding.generated_prompt):
        return
    if getattr(binding.runtime, "memory", None) is not pool or not binding.admitted:
        raise PolicyUnavailableError("Native context episode constructor differs")
    await _lock_memory_context(conn, binding)
    schema = _own_schema(binding.runtime)
    if await conn.fetchval(
        f"SELECT EXISTS(SELECT 1 FROM {schema}.location_runtime_context_dispositions "
        "WHERE input_generation=$1)",
        binding.generation,
    ):
        raise PolicyUnavailableError("Native context input was disposed")


async def bind_context_episode(pool: Any, conn: Any, episode: UUID) -> None:
    binding = current_runtime_context()
    if binding is None or not (binding.loans or binding.local_rows or binding.generated_prompt):
        return
    await context_episode_writer(pool, conn)
    from butlers.chronicler.location_memory_copies import episode_body_digest

    row = await conn.fetchrow("SELECT * FROM episodes WHERE id=$1 FOR UPDATE", episode)
    schema = _own_schema(binding.runtime)
    session = await conn.fetchrow(
        f"SELECT result,completed_at FROM {schema}.sessions WHERE id=$1 FOR UPDATE",
        binding.session,
    )
    if (
        row is None
        or UUID(str(row["session_id"])) != binding.session
        or session is None
        or session["completed_at"] is None
        or session["result"] != row["content"]
    ):
        raise PolicyUnavailableError("Native context episode persisted body differs")
    await conn.execute(
        f"INSERT INTO {schema}.location_runtime_context_episodes "
        "(input_generation,episode_id,body_digest) VALUES($1,$2,$3)",
        binding.generation,
        episode,
        episode_body_digest(row),
    )


async def finish_context_server(runtime: Any, generation: UUID, request: UUID) -> None:
    receipt = uuid4()
    async with runtime.domain.acquire() as conn:
        async with conn.transaction():
            await runtime.lock_domain(conn)
            if (
                await conn.fetchval(
                    "SELECT server_request FROM location_runtime_context_intents "
                    "WHERE input_generation=$1",
                    generation,
                )
                != request
            ):
                raise PolicyUnavailableError("Native context server lifetime differs")
            await conn.execute(
                "INSERT INTO location_runtime_context_server_finished "
                "(input_generation,server_request,receipt_id) VALUES($1,$2,$3)",
                generation,
                request,
                receipt,
            )
    if (
        await runtime.domain.fetchval(
            "SELECT receipt_id FROM location_runtime_context_server_finished "
            "WHERE input_generation=$1",
            generation,
        )
        != receipt
    ):
        raise PolicyUnavailableError("Committed context server completion is unknown")


async def dispose_runtime_context(runtime: Any, input_generation: UUID, plan: dict) -> bool:
    """Own exact unchanged closed input, session and native episodes atomically.

    Independent/mixed context, missing runtime/server finalizers, other live
    inputs, changed bodies and actual downstream derivations remain intact.
    The source owner's plan selects; it never proxies this receiver's receipt.
    """
    from butlers.chronicler.location_memory_copies import episode_body_digest

    declared = {
        UUID(loan["loan_id"]): loan
        for loan in plan.get("catalog_loans", ())
        if loan["receiver_name"] == runtime.name and loan.get("complete_input") is True
    }
    schema = _own_schema(runtime)
    receipt = uuid4()
    completed_loans = []
    # The actual configured owning Memory connection can dispose its own
    # episode and own domain session/receipt in ONE transaction. No peer SQL.
    async with runtime.memory.acquire() as conn:
        async with conn.transaction():
            binding = _RuntimeContext(runtime, input_generation, UUID(int=0), False)
            await _lock_memory_context(conn, binding)
            frozen = await conn.fetchrow(
                f"SELECT b.*,i.server_request,e.receipt_id AS ended_receipt "
                f"FROM {schema}.location_runtime_context_bindings b "
                f"JOIN {schema}.location_runtime_context_intents i USING(input_generation) "
                f"LEFT JOIN {schema}.location_runtime_context_ended e USING(input_generation) "
                "WHERE b.input_generation=$1",
                input_generation,
            )
            if (
                frozen is None
                or frozen["exclusive_input"] is not True
                or frozen["ended_receipt"] is None
            ):
                return False
            prior = await conn.fetchval(
                f"SELECT receipt_id FROM {schema}.location_runtime_context_dispositions "
                "WHERE input_generation=$1 AND decision_id=$2 AND manifest_digest=$3",
                input_generation,
                UUID(plan["decision_id"]),
                bytes.fromhex(plan["manifest_digest"]),
            )
            if prior is not None:
                return True
            if frozen["server_request"] is not None and not await conn.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM {schema}.location_runtime_context_server_finished "
                "WHERE input_generation=$1 AND server_request=$2)",
                input_generation,
                frozen["server_request"],
            ):
                return False
            loans = await conn.fetch(
                f"SELECT l.* FROM {schema}.location_catalog_copy_lifetimes h "
                f"JOIN {schema}.location_catalog_copy_loans l USING(loan_id,body_digest) "
                "WHERE h.holder_id=$1 AND h.holder_kind='unbound_processing' ORDER BY l.loan_id",
                input_generation,
            )
            if any(
                loan["loan_id"] not in declared
                or declared[loan["loan_id"]]["source_generation"] != str(loan["source_generation"])
                or declared[loan["loan_id"]]["body_digest"] != loan["body_digest"].hex()
                or declared[loan["loan_id"]]["receiving_incarnation"]
                != str(loan["receiving_incarnation"])
                for loan in loans
            ):
                return False
            context_loans = list(loans)
            tool_witnesses = await conn.fetch(
                f"SELECT t.*,r.outcome,r.result_digest,r.exclusive_inputs "
                f"FROM {schema}.location_runtime_tool_intents t "
                f"LEFT JOIN {schema}.location_runtime_tool_results r USING(tool_generation) "
                "WHERE t.receiving_session=$1 ORDER BY t.tool_generation",
                frozen["receiving_session"],
            )
            tool_loans = await conn.fetch(
                f"SELECT l.* FROM {schema}.location_runtime_tool_intents t "
                f"JOIN {schema}.location_runtime_tool_inputs i USING(tool_generation) "
                f"JOIN {schema}.location_catalog_copy_lifetimes h "
                "ON h.loan_id=i.loan_id AND h.body_digest=i.body_digest "
                "AND h.holder_id=i.tool_generation AND h.holder_kind='unbound_processing' "
                f"JOIN {schema}.location_catalog_copy_loans l USING(loan_id,body_digest) "
                "WHERE t.receiving_session=$1 ORDER BY l.loan_id",
                frozen["receiving_session"],
            )
            if any(
                loan["loan_id"] not in declared
                or declared[loan["loan_id"]]["source_generation"] != str(loan["source_generation"])
                or declared[loan["loan_id"]]["body_digest"] != loan["body_digest"].hex()
                or declared[loan["loan_id"]]["receiving_incarnation"]
                != str(loan["receiving_incarnation"])
                for loan in tool_loans
            ):
                return False
            loans = context_loans + list(tool_loans)
            expected_bundle = content_digest(
                {
                    "loans": [
                        [str(loan["loan_id"]), loan["body_digest"].hex()] for loan in context_loans
                    ],
                    "context": frozen["context_digest"].hex(),
                    "system": frozen["system_digest"].hex(),
                    "prompt": frozen["prompt_digest"].hex(),
                }
            )
            if expected_bundle != frozen["bundle_digest"]:
                raise PolicyUnavailableError("Native runtime full input bundle changed")
            session = await conn.fetchrow(
                f"SELECT * FROM {schema}.sessions WHERE id=$1 FOR UPDATE",
                frozen["receiving_session"],
            )
            if (
                session is None
                or session["completed_at"] is None
                or hashlib.sha256(session["prompt"].encode()).digest() != frozen["prompt_digest"]
                or hashlib.sha256(session["effective_system_prompt"].encode()).digest()
                != frozen["system_digest"]
            ):
                return False
            # A native generated prompt has real own-domain parent births.
            # Its entire prompt ancestry must be selected, including newer
            # local inputs; a catalog loan alone cannot authorize that prompt.
            if runtime.name != "chronicler" or await conn.fetchval(
                "SELECT NOT EXISTS(SELECT 1 FROM chronicler.location_native_copy_births "
                "WHERE receiving_session=$1) OR EXISTS(SELECT 1 FROM "
                "chronicler.location_native_copy_births b WHERE b.receiving_session=$1 "
                "AND (NOT b.lineage_known OR NOT b.exclusive_input OR NOT EXISTS(SELECT 1 "
                "FROM chronicler.location_retention_plan_outputs o WHERE o.decision_id=$2 "
                "AND o.output_kind=b.output_kind AND o.output_id=b.output_id)))",
                frozen["receiving_session"],
                UUID(plan["decision_id"]),
            ):
                return False
            encoded_system = session["effective_system_prompt"].encode()
            size = frozen["context_bytes"]
            if size:
                if (
                    len(encoded_system) < size + 2
                    or encoded_system[-size - 2 : -size] != b"\n\n"
                    or hashlib.sha256(encoded_system[-size:]).digest() != frozen["context_digest"]
                ):
                    raise PolicyUnavailableError("Native composed context suffix differs")
                reduced_system = encoded_system[: -size - 2].decode()
            else:
                reduced_system = session["effective_system_prompt"]
            preserved_provenance = [
                entry
                for entry in session["prompt_provenance"] or []
                if entry.get("source") != "memory_context"
            ]
            episodes = await conn.fetch(
                f"SELECT episode_id,body_digest FROM {schema}.location_runtime_context_episodes "
                "WHERE input_generation=$1 ORDER BY episode_id",
                input_generation,
            )
            for expected in episodes:
                row = await conn.fetchrow(
                    "SELECT * FROM episodes WHERE id=$1 FOR UPDATE", expected["episode_id"]
                )
                if (
                    row is None
                    or episode_body_digest(row) != expected["body_digest"]
                    or row["leased_until"] is not None
                    or row["leased_by"] is not None
                    or row["consolidation_status"] not in {"pending", "failed", "dead_letter"}
                ):
                    return False
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM facts WHERE source_episode_id=$1) "
                    "OR EXISTS(SELECT 1 FROM rules WHERE source_episode_id=$1) "
                    "OR EXISTS(SELECT 1 FROM memory_links WHERE "
                    "source_id=$1 OR target_id=$1)",
                    expected["episode_id"],
                ):
                    return False
            # These are actual newly inserted outputs captured by the owning
            # writer before COMMIT. A graph/catalog/other-context descendant
            # requires its own closure; absence is checked on the live writer,
            # never inferred from a model's provenance or a missing callback.
            artifacts = await conn.fetch(
                f"SELECT * FROM {schema}.location_runtime_context_artifacts "
                "WHERE input_generation=$1 ORDER BY memory_table,artifact_id",
                input_generation,
            )
            from butlers.chronicler.location_tool_copies import matched_tool_records

            if tool_witnesses and not matched_tool_records(session["tool_calls"], tool_witnesses):
                return False
            mutation_inputs = []
            if runtime.name == "chronicler":
                mutation_inputs = await conn.fetch(
                    "SELECT i.*,a.body_digest AS original_digest "
                    "FROM chronicler.location_native_memory_mutation_inputs i "
                    "JOIN chronicler.location_native_memory_artifacts a USING(artifact_generation) "
                    "JOIN chronicler.location_runtime_tool_intents t USING(tool_generation) "
                    "WHERE t.receiving_session=$1 ORDER BY i.tool_generation,i.artifact_generation",
                    frozen["receiving_session"],
                )
                for item in mutation_inputs:
                    if item["lifecycle_only"] is not True or not await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM "
                        "chronicler.location_native_memory_artifact_dispositions "
                        "WHERE artifact_generation=$1 AND body_digest=$2 AND decision_id=$3)",
                        item["artifact_generation"],
                        item["original_digest"],
                        UUID(plan["decision_id"]),
                    ):
                        return False
                    # Every actual selected local parent must be a closed,
                    # current-plan copy; another artifact's receipt cannot
                    # substitute for this mutation's own input generation.
                    if (
                        await conn.fetchval(
                            "SELECT count(*)=$5 AND bool_and(b.input_digest=$2 "
                            "AND b.receiving_session=$3 AND b.lineage_known AND b.exclusive_input "
                            "AND EXISTS(SELECT 1 FROM chronicler.location_retention_plan_outputs p "
                            "WHERE p.decision_id=$4 AND p.output_kind=b.output_kind "
                            "AND p.output_id=b.output_id)) "
                            "FROM chronicler.location_native_copy_births b "
                            "WHERE b.copy_generation=$1",
                            item["input_generation"],
                            item["before_digest"],
                            frozen["receiving_session"],
                            UUID(plan["decision_id"]),
                            item["parent_count"],
                        )
                        is not True
                    ):
                        return False
            # Only exact source ledger dispositions can select native core
            # question tool copies. A ledger status or another tool's receipt
            # cannot authorize erasing these original input/result records.
            closed_questions = await conn.fetch(
                f"SELECT q.tool_generation FROM {schema}.location_native_delegation_inputs q "
                f"JOIN {schema}.location_native_delegation_dispositions d "
                "USING(question_generation,body_digest) "
                "WHERE q.context_generation=$1 AND d.decision_id=$2 AND d.manifest_digest=$3 "
                "AND NOT EXISTS(SELECT 1 FROM "
                f"{schema}.location_native_delegation_inputs sibling "
                "WHERE sibling.tool_generation=q.tool_generation AND NOT EXISTS(SELECT 1 "
                f"FROM {schema}.location_native_delegation_dispositions sd "
                "WHERE sd.question_generation=sibling.question_generation "
                "AND sd.body_digest=sibling.body_digest AND sd.decision_id=$2 "
                "AND sd.manifest_digest=$3))",
                input_generation,
                UUID(plan["decision_id"]),
                bytes.fromhex(plan["manifest_digest"]),
            )
            if not captured_artifact_calls(
                session["tool_calls"],
                artifacts,
                tool_witnesses,
                mutation_inputs,
                closed_questions,
            ):
                return False  # Unknown/routed mutations retain their input and copy holders.
            already_disposed = set()
            for artifact in artifacts:
                table, identifier = artifact["memory_table"], artifact["artifact_id"]
                if table not in {"facts", "rules"}:
                    raise PolicyUnavailableError("Native context artifact profile differs")
                canonical = await conn.fetchrow(
                    f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", identifier
                )
                from butlers.chronicler.location_memory_mutations import (
                    current_artifact_body_matches,
                )

                if (
                    canonical is None
                    and runtime.name == "chronicler"
                    and await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_memory_artifacts a "
                        "JOIN chronicler.location_native_memory_artifact_dispositions d "
                        "USING(artifact_generation,body_digest) WHERE a.artifact_generation=$1 "
                        "AND EXISTS(SELECT 1 FROM chronicler.location_runtime_context_artifacts c "
                        "WHERE c.artifact_generation=a.artifact_generation "
                        "AND c.input_generation=$2) "
                        "AND d.decision_id=$3 AND a.body_digest=$4)",
                        artifact["artifact_generation"],
                        input_generation,
                        UUID(plan["decision_id"]),
                        artifact["body_digest"],
                    )
                ):
                    already_disposed.add(artifact["artifact_generation"])
                    continue
                from butlers.chronicler.location_memory_copies import artifact_body_matches

                # Other receivers use only their own frozen local witness;
                # this is never permission to query Chronicle's private chain.
                body_matches = (
                    await current_artifact_body_matches(conn, canonical, artifact)
                    if runtime.name == "chronicler"
                    else artifact_body_matches(canonical, artifact)
                )
                if not body_matches or await conn.fetchval(
                    f"SELECT EXISTS(SELECT 1 FROM {schema}.location_runtime_context_artifacts "
                    "WHERE memory_table=$1 AND artifact_id=$2 AND input_generation<>$3) "
                    "OR EXISTS(SELECT 1 FROM public.memory_catalog WHERE source_schema=$4 "
                    "AND source_table=$1 AND source_id=$2) "
                    "OR EXISTS(SELECT 1 FROM memory_links WHERE source_id=$2 OR target_id=$2) "
                    + (
                        "OR EXISTS(SELECT 1 FROM facts WHERE supersedes_id=$2)"
                        if table == "facts"
                        else ""
                    ),
                    table,
                    identifier,
                    input_generation,
                    runtime.memory_identity[0],
                ):
                    return False
            for artifact in artifacts:
                if artifact.get("artifact_generation") in already_disposed:
                    continue
                if artifact["memory_table"] == "facts":
                    from butlers.core import entity_graph_edges

                    await entity_graph_edges.delete_entity_graph_edge(
                        conn,
                        source_schema=runtime.memory_identity[0],
                        source_table="facts",
                        source_id=artifact["artifact_id"],
                    )
                removed = await conn.fetchval(
                    f"DELETE FROM {artifact['memory_table']} WHERE id=$1 RETURNING id",
                    artifact["artifact_id"],
                )
                if removed != artifact["artifact_id"]:
                    raise PolicyUnavailableError("Native context artifact disposal changed")
            for expected in episodes:
                if (
                    await conn.fetchval(
                        "DELETE FROM episodes WHERE id=$1 RETURNING id", expected["episode_id"]
                    )
                    != expected["episode_id"]
                ):
                    raise PolicyUnavailableError("Native context episode disposal changed")
            await conn.execute(
                f"UPDATE {schema}.session_process_logs SET command='[Location input forgotten]',"
                "stderr=NULL WHERE session_id=$1",
                frozen["receiving_session"],
            )
            await conn.execute(
                f"UPDATE {schema}.sessions SET prompt='[Location input forgotten]',"
                "effective_system_prompt=$3,"
                "prompt_digest=$2,prompt_provenance=$4,"
                "result='[Location output forgotten]',"
                "tool_calls='[]'::jsonb,error=NULL WHERE id=$1",
                frozen["receiving_session"],
                hashlib.sha256(reduced_system.encode()).hexdigest(),
                reduced_system,
                preserved_provenance,
            )
            for loan in loans:
                completed_loans.append(loan["loan_id"])
                await conn.execute(
                    f"INSERT INTO {schema}.location_catalog_copy_finished "
                    "(loan_id,body_digest,receipt_id) "
                    "VALUES($1,$2,$3)",
                    loan["loan_id"],
                    loan["body_digest"],
                    uuid4(),
                )
            await conn.execute(
                f"INSERT INTO {schema}.location_runtime_context_dispositions "
                "(input_generation,decision_id,manifest_digest,receipt_id) VALUES($1,$2,$3,$4)",
                input_generation,
                UUID(plan["decision_id"]),
                bytes.fromhex(plan["manifest_digest"]),
                receipt,
            )
    async with runtime.domain.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT receipt_id FROM location_runtime_context_dispositions "
            "WHERE input_generation=$1",
            input_generation,
        )
        loan_count = await committed.fetchval(
            "SELECT count(*) FROM location_catalog_copy_finished WHERE loan_id=ANY($1::uuid[])",
            completed_loans,
        )
    async with runtime.memory.acquire() as committed_memory:
        for artifact in artifacts:
            if await committed_memory.fetchval(
                f"SELECT EXISTS(SELECT 1 FROM {artifact['memory_table']} WHERE id=$1)",
                artifact["artifact_id"],
            ):
                raise PolicyUnavailableError("Committed context artifact disposal is unknown")
    if observed != receipt or loan_count != len(completed_loans):
        raise PolicyUnavailableError("Committed native context disposal is unknown")
    return True


def captured_artifact_calls(
    calls: Any,
    artifacts: list[Any],
    witnesses: list[Any] = (),
    mutation_inputs: list[Any] = (),
    closed_questions: list[Any] = (),
) -> bool:
    """Recorded outputs can select only SAME-writer captured artifact IDs.

    This admits no authority or source lineage from the record. Every selected
    artifact is still checked against the private immutable writer ledger and
    canonical full body, and graph/catalog/other-context copies must close.
    Unknown operations/outcomes keep the complete source context held.
    """
    if calls is None:
        return True
    if not isinstance(calls, list):
        return False
    owned = {(row["memory_table"], str(row["artifact_id"])) for row in artifacts}
    names = {"memory_store_fact": "facts", "memory_store_rule": "rules"}
    from butlers.chronicler.location_memory_mutations import _MUTATION_TOOLS
    from butlers.chronicler.location_tool_copies import (
        NATIVE_MEMORY_READ_TOOLS,
        matched_tool_records,
    )

    if any(
        isinstance(call, dict)
        and call.get("name") in (NATIVE_MEMORY_READ_TOOLS | _MUTATION_TOOLS | {"delegate_ask"})
        for call in calls
    ):
        try:
            if not matched_tool_records(calls, witnesses):
                return False
        except (KeyError, TypeError, ValueError, AttributeError):
            return False  # Incomplete witnesses never supply disposal authority.
    for call in calls:
        if not isinstance(call, dict) or call.get("outcome") != "success":
            return False
        if call.get("name") == "delegate_ask":
            applicable = [row for row in witnesses if row["tool_name"] == "delegate_ask"]
            if not applicable or any(
                row["module_name"] != "core"
                or row["outcome"] != "success"
                or row["exclusive_inputs"] is not True
                or not any(q["tool_generation"] == row["tool_generation"] for q in closed_questions)
                for row in applicable
            ):
                return False
            continue
        if call.get("name") in NATIVE_MEMORY_READ_TOOLS:
            applicable = [row for row in witnesses if row["tool_name"] == call["name"]]
            if not applicable or any(
                row["module_name"] != "memory"
                or row["exclusive_inputs"] is not True
                or row["outcome"] != "success"
                for row in applicable
            ):
                return False
            # The caller also requires full one-to-one input/result matching.
            # One exclusive call cannot bless another same-name mixed call.
            continue
        if call.get("name") in _MUTATION_TOOLS:
            applicable = [row for row in witnesses if row["tool_name"] == call["name"]]
            if not applicable or any(
                row["module_name"] != "memory"
                or row["outcome"] != "success"
                or row["exclusive_inputs"] is not True
                or not any(
                    item["tool_generation"] == row["tool_generation"]
                    and item["lifecycle_only"] is True
                    for item in mutation_inputs
                )
                for row in applicable
            ):
                return False
            # The owning caller separately verifies every applicable input
            # and exact artifact disposition, not just these record selectors.
            continue
        result = call.get("result")
        table = names.get(call.get("name"))
        if not isinstance(result, dict) or (table, str(result.get("id"))) not in owned:
            return False
    return True


_context_writers: dict[Any, Any] = {}


def register_context_writer(runtime: Any) -> None:
    """Fixed Memory lifecycle retains late-write fences for its actual live pool."""
    for pool in tuple(_context_writers):
        if pool.is_closing():
            _context_writers.pop(pool)
    _context_writers[runtime.domain] = runtime


def context_writer(pool: Any):
    return _context_writers.get(pool)


async def context_session_forgotten(pool: Any, conn: Any, session: UUID) -> bool:
    runtime = context_writer(pool)
    if runtime is None:
        return False
    await runtime.lock_domain(conn)
    return (
        await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_runtime_context_dispositions d "
            "JOIN location_runtime_context_bindings b USING(input_generation) "
            "WHERE b.receiving_session=$1)",
            session,
        )
        is True
    )


async def dispose_own_contexts(domain: Any, decision: UUID) -> None:
    """Scheduled own producer: only Chronicler's actual configured Memory runtime.

    The stored plan selects its complete native cohort. A receiver loan is an
    additional parent, never required to fabricate a local native parent.
    """
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_retention import plan_status

    runtime = next(
        (
            r
            for r in _runtimes.values()
            if r.domain is domain and r.name == "chronicler" and r.active
        ),
        None,
    )
    if runtime is None:
        return
    plan = await plan_status(domain, decision)
    generations = await domain.fetch(
        "SELECT DISTINCT i.input_generation FROM location_runtime_context_intents i "
        "JOIN location_native_copy_births b USING(receiving_session) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "WHERE p.decision_id=$1 ORDER BY i.input_generation",
        decision,
    )
    for row in generations:
        await dispose_runtime_context(runtime, row["input_generation"], plan)


@dataclass
class _ArtifactWriter:
    runtime: Any
    generation: UUID
    connection: Any
    pending: set[tuple[str, UUID]] = field(default_factory=set)


_artifact_writer: ContextVar[_ArtifactWriter | None] = ContextVar(
    "native_context_artifact_writer", default=None
)


async def context_artifact_scope(pool: Any, conn: Any) -> _ArtifactWriter | None:
    """Resolve actual registered guard invocation or native constructor scope.

    A model's session/provenance/actor argument is never consulted. Stored
    session IDs only locate an already committed constructor-owned context.
    """
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.core.copy_lifetime import _current_copy_invocation

    runtime = _runtimes.get(pool)
    if runtime is None or not runtime.active:
        return None
    native = current_runtime_context()
    invocation = _current_copy_invocation.get()
    if native is not None and native.runtime is runtime and native.admitted:
        session = native.session
    elif invocation is not None:
        from butlers.chronicler.location_tool_copies import registered_copy_invocation

        invocation = registered_copy_invocation(runtime.name)
        session = UUID(invocation.runtime_session)
    else:
        return None
    provisional = _RuntimeContext(runtime, UUID(int=0), session, False)
    await _lock_memory_context(conn, provisional)
    schema = _own_schema(runtime)
    row = await conn.fetchrow(
        f"SELECT b.input_generation FROM {schema}.location_runtime_context_bindings b "
        "WHERE b.receiving_session=$1 AND (EXISTS("
        f"SELECT 1 FROM {schema}.location_catalog_copy_lifetimes l "
        "WHERE (l.holder_id=b.input_generation AND l.holder_kind='unbound_processing') "
        "OR (l.holder_id=b.receiving_session AND l.holder_kind='runtime_session')) "
        + (
            "OR EXISTS(SELECT 1 FROM chronicler.location_native_copy_births n "
            "WHERE n.receiving_session=b.receiving_session)"
            if runtime.name == "chronicler"
            else ""
        )
        + ")",
        session,
    )
    if row is None:
        return None  # No fabricated source ancestry for ordinary writes.
    generation = row["input_generation"]
    if await conn.fetchval(
        f"SELECT EXISTS(SELECT 1 FROM {schema}.location_runtime_context_dispositions "
        "WHERE input_generation=$1)",
        generation,
    ):
        raise PolicyUnavailableError("Native context artifact input was disposed")
    if runtime.name == "chronicler" and await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_births b "
        "JOIN chronicler.location_retention_plan_outputs o USING(output_kind,output_id) "
        "WHERE b.receiving_session=$1)",
        session,
    ):
        raise PolicyUnavailableError("Native context artifact source is prepared")
    return _ArtifactWriter(runtime, generation, conn)


async def bind_context_artifact(conn: Any, table: str, artifact: UUID) -> None:
    binding = _artifact_writer.get()
    if binding is None:
        return
    if binding.connection is not conn or table not in {"facts", "rules"}:
        raise PolicyUnavailableError("Native context artifact writer differs")
    binding.pending.add((table, artifact))


async def finish_context_artifacts(binding: _ArtifactWriter) -> None:
    from butlers.chronicler.location_memory_copies import artifact_content_digest
    from butlers.chronicler.location_projection import _digest_value

    schema = _own_schema(binding.runtime)
    for table, artifact in sorted(binding.pending):
        row = await binding.connection.fetchrow(
            f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", artifact
        )
        if row is None:
            raise PolicyUnavailableError("Native context artifact body is unavailable")
        artifact_generation = uuid4()
        digest = content_digest({"memory_artifact": _digest_value(dict(row))})
        await binding.connection.execute(
            f"INSERT INTO {schema}.location_runtime_context_artifacts "
            "(artifact_generation,input_generation,memory_table,artifact_id,"
            "body_digest,content_digest) "
            "VALUES($1,$2,$3,$4,$5,$6)",
            artifact_generation,
            binding.generation,
            table,
            artifact,
            digest,
            artifact_content_digest(table, row),
        )
        captured = await capture_context_catalog_source(
            binding, artifact_generation, table, artifact, digest
        )
        catalog = await binding.connection.fetchrow(
            "SELECT source_schema FROM public.memory_catalog WHERE source_schema=$1 "
            "AND source_table=$2 AND source_id=$3",
            binding.runtime.memory_identity[0],
            table,
            artifact,
        )
        if catalog is not None:
            if not captured:
                raise PolicyUnavailableError("Native context catalog full ancestry is unavailable")
            from butlers.chronicler.location_catalog_copies import bind_catalog

            await bind_catalog(
                binding.connection,
                binding.runtime.memory,
                catalog["source_schema"],
                table,
                artifact,
            )


async def capture_context_catalog_source(
    binding: _ArtifactWriter, generation: UUID, table: str, artifact: UUID, digest: bytes
) -> bool:
    """Actual exclusive Chronicler input can use its existing owning catalog plane.

    The full native context and every actual parent are reread on the same
    writer. Borrowed/mixed/unknown inputs remain in their own artifact ledger;
    they cannot borrow Chronicle's source authority or fabricate a catalog
    generation. Other receivers never read Chronicle's private namespace.
    """
    if binding.runtime.name != "chronicler":
        return False
    conn = binding.connection
    from butlers.chronicler.location_memory_copies import artifact_content_digest
    from butlers.chronicler.location_projection import _digest_value

    if table not in {"facts", "rules"}:
        raise PolicyUnavailableError("Native source artifact profile differs")
    canonical = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", artifact)
    if (
        canonical is None
        or content_digest({"memory_artifact": _digest_value(dict(canonical))}) != digest
    ):
        raise PolicyUnavailableError("Native source artifact body differs")
    frozen = await conn.fetchrow(
        "SELECT b.*,i.server_request FROM chronicler.location_runtime_context_bindings b "
        "JOIN chronicler.location_runtime_context_intents i USING(input_generation) "
        "WHERE b.input_generation=$1",
        binding.generation,
    )
    if frozen is None or frozen["exclusive_input"] is not True:
        return False
    from butlers.chronicler.location_memory_mutations import _MUTATION_TOOLS
    from butlers.chronicler.location_tool_copies import NATIVE_MEMORY_READ_TOOLS, current_tool_copy

    current_tool = current_tool_copy(binding.runtime)
    tools = await conn.fetch(
        "SELECT t.*,r.outcome,r.result_digest,r.exclusive_inputs "
        "FROM chronicler.location_runtime_tool_intents t "
        "LEFT JOIN chronicler.location_runtime_tool_results r USING(tool_generation) "
        "WHERE t.receiving_session=$1 ORDER BY t.tool_generation",
        frozen["receiving_session"],
    )
    for tool in tools:
        active_write = (
            current_tool is not None
            and tool["tool_generation"] == current_tool.generation
            and current_tool.session == frozen["receiving_session"]
        )
        if (
            tool["module_name"] != "memory"
            or tool["tool_name"]
            not in (
                {"memory_store_fact", "memory_store_rule"}
                | NATIVE_MEMORY_READ_TOOLS
                | _MUTATION_TOOLS
            )
            or (not active_write and tool["outcome"] != "success")
            or (
                tool["tool_name"] in (NATIVE_MEMORY_READ_TOOLS | _MUTATION_TOOLS | {"delegate_ask"})
                and tool["exclusive_inputs"] is not True
            )
        ):
            return False
        if tool["tool_name"] in _MUTATION_TOOLS:
            # A later output inherits the mutation's own full selected input,
            # not the mutation tool name or an unrelated earlier read's loan.
            selected_inputs = await conn.fetch(
                "SELECT * FROM chronicler.location_native_memory_mutation_inputs "
                "WHERE tool_generation=$1 ORDER BY artifact_generation",
                tool["tool_generation"],
            )
            if not selected_inputs:
                return False
            for selected in selected_inputs:
                if (
                    selected["lifecycle_only"] is not True
                    or await conn.fetchval(
                        "SELECT count(*)=$4 AND bool_and(input_digest=$2 "
                        "AND receiving_session=$3 AND lineage_known AND exclusive_input) "
                        "FROM chronicler.location_native_copy_births WHERE copy_generation=$1",
                        selected["input_generation"],
                        selected["before_digest"],
                        frozen["receiving_session"],
                        selected["parent_count"],
                    )
                    is not True
                ):
                    return False
    loans = await conn.fetch(
        "SELECT l.* FROM chronicler.location_catalog_copy_loans l "
        "JOIN chronicler.location_catalog_copy_lifetimes h USING(loan_id,body_digest) "
        "WHERE (h.holder_id=$1 AND h.holder_kind='unbound_processing') OR EXISTS("
        "SELECT 1 FROM chronicler.location_runtime_tool_intents t "
        "JOIN chronicler.location_runtime_tool_inputs i USING(tool_generation) "
        "WHERE t.receiving_session=$2 AND i.loan_id=l.loan_id "
        "AND i.body_digest=l.body_digest AND h.holder_id=t.tool_generation "
        "AND h.holder_kind='unbound_processing') ORDER BY l.loan_id",
        binding.generation,
        frozen["receiving_session"],
    )
    loan_parents = []
    for loan in loans:
        # This is Chronicle's OWN source history through its existing configured
        # Memory bridge. Other receivers never read this namespace. A loan from
        # any other source must use that source's actual owning protocol.
        selected = await conn.fetch(
            "SELECT p.copy_generation,p.input_digest,i.parent_count,b.output_id,"
            "b.input_digest AS birth_digest,b.lineage_known,b.exclusive_input "
            "FROM chronicler.location_native_catalog_generations g "
            "JOIN chronicler.location_native_memory_artifacts a USING(artifact_generation) "
            "JOIN chronicler.location_native_dispatch_inputs i USING(input_generation) "
            "LEFT JOIN chronicler.location_native_dispatch_parents p USING(input_generation) "
            "LEFT JOIN chronicler.location_native_copy_births b "
            "ON b.copy_generation=p.copy_generation "
            "WHERE g.source_generation=$1 AND g.body_digest=$2",
            loan["source_generation"],
            loan["body_digest"],
        )
        if not selected:
            return False
        from butlers.chronicler.location_memory_ancestry import require_complete_parents

        require_complete_parents(selected)
        loan_parents.extend(selected)
    parents = await conn.fetch(
        "SELECT DISTINCT copy_generation,input_digest,lineage_known,exclusive_input "
        "FROM chronicler.location_native_copy_births WHERE receiving_session=$1",
        frozen["receiving_session"],
    )
    parents = list(
        {
            (row["copy_generation"], row["input_digest"]): row for row in [*parents, *loan_parents]
        }.values()
    )
    if not parents or any(
        row["lineage_known"] is not True or row["exclusive_input"] is not True for row in parents
    ):
        return False
    lineage_generation = generation
    bundle_digest = content_digest(
        {
            "context": frozen["bundle_digest"].hex(),
            "tools": [
                {
                    "generation": str(row["tool_generation"]),
                    "input": row["input_digest"].hex(),
                    "result": row["result_digest"].hex() if row["result_digest"] else None,
                    "name": row["tool_name"],
                    "module": row["module_name"],
                }
                for row in tools
            ],
            "loans": [[str(row["loan_id"]), row["body_digest"].hex()] for row in loans],
            "parents": sorted(
                [[str(row["copy_generation"]), row["input_digest"].hex()] for row in parents]
            ),
        }
    )
    prior = await conn.fetchrow(
        "SELECT * FROM chronicler.location_native_memory_bundles WHERE input_generation=$1",
        lineage_generation,
    )
    if prior is None:
        await conn.execute(
            "INSERT INTO chronicler.location_native_dispatch_inputs "
            "(input_generation,server_request,prompt_digest,parent_count,origin_kind) "
            "VALUES($1,$2,$3,$4,'native_memory')",
            lineage_generation,
            frozen["server_request"] or lineage_generation,
            frozen["prompt_digest"],
            len(parents),
        )
        for parent in parents:
            await conn.execute(
                "INSERT INTO chronicler.location_native_dispatch_parents "
                "(input_generation,copy_generation,input_digest) VALUES($1,$2,$3)",
                lineage_generation,
                parent["copy_generation"],
                parent["input_digest"],
            )
        await conn.execute(
            "INSERT INTO chronicler.location_native_memory_bundles "
            "(input_generation,bundle_digest,exclusive_input) VALUES($1,$2,true)",
            lineage_generation,
            bundle_digest,
        )
    elif prior["bundle_digest"] != bundle_digest or prior["exclusive_input"] is not True:
        raise PolicyUnavailableError("Native context catalog bundle differs")
    await conn.execute(
        "INSERT INTO chronicler.location_native_memory_artifacts "
        "(artifact_generation,input_generation,memory_table,artifact_id,"
        "body_digest,content_digest) "
        "VALUES($1,$2,$3,$4,$5,$6)",
        generation,
        lineage_generation,
        table,
        artifact,
        digest,
        artifact_content_digest(table, canonical),
    )
    return True
