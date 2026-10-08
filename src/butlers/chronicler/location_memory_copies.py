"""Native Memory session-episode producer and fixed owning disposal.

The registered module supplies its real domain and configured memory pools.
No model session UUID, schema, response or callback supplies that authority.
A reservation precedes embedding; body insertion and immutable binding share
its actual Memory writer transaction. Missing descendants/leases stay held.
"""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_retention import PolicyUnavailableError, native_copy_pool
from butlers.location_retention import ADAPTER_NAMES, content_digest


@dataclass
class _MemoryCopy:
    pool: Any
    reservation: UUID
    session: UUID
    digest: bytes
    schema: str
    role: str
    active: bool = True
    episode: UUID | None = None


_current_memory_copy: ContextVar[_MemoryCopy | None] = ContextVar(
    "native_location_memory_copy", default=None
)
_receivers: dict[Any, tuple[Any, str, str]] = {}


# Only these existing operational fields vary without changing the stored
# source-derived content. Every metadata/vector/authority/body field remains
# bound. State/lease/descendant closure is checked independently at disposal.
_EPISODE_LIFECYCLE_FIELDS = frozenset(
    {
        "reference_count",
        "last_referenced_at",
        "expires_at",
        "consolidated",
        "consolidation_status",
        "consolidation_attempts",
        "leased_until",
        "leased_by",
        "next_consolidation_retry_at",
    }
)


def episode_body_digest(row: Any) -> bytes:
    from butlers.chronicler.location_projection import _digest_value

    return content_digest(
        {
            "memory_body": _digest_value(
                {
                    key: value
                    for key, value in dict(row).items()
                    if key not in _EPISODE_LIFECYCLE_FIELDS
                }
            )
        }
    )


def artifact_content_digest(table: str, row: Any) -> bytes:
    """Versioned producer content witness; only read-reference metadata varies."""
    from butlers.chronicler.location_projection import _digest_value

    if table not in {"facts", "rules"}:
        raise PolicyUnavailableError("Native artifact content profile differs")
    return content_digest(
        {
            "profile": "native_memory_artifact_content.v1",
            "table": table,
            "body": _digest_value(
                {
                    key: value
                    for key, value in dict(row).items()
                    if key not in {"reference_count", "last_referenced_at"}
                }
            ),
        }
    )


def artifact_body_matches(row: Any, witness: Any) -> bool:
    """Frozen full body or SAME-writer optional content witness; no backfill."""
    from butlers.chronicler.location_projection import _digest_value

    if row is None:
        return False
    if content_digest({"memory_artifact": _digest_value(dict(row))}) == witness["body_digest"]:
        return True
    frozen = witness.get("content_digest")
    return (
        isinstance(frozen, bytes)
        and len(frozen) == 32
        and artifact_content_digest(witness["memory_table"], row) == frozen
    )


def unregister_memory_receiver(domain: Any, memory: Any) -> None:
    configured = _receivers.get(domain)
    if configured is not None and configured[0] is memory:
        del _receivers[domain]


async def _lock(conn: Any, schema: str, role: str) -> None:
    if (
        await conn.fetchval("SELECT current_schema()") != schema
        or await conn.fetchval("SELECT current_user") != role
    ):
        raise PolicyUnavailableError("Owning Memory writer identity differs")
    policy = await conn.fetchrow(
        "SELECT version FROM chronicler.location_retention_policy WHERE singleton FOR UPDATE"
    )
    if policy is None:
        raise PolicyUnavailableError("Owning Memory policy is unavailable")
    for name in ADAPTER_NAMES:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", name)


