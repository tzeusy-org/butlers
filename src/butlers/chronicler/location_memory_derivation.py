"""Owning consolidation input and artifact capture, never model provenance.

The configured Memory writer captures the entire actual input bundle before
dispatch. New artifacts inherit that immutable bundle on their own transaction;
cited episode IDs and returned runtime IDs cannot install this context.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_retention import PolicyUnavailableError
from butlers.location_retention import content_digest


@dataclass
class _Derivation:
    pool: Any
    schema: str
    role: str
    generation: UUID
    digest: bytes
    active: bool = True
    memory_context_present: bool | None = None
    system_digest: bytes | None = None
    connection: Any = None
    pending_artifacts: set[tuple[str, UUID]] = field(default_factory=set)


def capture_composed_prompt(memory_context: str | None, system_prompt: str) -> None:
    """Called by the actual core prompt composer, not a tool/caller field."""
    binding = _current_derivation.get()
    if binding is not None:
        if not binding.active:
            raise PolicyUnavailableError("Native consolidation lifetime ended")
        binding.memory_context_present = bool(memory_context)
        binding.system_digest = hashlib.sha256(system_prompt.encode("utf-8")).digest()


async def bind_composed_prompt(conn: Any, generation: UUID, session: UUID) -> None:
    binding = _current_derivation.get()
    if binding is None:
        return
    if (
        not binding.active
        or binding.generation != generation
        or binding.memory_context_present is None
        or binding.system_digest is None
    ):
        raise PolicyUnavailableError("Native runtime input receipt is unavailable")
    row = await conn.fetchrow("SELECT effective_system_prompt FROM sessions WHERE id=$1", session)
    if (
        row is None
        or hashlib.sha256(row["effective_system_prompt"].encode("utf-8")).digest()
        != binding.system_digest
    ):
        raise PolicyUnavailableError("Native composed runtime body differs")
    await conn.execute(
        "INSERT INTO location_native_memory_runtime_receipts "
        "(input_generation,receiving_session,system_digest,memory_context_present) "
        "VALUES($1,$2,$3,$4)",
        generation,
        session,
        binding.system_digest,
        binding.memory_context_present,
    )


_current_derivation: ContextVar[_Derivation | None] = ContextVar(
    "native_location_memory_derivation", default=None
)


@asynccontextmanager
async def native_consolidation_input(
    pool: Any, spawner: Any, episodes: list[dict], facts: list[dict], rules: list[dict], prompt: str
):
    from butlers.chronicler.location_input_binding import (
        _current_dispatch_input,
        _DispatchInput,
        registered_dispatcher,
        reserve_dispatch_receiver,
    )
    from butlers.chronicler.location_memory_copies import _lock, _receivers
    from butlers.chronicler.location_projection import _digest_value

    registered = next(
        (
            (domain, schema, role)
            for domain, (memory, schema, role) in _receivers.items()
            if memory is pool
        ),
        None,
    )
    if registered is None:
        yield
        return
    domain, schema, role = registered
    ids = [UUID(str(row["id"])) for row in episodes]
    parents = await domain.fetch(
        "SELECT DISTINCT p.copy_generation,p.input_digest FROM location_native_memory_commits c "
        "JOIN location_native_memory_parents p USING(reservation_id) WHERE c.episode_id=ANY($1)",
        ids,
    )
    if not parents:
        yield
        return
    if not registered_dispatcher(domain, spawner) or spawner._pool is not domain:
        raise PolicyUnavailableError("Native consolidation receiver differs")
    generation, server_request, parent_generation = uuid4(), uuid4(), uuid4()
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    binding = _DispatchInput(generation, digest, uuid4())
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _lock(conn, schema, role)
            # Re-read actual claimed bodies before copied prompt admission. The
            # lease changes are deliberately outside the content comparison.
            for expected in episodes:
                actual = await conn.fetchrow(
                    "SELECT id,butler,content,importance,metadata,created_at,tenant_id,"
                    "consolidation_attempts,content_authority,authority_entity_id "
                    "FROM episodes WHERE id=$1 FOR UPDATE",
                    expected["id"],
                )
                if actual is None or dict(actual) != expected:
                    raise PolicyUnavailableError("Native consolidation input changed")
            for table, selected, columns in (
                ("facts", facts, "id,subject,predicate,content,permanence,entity_id,valid_at"),
                ("rules", rules, "id,content,maturity"),
            ):
                for expected in selected:
                    actual = await conn.fetchrow(
                        f"SELECT {columns} FROM {table} WHERE id=$1 FOR UPDATE", expected["id"]
                    )
                    if actual is None or dict(actual) != expected:
                        raise PolicyUnavailableError("Native dedup input changed")
            for parent in parents:
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_dispositions "
                    "WHERE copy_generation=$1)",
                    parent["copy_generation"],
                ):
                    raise PolicyUnavailableError("Native consolidation input was disposed")
            await conn.execute(
                "INSERT INTO chronicler.location_native_dispatch_inputs "
                "(input_generation,server_request,prompt_digest,parent_count,origin_kind) "
                "VALUES($1,$2,$3,1,'native_memory')",
                generation,
                server_request,
                digest,
            )
            # One generation describes this full captured bundle. Independent
            # fact/rule context remains preserved; it is not exclusively owned
            # by an episode's location input merely because it was read nearby.
            complete = await conn.fetchval(
                "SELECT count(DISTINCT episode_id) FROM chronicler.location_native_memory_commits "
                "WHERE episode_id=ANY($1)",
                ids,
            ) == len(ids)
            exclusive = complete and not facts and not rules
            for parent in parents:
                await conn.execute(
                    "INSERT INTO chronicler.location_native_copy_births "
                    "(copy_generation,output_kind,output_id,input_digest,lineage_known,"
                    "receiving_server_request,receiving_session,exclusive_input,producer_kind) "
                    "SELECT $1,output_kind,output_id,$2,$3,$4,$8,$5,'native_memory' "
                    "FROM chronicler.location_native_copy_births WHERE copy_generation=$6 "
                    "AND input_digest=$7 ON CONFLICT DO NOTHING",
                    parent_generation,
                    digest,
                    complete,
                    server_request,
                    exclusive,
                    parent["copy_generation"],
                    parent["input_digest"],
                    binding.session_id,
                )
            await conn.execute(
                "INSERT INTO chronicler.location_native_dispatch_parents "
                "(input_generation,copy_generation,input_digest) VALUES($1,$2,$3)",
                generation,
                parent_generation,
                digest,
            )
            await conn.execute(
                "INSERT INTO chronicler.location_native_memory_bundles "
                "(input_generation,bundle_digest,exclusive_input) VALUES($1,$2,$3)",
                generation,
                content_digest(
                    {
                        "bundle": _digest_value(
                            {"episodes": episodes, "facts": facts, "rules": rules}
                        )
                    }
                ),
                exclusive,
            )
            for episode in episodes:
                await conn.execute(
                    "INSERT INTO chronicler.location_native_memory_bundle_episodes "
                    "(input_generation,episode_id) VALUES($1,$2)",
                    generation,
                    UUID(str(episode["id"])),
                )
    async with domain.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_dispatch_inputs i "
            "JOIN location_native_memory_bundles b USING(input_generation) "
            "WHERE i.input_generation=$1 AND i.prompt_digest=$2 AND i.server_request=$3 "
            "AND EXISTS(SELECT 1 FROM location_native_copy_births c "
            "WHERE c.copy_generation=$4 AND c.input_digest=i.prompt_digest))",
            generation,
            digest,
            server_request,
            parent_generation,
        )
    if observed is not True:
        raise PolicyUnavailableError("Committed consolidation input is unknown")
    await reserve_dispatch_receiver(domain, binding, server_request)
    derivation = _Derivation(pool, schema, role, generation, digest)
    dispatch_token = _current_dispatch_input.set(binding)
    token = _current_derivation.set(derivation)
    try:
        yield
    finally:
        derivation.active = False
        binding.active = False
        _current_derivation.reset(token)
        _current_dispatch_input.reset(dispatch_token)


@asynccontextmanager
async def derivation_writer(pool: Any):
    from butlers.chronicler.location_memory_copies import _lock

    binding = _current_derivation.get()
    async with pool.acquire() as conn:
        if binding is None:
            yield conn
            return
        if not binding.active or (binding.pool is not pool and binding.connection is not conn):
            raise PolicyUnavailableError("Native artifact writer differs")
        async with conn.transaction():
            await _lock(conn, binding.schema, binding.role)
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_dispositions "
                "WHERE copy_generation=$1)",
                binding.generation,
            ):
                raise PolicyUnavailableError("Native artifact input was disposed")
            prior = binding.connection
            binding.connection = conn
            try:
                yield conn
            finally:
                binding.connection = prior


async def bind_artifact(conn: Any, table: str, artifact: UUID) -> None:
    binding = _current_derivation.get()
    if binding is None:
        return
    if not binding.active or table not in {"facts", "rules"}:
        raise PolicyUnavailableError("Native artifact producer differs")
    if (
        await conn.fetchval("SELECT current_schema()") != binding.schema
        or await conn.fetchval("SELECT current_user") != binding.role
    ):
        raise PolicyUnavailableError("Native artifact identity differs")
    if binding.connection is not conn:
        raise PolicyUnavailableError("Native artifact transaction differs")
    # The real INSERT, including inverse facts, registers a server-minted ID.
    # Final provenance/metadata writers still execute on this transaction.
    # Only that final producer checkpoint freezes the complete persisted body.
    binding.pending_artifacts.add((table, artifact))


async def finalize_derivation_artifacts(conn: Any) -> None:
    binding = _current_derivation.get()
    if binding is None:
        return
    if not binding.active or binding.connection is not conn:
        raise PolicyUnavailableError("Native final artifact writer differs")
    from butlers.chronicler.location_catalog_copies import bind_catalog
    from butlers.chronicler.location_projection import _digest_value

    runtime_receipt = await conn.fetchrow(
        "SELECT * FROM chronicler.location_native_memory_runtime_receipts "
        "WHERE input_generation=$1",
        binding.generation,
    )
    if runtime_receipt is None or runtime_receipt["system_digest"] != binding.system_digest:
        raise PolicyUnavailableError("Native artifact runtime input is unknown")
    for table, artifact in sorted(binding.pending_artifacts):
        row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", artifact)
        if row is None:
            raise PolicyUnavailableError("Native final artifact body is unavailable")
        await conn.execute(
            "INSERT INTO chronicler.location_native_memory_artifacts "
            "(artifact_generation,input_generation,memory_table,artifact_id,body_digest) "
            "VALUES($1,$2,$3,$4,$5)",
            uuid4(),
            binding.generation,
            table,
            artifact,
            content_digest({"memory_artifact": _digest_value(dict(row))}),
        )
        catalog = await conn.fetchrow(
            "SELECT source_schema FROM public.memory_catalog "
            "WHERE source_table=$1 AND source_id=$2 AND source_schema=$3",
            table,
            artifact,
            binding.schema,
        )
        if catalog is not None:
            await bind_catalog(conn, binding.pool, catalog["source_schema"], table, artifact)
    binding.pending_artifacts.clear()


async def execute_rule_insert(pool: Any, artifact: UUID, sql: str, *values: Any) -> None:
    if _current_derivation.get() is None:
        await pool.execute(sql, *values)
        return
    async with derivation_writer(pool) as conn:
        await conn.execute(sql, *values)
        await bind_artifact(conn, "rules", artifact)