async def register_memory_receiver(domain: Any, memory: Any) -> None:
    """Called only by the owning Memory module's actual startup constructor."""
    import asyncpg

    if not isinstance(domain, asyncpg.Pool) or not isinstance(memory, asyncpg.Pool):
        return  # Unconfigured legacy doubles prove no installed runtime.
    async with memory.acquire() as conn:
        schema = await conn.fetchval("SELECT current_schema()")
        role = await conn.fetchval("SELECT current_user")
        memory_db = await conn.fetchval(
            "SELECT oid FROM pg_database WHERE datname=current_database()"
        )
    domain_schema = await domain.fetchval("SELECT current_schema()")
    domain_db = await domain.fetchval(
        "SELECT oid FROM pg_database WHERE datname=current_database()"
    )
    if domain_schema != "chronicler" or memory_db != domain_db:
        raise PolicyUnavailableError("Owning Memory startup database differs")
    if not isinstance(schema, str) or not isinstance(role, str):
        raise PolicyUnavailableError("Owning Memory startup identity is unavailable")
    previous = _receivers.get(domain)
    if previous is not None and previous[0] is not memory:
        if not isinstance(previous[0], asyncpg.Pool) or not previous[0].is_closing():
            raise PolicyUnavailableError("Owning Memory lifecycle is still active")
    _receivers[domain] = (memory, schema, role)


@asynccontextmanager
async def capture_memory_episode(domain: Any, memory: Any, session: Any, content: str):
    if not native_copy_pool(domain) or session is None:
        yield
        return
    session = UUID(str(session))
    # Only actual stored native receiving births qualify; the UUID is a locator.
    parents = await domain.fetch(
        "SELECT DISTINCT copy_generation,input_digest FROM location_native_copy_births "
        "WHERE receiving_session=$1 ORDER BY copy_generation",
        session,
    )
    if not parents:
        yield
        return
    if domain not in _receivers:
        async with memory.acquire() as conn:
            schema = await conn.fetchval("SELECT current_schema()")
            role = await conn.fetchval("SELECT current_user")
            memory_db = await conn.fetchval(
                "SELECT oid FROM pg_database WHERE datname=current_database()"
            )
        domain_db = await domain.fetchval(
            "SELECT oid FROM pg_database WHERE datname=current_database()"
        )
        if memory_db != domain_db or not isinstance(schema, str) or not isinstance(role, str):
            raise PolicyUnavailableError("Owning Memory database differs")
        _receivers[domain] = (memory, schema, role)
    owner, schema, role = _receivers[domain]
    if owner is not memory:
        raise PolicyUnavailableError("Owning Memory lifecycle differs")
    reservation = uuid4()
    digest = content_digest({"native_memory_content": content})
    async with memory.acquire() as conn:
        async with conn.transaction():
            await _lock(conn, schema, role)
            source_session = await conn.fetchrow(
                "SELECT result,completed_at,success FROM chronicler.sessions "
                "WHERE id=$1 FOR UPDATE",
                session,
            )
            if (
                source_session is None
                or source_session["completed_at"] is None
                or source_session["success"] is not True
                or source_session["result"] != content
            ):
                raise PolicyUnavailableError("Native Memory source output differs")
            parents = await conn.fetch(
                "SELECT DISTINCT copy_generation,input_digest "
                "FROM chronicler.location_native_copy_births "
                "WHERE receiving_session=$1 ORDER BY copy_generation",
                session,
            )
            if not parents:
                raise PolicyUnavailableError("Native Memory source generation differs")
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_dispositions "
                "WHERE receiving_session=$1)",
                session,
            ):
                raise PolicyUnavailableError("Native Memory input has been disposed")
            await conn.execute(
                "INSERT INTO chronicler.location_native_memory_reservations "
                "(reservation_id,receiving_session,content_digest,memory_schema,"
                "writer_role,parent_count) "
                "VALUES($1,$2,$3,$4,$5,$6)",
                reservation,
                session,
                digest,
                schema,
                role,
                len(parents),
            )
            for parent in parents:
                await conn.execute(
                    "INSERT INTO chronicler.location_native_memory_parents "
                    "(reservation_id,copy_generation,input_digest) VALUES($1,$2,$3)",
                    reservation,
                    parent["copy_generation"],
                    parent["input_digest"],
                )
    async with memory.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT count(*) FROM chronicler.location_native_memory_parents p "
            "JOIN chronicler.location_native_memory_reservations r "
            "USING(reservation_id) "
            "WHERE reservation_id=$1 AND r.content_digest=$2 AND r.receiving_session=$3",
            reservation,
            digest,
            session,
        )
    if observed != len(parents):
        raise PolicyUnavailableError("Committed Memory reservation is unknown")
    binding = _MemoryCopy(memory, reservation, session, digest, schema, role)
    token = _current_memory_copy.set(binding)
    try:
        yield
        if binding.episode is None:
            raise PolicyUnavailableError("Native Memory body commit is unknown")
        async with memory.acquire() as committed:
            observed = await committed.fetchval(
                "SELECT episode_id FROM chronicler.location_native_memory_commits "
                "WHERE reservation_id=$1",
                reservation,
            )
        if observed != binding.episode:
            raise PolicyUnavailableError("Committed Memory binding is unknown")
    finally:
        binding.active = False
        _current_memory_copy.reset(token)


@asynccontextmanager
async def memory_episode_writer(pool: Any):
    binding = _current_memory_copy.get()
    if binding is None:
        from butlers.chronicler.location_memory_context import (
            context_episode_writer,
            current_runtime_context,
        )

        context = current_runtime_context()
        async with pool.acquire() as conn:
            if context is None or not (
                context.loans or context.local_rows or context.generated_prompt
            ):
                yield conn
            else:
                async with conn.transaction():
                    await context_episode_writer(pool, conn)
                    yield conn
        return
    if not binding.active or pool is not binding.pool:
        raise PolicyUnavailableError("Native Memory writer lifetime differs")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _lock(conn, binding.schema, binding.role)
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_dispositions "
                "WHERE receiving_session=$1)",
                binding.session,
            ):
                raise PolicyUnavailableError("Native Memory input has been disposed")
            yield conn


async def bind_memory_episode(conn: Any, episode: UUID) -> None:
    binding = _current_memory_copy.get()
    if binding is None:
        return
    if not binding.active:
        raise PolicyUnavailableError("Native Memory writer lifetime ended")
    row = await conn.fetchrow("SELECT * FROM episodes WHERE id=$1 FOR UPDATE", episode)
    if (
        row is None
        or row["session_id"] != binding.session
        or content_digest({"native_memory_content": row["content"]}) != binding.digest
    ):
        raise PolicyUnavailableError("Native Memory persisted body differs")
    digest = episode_body_digest(row)
    await conn.execute(
        "INSERT INTO chronicler.location_native_memory_commits "
        "(reservation_id,episode_id,body_digest) VALUES($1,$2,$3)",
        binding.reservation,
        episode,
        digest,
    )
    binding.episode = episode


async def dispose_native_memory(domain: Any, decision: UUID) -> None:
    """Actual fixed receiver disposes only unchanged, unleased, un-derived bodies."""
    runtime = _receivers.get(domain)
    if runtime is None:
        return  # Existing reservations still block the frontier, never empty success.
    pool, schema, role = runtime
    candidates = await domain.fetch(
        "SELECT r.*,c.episode_id,c.body_digest FROM location_native_memory_reservations r "
        "JOIN location_native_memory_commits c USING(reservation_id) "
        "WHERE NOT EXISTS(SELECT 1 FROM location_native_memory_dispositions d "
        "WHERE d.reservation_id=r.reservation_id) ORDER BY r.reservation_id LIMIT 32"
    )
    for candidate in candidates:
        receipt = uuid4()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _lock(conn, schema, role)
                if candidate["memory_schema"] != schema or candidate["writer_role"] != role:
                    raise PolicyUnavailableError("Committed Memory producer differs")
                from butlers.chronicler.location_memory_ancestry import require_complete_parents

                ancestry = await conn.fetch(
                    "SELECT p.copy_generation,p.input_digest,r.parent_count,b.output_id,"
                    "b.input_digest AS birth_digest FROM "
                    "chronicler.location_native_memory_reservations r "
                    "LEFT JOIN chronicler.location_native_memory_parents p "
                    "USING(reservation_id) "
                    "LEFT JOIN chronicler.location_native_copy_births b "
                    "ON b.copy_generation=p.copy_generation WHERE r.reservation_id=$1",
                    candidate["reservation_id"],
                )
                if not ancestry:
                    raise PolicyUnavailableError("Native episode input ancestry is unavailable")
                require_complete_parents(ancestry)
                if not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_dispositions "
                    "WHERE receiving_session=$1) AND NOT EXISTS("
                    "SELECT 1 FROM chronicler.location_native_memory_parents p "
                    "JOIN chronicler.location_native_copy_births b "
                    "USING(copy_generation,input_digest) "
                    "WHERE p.reservation_id=$2 AND (NOT b.lineage_known OR NOT b.exclusive_input "
                    "OR NOT EXISTS(SELECT 1 FROM chronicler.location_retention_plan_outputs o "
                    "WHERE o.decision_id=$3 AND o.output_kind=b.output_kind "
                    "AND o.output_id=b.output_id)))",
                    candidate["receiving_session"],
                    candidate["reservation_id"],
                    decision,
                ):
                    continue
                body = await conn.fetchrow(
                    "SELECT * FROM episodes WHERE id=$1 FOR UPDATE OF episodes",
                    candidate["episode_id"],
                )
                if (
                    body is None
                    or body["session_id"] != candidate["receiving_session"]
                    or episode_body_digest(body) != candidate["body_digest"]
                    or body["leased_until"] is not None
                    or body["leased_by"] is not None
                    or body["consolidation_status"]
                    not in {"pending", "consolidated", "failed", "dead_letter"}
                ):
                    continue
                # Exact producer-captured bundle membership closes descendants;
                # citations, source_episode_id and temporal overlap do not mint
                # it. Inert UUID-only links may survive only when their actual
                # other endpoint is itself a disposed native artifact/episode.
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM "
                    "chronicler.location_native_memory_bundle_episodes e "
                    "WHERE e.episode_id=$1 AND (NOT EXISTS("
                    "SELECT 1 FROM chronicler.location_native_dispatch_parents p "
                    "WHERE p.input_generation=e.input_generation) OR EXISTS("
                    "SELECT 1 FROM chronicler.location_native_dispatch_parents p "
                    "WHERE p.input_generation=e.input_generation AND NOT EXISTS("
                    "SELECT 1 FROM chronicler.location_native_copy_dispositions d "
                    "WHERE d.copy_generation=p.copy_generation AND d.input_digest=p.input_digest "
                    "AND d.decision_id=$2)) OR EXISTS("
                    "SELECT 1 FROM chronicler.location_native_memory_artifacts a "
                    "WHERE a.input_generation=e.input_generation AND NOT EXISTS("
                    "SELECT 1 FROM chronicler.location_native_memory_artifact_dispositions d "
                    "WHERE d.artifact_generation=a.artifact_generation AND d.decision_id=$2)))) "
                    "OR EXISTS(SELECT 1 FROM facts WHERE source_episode_id=$1) "
                    "OR EXISTS(SELECT 1 FROM rules WHERE source_episode_id=$1) "
                    "OR EXISTS(SELECT 1 FROM memory_links l WHERE "
                    "((l.source_type='episode' AND l.source_id=$1) OR "
                    "(l.target_type='episode' AND l.target_id=$1)) AND NOT EXISTS("
                    "SELECT 1 FROM chronicler.location_native_memory_artifacts a "
                    "JOIN chronicler.location_native_memory_artifact_dispositions d "
                    "USING(artifact_generation) WHERE d.decision_id=$2 AND "
                    "((l.source_type='episode' AND l.source_id=$1 "
                    "AND l.target_type=CASE a.memory_table "
                    "WHEN 'facts' THEN 'fact' ELSE 'rule' END "
                    "AND l.target_id=a.artifact_id) OR (l.target_type='episode' AND l.target_id=$1 "
                    "AND l.source_type=CASE a.memory_table "
                    "WHEN 'facts' THEN 'fact' ELSE 'rule' END "
                    "AND l.source_id=a.artifact_id))))",
                    candidate["episode_id"],
                    decision,
                ):
                    continue
                if body["consolidation_status"] == "consolidated" and not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_memory_bundle_episodes "
                    "WHERE episode_id=$1)",
                    candidate["episode_id"],
                ):
                    continue  # An old consolidated episode has unknown descendants.
                deleted = await conn.fetchval(
                    "DELETE FROM episodes WHERE id=$1 AND session_id=$2 RETURNING id",
                    candidate["episode_id"],
                    candidate["receiving_session"],
                )
                if deleted != candidate["episode_id"]:
                    raise PolicyUnavailableError("Native Memory disposal changed")
                await conn.execute(
                    "INSERT INTO chronicler.location_native_memory_dispositions "
                    "(reservation_id,body_digest,receipt_id) VALUES($1,$2,$3)",
                    candidate["reservation_id"],
                    candidate["body_digest"],
                    receipt,
                )
        async with pool.acquire() as committed:
            observed = await committed.fetchval(
                "SELECT receipt_id FROM chronicler.location_native_memory_dispositions "
                "WHERE reservation_id=$1 AND body_digest=$2",
                candidate["reservation_id"],
                candidate["body_digest"],
            )
            remaining = await committed.fetchval(
                "SELECT EXISTS(SELECT 1 FROM episodes WHERE id=$1)", candidate["episode_id"]
            )
        if observed != receipt or remaining is not False:
            raise PolicyUnavailableError("Committed Memory disposal is unknown")


async def fence_memory_mutation(
    pool: Any, conn: Any, table: str, identifier: UUID, *, memory_schema: str | None = None
) -> None:
    """Current prepared-generation fence on the actual configured owning writer.

    Ordinary unconfigured Memory keeps its established behavior. This grants
    no lineage to the mutation or terminal authority to its returned fields.
    """
    from butlers.chronicler.location_memory_mutations import enter_mutation_owner

    owner = await enter_mutation_owner(pool, conn, memory_schema)
    if owner is None:
        return
    if table not in {"episodes", "facts", "rules"}:
        raise PolicyUnavailableError("Native mutation owning writer differs")
    schema, role = owner
    await _lock(conn, schema, role)  # Policy precedes the actual canonical row.
    await conn.fetchrow(f"SELECT id FROM {table} WHERE id=$1 FOR UPDATE", identifier)
    if table == "episodes":
        query = (
            "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_memory_commits c "
            "JOIN chronicler.location_native_memory_parents i USING(reservation_id) "
            "JOIN chronicler.location_native_copy_births b USING(copy_generation,input_digest) "
            "JOIN chronicler.location_retention_plan_outputs p USING(output_kind,output_id) "
            "WHERE c.episode_id=$1)"
        )
        values = (identifier,)
    else:
        query = (
            "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_memory_artifacts a "
            "JOIN chronicler.location_native_dispatch_parents i USING(input_generation) "
            "JOIN chronicler.location_native_copy_births b USING(copy_generation,input_digest) "
            "JOIN chronicler.location_retention_plan_outputs p USING(output_kind,output_id) "
            "WHERE a.memory_table=$1 AND a.artifact_id=$2)"
        )
        values = (table, identifier)
    if await conn.fetchval(query, *values):
        raise PolicyUnavailableError("Native mutation source generation is prepared")


@asynccontextmanager
async def memory_mutation_writer(
    pool: Any, table: str, identifier: UUID, *, memory_schema: str | None = None
):
    """Single-update helper retains unconfigured pool call compatibility."""
    from butlers.chronicler.location_copy_pools import _api_copy_pools
    from butlers.chronicler.location_memory_mutations import (
        memory_mutation_transaction,
        mutation_owner,
    )

    if mutation_owner(pool) is None and pool not in _api_copy_pools:
        yield pool
        return

    async with memory_mutation_transaction(
        pool, table, identifier, memory_schema=memory_schema
    ) as conn:
        yield conn


async def capture_memory_rows(pool: Any, table: str, query: str, args=()) -> list[Any]:
    """Policy-first native Memory reads, with actual receiving lineage.

    Episode content binds its owning commit; fact/rule content binds its
    original producer full body or optional versioned content witness.
    Independent/changed/unknown selected rows keep native tool inputs mixed;
    consolidated descendants never inherit from a nullable episode reference.
    """
    owners = [(domain, runtime) for domain, runtime in _receivers.items() if runtime[0] is pool]
    from butlers.chronicler.location_export_lifetime import (
        native_export_request,
        register_native_export,
    )
    from butlers.chronicler.location_retention import _api_copy_pools

    api_export = pool in _api_copy_pools
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_tool_copies import current_tool_copy

    runtime = _runtimes.get(owners[0][0]) if len(owners) == 1 else None
    tool = current_tool_copy(runtime) if runtime is not None else None
    if tool is not None and tool.runtime.memory is not pool:
        raise PolicyUnavailableError("Native Memory tool writer differs")
    if table not in {"episodes", "facts", "rules"} or (not owners and not api_export):
        rows = await pool.fetch(query, *args)
        if tool is not None:
            tool.read_observed = True
            # An unrecognized selection cannot borrow another row's authority.
            tool.mixed_inputs |= bool(rows)
        return rows
    if api_export:
        request = native_export_request()
        if request is None:
            raise PolicyUnavailableError("Owning Memory export lifetime is unavailable")
        schema = "chronicler"
        role = await pool.fetchval("SELECT current_user")
    else:
        request = None
        if len(owners) != 1:
            raise PolicyUnavailableError("Owning Memory read lifecycle is ambiguous")
        _, (_, schema, role) = owners[0]
    from butlers.chronicler.location_input_binding import _current_dispatch_input
    from butlers.chronicler.location_projection import _digest_value
    from butlers.core.copy_lifetime import _current_copy_invocation

    invocation = _current_copy_invocation.get()
    dispatch = _current_dispatch_input.get()
    from butlers.chronicler.location_memory_context import current_runtime_context

    context = current_runtime_context()
    receiver = context.session if context is not None else None
    if tool is not None:
        receiver = tool.session  # Only the already admitted private tool binding.
    elif invocation is not None:
        from butlers.chronicler.location_tool_copies import registered_copy_invocation

        invocation = registered_copy_invocation("chronicler")
        receiver = UUID(invocation.runtime_session)
    elif dispatch is not None and dispatch.active:
        receiver = dispatch.session_id
    copies = []
    mixed_inputs = False
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _lock(conn, schema, role)
            rows = await conn.fetch(query, *args)
            for row in rows:
                canonical = await conn.fetchrow(
                    f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", row["id"]
                )
                selected_matches = (
                    canonical is not None
                    and all(row.get(key) == value for key, value in dict(canonical).items())
                    and not (set(row) - set(canonical) - {"similarity", "rank"})
                    and all(
                        type(row[key]) in (int, float) and math.isfinite(row[key])
                        for key in set(row) - set(canonical)
                    )
                )
                if table == "episodes":
                    parents = await conn.fetch(
                        "SELECT p.copy_generation,p.input_digest,r.parent_count,"
                        "b.output_kind,b.output_id,"
                        "b.input_digest AS birth_digest,b.lineage_known,b.exclusive_input,"
                        "c.body_digest FROM chronicler.location_native_memory_commits c "
                        "JOIN chronicler.location_native_memory_reservations r "
                        "USING(reservation_id) "
                        "LEFT JOIN chronicler.location_native_memory_parents p "
                        "USING(reservation_id) "
                        "LEFT JOIN chronicler.location_native_copy_births b "
                        "ON b.copy_generation=p.copy_generation "
                        "WHERE c.episode_id=$1 ORDER BY b.output_kind,b.output_id",
                        row["id"],
                    )
                else:
                    parents = await conn.fetch(
                        "SELECT p.copy_generation,p.input_digest,i.parent_count,"
                        "b.input_digest AS birth_digest,b.output_kind,b.output_id,"
                        "(b.lineage_known AND m.exclusive_input) AS lineage_known,"
                        "(b.exclusive_input AND m.exclusive_input) AS exclusive_input,"
                        "a.body_digest,a.content_digest,a.memory_table,a.artifact_generation "
                        "FROM chronicler.location_native_memory_artifacts a "
                        "JOIN chronicler.location_native_memory_bundles m USING(input_generation) "
                        "JOIN chronicler.location_native_dispatch_inputs i USING(input_generation) "
                        "LEFT JOIN chronicler.location_native_dispatch_parents p "
                        "USING(input_generation) "
                        "LEFT JOIN chronicler.location_native_copy_births b "
                        "ON b.copy_generation=p.copy_generation "
                        "WHERE a.memory_table=$1 AND a.artifact_id=$2 "
                        "ORDER BY b.output_kind,b.output_id",
                        table,
                        row["id"],
                    )
                from butlers.chronicler.location_memory_ancestry import require_complete_parents

                require_complete_parents(parents)
                if not parents:
                    mixed_inputs = True
                    continue
                from butlers.chronicler.location_memory_mutations import (
                    current_artifact_body_matches,
                )

                unchanged = selected_matches
                for parent in parents:
                    unchanged &= (
                        parent["body_digest"] == episode_body_digest(canonical)
                        if table == "episodes"
                        else await current_artifact_body_matches(conn, canonical, parent)
                    )
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_retention_plans p "
                    "JOIN chronicler.location_retention_plan_outputs o USING(decision_id) "
                    "JOIN chronicler.location_native_copy_births b USING(output_kind,output_id) "
                    "WHERE (EXISTS("
                    "SELECT 1 FROM chronicler.location_native_memory_parents m "
                    "JOIN chronicler.location_native_memory_commits c USING(reservation_id) "
                    "WHERE c.episode_id=$1 AND m.copy_generation=b.copy_generation "
                    "AND m.input_digest=b.input_digest) OR EXISTS("
                    "SELECT 1 FROM chronicler.location_native_memory_artifacts a "
                    "JOIN chronicler.location_native_dispatch_parents i USING(input_generation) "
                    "WHERE a.memory_table=$2 AND a.artifact_id=$1 "
                    "AND i.copy_generation=b.copy_generation AND i.input_digest=b.input_digest)))",
                    row["id"],
                    table,
                ):
                    raise PolicyUnavailableError("Native Memory read generation is fenced")
                generation = uuid4()
                digest = content_digest({"memory_export": _digest_value(dict(row))})
                outputs = {}
                for parent in parents:
                    key = (parent["output_kind"], parent["output_id"])
                    outputs[key] = outputs.get(key, True) and (
                        unchanged
                        and parent["lineage_known"] is True
                        and parent["exclusive_input"] is True
                    )
                mixed_inputs |= not all(outputs.values())
                for (kind, output), known in outputs.items():
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_copy_births "
                        "(copy_generation,output_kind,output_id,input_digest,lineage_known,"
                        "receiving_session,exclusive_input,producer_kind,receiving_server_request) "
                        "VALUES($1,$2,$3,$4,$5,$6,$5,$7,$8)",
                        generation,
                        kind,
                        output,
                        digest,
                        known,
                        None if api_export else receiver,
                        "api_export" if api_export else "native_mcp",
                        request,
                    )
                if context is not None and context.runtime.memory is pool:
                    context.local_rows.add((table, UUID(str(row["id"]))))
                copies.append((generation, digest, len(outputs)))
    async with pool.acquire() as committed:
        for generation, digest, count in copies:
            observed = await committed.fetchval(
                "SELECT count(*) FROM chronicler.location_native_copy_births "
                "WHERE copy_generation=$1 AND input_digest=$2",
                generation,
                digest,
            )
            if observed != count:
                raise PolicyUnavailableError("Committed Memory read birth is unknown")
            if api_export:
                register_native_export(pool, "native_read", generation, digest)
    if context is not None and context.runtime.memory is pool:
        context.known_context &= not mixed_inputs
    if tool is not None:
        tool.read_observed = True
        tool.mixed_inputs |= mixed_inputs
    return rows


async def capture_memory_row(pool: Any, table: str, query: str, args=()) -> Any:
    from butlers.chronicler.location_retention import _api_copy_pools

    native = pool in _api_copy_pools or any(runtime[0] is pool for runtime in _receivers.values())
    if not native:
        return await pool.fetchrow(query, *args)
    rows = await capture_memory_rows(pool, table, query, args)
    return rows[0] if rows else None
