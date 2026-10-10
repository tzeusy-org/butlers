"""Immutable SAME-writer body evolution, anchored to a native artifact birth.

The original body and parent bundle never change. A transition contains only
fixed-profile digests, not copied prose. Mixed edits stay held; a current row
or a model-selected UUID cannot refill an absent original content witness.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from butlers.chronicler.location_copy_pools import (
    _api_writers as _api_writers,
)
from butlers.chronicler.location_copy_pools import (
    register_api_memory_writer as register_api_memory_writer,
)
from butlers.chronicler.location_retention import PolicyUnavailableError


def mutation_owner(pool: Any):
    from butlers.chronicler.location_memory_copies import _receivers

    owners = [cell[1:] for cell in _receivers.values() if cell[0] is pool]
    if pool in _api_writers:
        _, schema, role = _api_writers[pool]
        owners.append((schema, role))
    if len(owners) > 1:
        raise PolicyUnavailableError("Native mutation owning writer is ambiguous")
    return owners[0] if owners else None


async def enter_mutation_owner(pool: Any, conn: Any, memory_schema: str | None):
    from butlers.chronicler.location_copy_pools import _api_copy_pools

    owner = mutation_owner(pool)
    if owner is None:
        if pool in _api_copy_pools:
            raise PolicyUnavailableError("Native API Memory writer is not enrolled")
        return None
    schema, role = owner
    if memory_schema is not None and memory_schema != schema:
        raise PolicyUnavailableError("Native mutation owning schema differs")
    if pool in _api_writers:
        domain, _, expected_role = _api_writers[pool]
        if (
            await conn.fetchval("SELECT current_schema()") != domain
            or await conn.fetchval("SELECT current_user") != expected_role
        ):
            raise PolicyUnavailableError("Native API Memory writer identity differs")
        from butlers.db import schema_search_path

        # Transaction-local view only; no role, grant or pool search-path change.
        await conn.execute("SET LOCAL search_path TO " + schema_search_path(schema))
    return owner


# Exact lifecycle effects of the native writers. Freeform metadata, body,
# authority, source, entity and embedding changes cannot inherit exclusivity.
_LIFECYCLE_FIELDS = {
    "facts": frozenset({"validity", "last_confirmed_at", "permanence", "decay_rate"}),
    "rules": frozenset(
        {
            "retired_at",
            "last_confirmed_at",
            "applied_count",
            "success_count",
            "harmful_count",
            "last_applied_at",
            "effectiveness_score",
            "maturity",
        }
    ),
}


def lifecycle_only(table: str, before: Any, after: Any) -> bool:
    if table not in _LIFECYCLE_FIELDS or set(before) != set(after):
        return False
    changed = {key for key in before if before[key] != after[key]}
    # Plain rule-forget adds exactly the fixed boolean; reasons, actor changes
    # and every other metadata annotation remain independent/mixed.
    if "metadata" in changed and table == "rules":
        old, new = before["metadata"], after["metadata"]
        if isinstance(old, dict) and isinstance(new, dict) and new == {**old, "forgotten": True}:
            changed.remove("metadata")
    return changed <= _LIFECYCLE_FIELDS[table]


async def mutation_chain(conn: Any, witness: Any) -> list[Any]:
    generation = witness.get("artifact_generation")
    if generation is None:
        return []  # A legacy/double has no fabricated native generation.
    return await conn.fetch(
        "SELECT * FROM chronicler.location_native_memory_mutations "
        "WHERE artifact_generation=$1 ORDER BY revision",
        generation,
    )


def chain_head(witness: Any, transitions: list[Any]) -> tuple[bytes | None, bool]:
    digest, previous, exclusive = witness.get("content_digest"), None, True
    if not transitions:
        return digest, exclusive
    if not isinstance(digest, bytes) or len(digest) != 32:
        return None, False
    for revision, transition in enumerate(transitions, 1):
        if (
            transition["artifact_generation"] != witness["artifact_generation"]
            or transition["revision"] != revision
            or transition["previous_generation"] != previous
            or transition["before_digest"] != digest
            or not isinstance(transition["after_digest"], bytes)
            or len(transition["after_digest"]) != 32
        ):
            return None, False
        digest, previous = transition["after_digest"], transition["mutation_generation"]
        exclusive &= transition["lifecycle_only"] is True
    return digest, exclusive


async def current_artifact_body_matches(conn: Any, row: Any, witness: Any) -> bool:
    from butlers.chronicler.location_memory_copies import (
        artifact_body_matches,
        artifact_content_digest,
    )

    transitions = await mutation_chain(conn, witness)
    if not transitions:
        return artifact_body_matches(row, witness)
    digest, exclusive = chain_head(witness, transitions)
    return (
        row is not None
        and exclusive
        and digest is not None
        and artifact_content_digest(witness["memory_table"], row) == digest
    )


_MUTATION_TOOLS = frozenset(
    {
        "memory_confirm",
        "memory_forget",
        "memory_mark_helpful",
        "memory_mark_harmful",
        "memory_reclassify",
    }
)


async def reserve_mutation_tool_input(
    pool: Any, conn: Any, witness: Any, before: Any, exclusive: bool
):
    """Actual registered tool + actual native row, before business processing.

    These births describe the native server tool's selected canonical input,
    not another runtime's authority or a model-reported source/result ID.
    """
    from butlers.chronicler.location_catalog_copies import _runtimes
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.location_tool_copies import current_tool_copy
    from butlers.location_retention import content_digest

    runtime = _runtimes.get(pool)
    tool = current_tool_copy(runtime) if runtime is not None else None
    if tool is None:
        return None  # A fixed API writer does not enroll a receiving runtime.
    if runtime.name != "chronicler" or tool.module != "memory" or tool.name not in _MUTATION_TOOLS:
        raise PolicyUnavailableError("Native mutation tool producer differs")
    if not await conn.fetchval(
        "SELECT count(*)=1 AND bool_and("
        "c.confrelid='chronicler.location_runtime_tool_intents'::pg_catalog.regclass "
        "AND c.conname='location_native_memory_mutation_inputs_tool_generation_fkey' "
        "AND c.conkey=ARRAY[2]::SMALLINT[] AND c.confkey=ARRAY[1]::SMALLINT[] "
        "AND c.convalidated AND NOT c.condeferrable AND NOT c.condeferred "
        "AND c.confupdtype='a' AND c.confdeltype='a' AND c.confmatchtype='s') "
        "FROM pg_catalog.pg_constraint c WHERE "
        "c.conrelid='chronicler.location_native_memory_mutation_inputs'::pg_catalog.regclass "
        "AND c.contype='f' AND 2=ANY(c.conkey)"
    ):
        raise PolicyUnavailableError("Native mutation installed tool dependency differs")
    if not await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM chronicler.location_runtime_tool_intents "
        "WHERE tool_generation=$1 AND receiving_session=$2 AND tool_name=$3 "
        "AND module_name='memory')",
        tool.generation,
        tool.session,
        tool.name,
    ):
        raise PolicyUnavailableError("Native mutation tool reservation is unavailable")
    parents = await conn.fetch(
        "SELECT p.copy_generation,p.input_digest,i.parent_count,b.output_kind,b.output_id,"
        "b.input_digest AS birth_digest,b.lineage_known,b.exclusive_input,"
        "m.exclusive_input AS bundle_exclusive "
        "FROM chronicler.location_native_memory_artifacts a "
        "JOIN chronicler.location_native_dispatch_inputs i USING(input_generation) "
        "JOIN chronicler.location_native_memory_bundles m USING(input_generation) "
        "LEFT JOIN chronicler.location_native_dispatch_parents p USING(input_generation) "
        "LEFT JOIN chronicler.location_native_copy_births b "
        "ON b.copy_generation=p.copy_generation "
        "WHERE a.artifact_generation=$1 ORDER BY p.copy_generation,b.output_kind,b.output_id",
        witness["artifact_generation"],
    )
    if not parents:
        raise PolicyUnavailableError("Native mutation complete input ancestry is unavailable")
    from butlers.chronicler.location_memory_ancestry import require_complete_parents

    require_complete_parents(parents)
    digest = content_digest({"native_mutation_input": _digest_value(dict(before))})
    generation = uuid4()
    outputs = {}
    for parent in parents:
        key = (parent["output_kind"], parent["output_id"])
        outputs[key] = outputs.get(key, True) and (
            exclusive
            and parent["lineage_known"] is True
            and parent["exclusive_input"] is True
            and parent["bundle_exclusive"] is True
        )
    for (kind, identifier), known in outputs.items():
        await conn.execute(
            "INSERT INTO chronicler.location_native_copy_births "
            "(copy_generation,output_kind,output_id,input_digest,lineage_known,receiving_session,"
            "exclusive_input,producer_kind) VALUES($1,$2,$3,$4,$5,$6,$5,'native_mcp')",
            generation,
            kind,
            identifier,
            digest,
            known,
            tool.session,
        )
    return tool, generation, digest, len(outputs), all(outputs.values())


async def finish_mutation_tool_input(conn: Any, cell: Any, witness: Any, after: Any, closed: bool):
    if cell is None:
        return None
    from butlers.chronicler.location_memory_copies import artifact_content_digest
    from butlers.chronicler.location_tool_copies import current_tool_copy

    tool, generation, digest, count, known = cell
    if current_tool_copy(tool.runtime) is not tool:
        raise PolicyUnavailableError("Native mutation tool lifetime ended")
    known &= closed
    await conn.execute(
        "INSERT INTO chronicler.location_native_memory_mutation_inputs "
        "(input_generation,tool_generation,artifact_generation,before_digest,after_digest,"
        "parent_count,lifecycle_only) VALUES($1,$2,$3,$4,$5,$6,$7)",
        generation,
        tool.generation,
        witness["artifact_generation"],
        digest,
        artifact_content_digest(witness["memory_table"], after),
        count,
        known,
    )
    # The committed readback below precedes returning the handler's result;
    # finish_tool_copy then freezes the actual one-to-one result fingerprint.
    tool.read_observed = True
    tool.mixed_inputs |= not known
    return {
        "kind": "input",
        "generation": generation,
        "tool": tool.generation,
        "digest": digest,
        "parents": count,
    }


@asynccontextmanager
async def tracked_memory_mutation(
    pool: Any, conn: Any, table: str, identifier: Any, *, memory_schema: str | None = None
):
    """Enter inside the native business transaction, before its canonical lock.

    The configured owning writer, not the requested ID, supplies enrollment.
    A raised write/receipt error rolls the whole business transaction back.
    Separate committed readback is the caller's post-COMMIT responsibility.
    """
    from butlers.chronicler.location_memory_copies import (
        artifact_content_digest,
        fence_memory_mutation,
    )

    await fence_memory_mutation(pool, conn, table, identifier, memory_schema=memory_schema)
    configured = mutation_owner(pool) is not None
    witness = None
    if configured and table in _LIFECYCLE_FIELDS:
        witness = await conn.fetchrow(
            "SELECT * FROM chronicler.location_native_memory_artifacts "
            "WHERE memory_table=$1 AND artifact_id=$2 FOR UPDATE",
            table,
            identifier,
        )
    recorded = []
    if witness is None:
        yield recorded
        return  # Independent/unclassified rows never acquire source ancestry.
    before = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", identifier)
    transitions = await mutation_chain(conn, witness)
    prior, exclusive = chain_head(witness, transitions)
    if before is None or prior is None or artifact_content_digest(table, before) != prior:
        raise PolicyUnavailableError("Native mutation original body witness differs")
    before = dict(before)
    input_cell = await reserve_mutation_tool_input(pool, conn, witness, before, exclusive)
    yield recorded
    after = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", identifier)
    if after is None:
        raise PolicyUnavailableError("Native mutation cannot erase its body witness")
    digest = artifact_content_digest(table, after)
    closed = exclusive and lifecycle_only(table, before, dict(after))
    input_readback = await finish_mutation_tool_input(conn, input_cell, witness, after, closed)
    if input_readback is not None:
        recorded.append(input_readback)
    if digest == prior:
        return  # Idempotence/read-reference changes do not create body versions.
    mutation = uuid4()
    await conn.execute(
        "INSERT INTO chronicler.location_native_memory_mutations "
        "(mutation_generation,artifact_generation,revision,previous_generation,"
        "before_digest,after_digest,lifecycle_only) VALUES($1,$2,$3,$4,$5,$6,$7)",
        mutation,
        witness["artifact_generation"],
        len(transitions) + 1,
        transitions[-1]["mutation_generation"] if transitions else None,
        prior,
        digest,
        closed,
    )
    # The actual enrolled writer captures the SAME-transaction catalog
    # projection; old source generations and their loans are never replaced.
    schema, _ = mutation_owner(pool)
    catalog = await conn.fetchrow(
        "SELECT * FROM public.memory_catalog WHERE source_schema=$1 "
        "AND source_table=$2 AND source_id=$3 FOR UPDATE",
        schema,
        table,
        identifier,
    )
    if catalog is not None:
        from butlers.chronicler.location_catalog_copies import _body
        from butlers.location_retention import content_digest

        source = uuid4()
        await conn.execute(
            "INSERT INTO chronicler.location_native_catalog_generations "
            "(source_generation,catalog_id,artifact_generation,body_digest) VALUES($1,$2,$3,$4)",
            source,
            catalog["id"],
            witness["artifact_generation"],
            content_digest({"catalog_body": _body(catalog)}),
        )
        await conn.execute(
            "INSERT INTO chronicler.location_native_catalog_heads(catalog_id,source_generation) "
            "VALUES($1,$2) ON CONFLICT(catalog_id) DO UPDATE SET "
            "source_generation=EXCLUDED.source_generation",
            catalog["id"],
            source,
        )
    recorded.append(
        {
            "kind": "mutation",
            "generation": mutation,
            "artifact": witness["artifact_generation"],
            "digest": digest,
        }
    )


@asynccontextmanager
async def memory_mutation_transaction(
    pool: Any, table: str, identifier: Any, *, memory_schema: str | None = None
):
    """Native atomic business write, followed by a separate committed readback.

    Return inside the caller's body still exits this transaction and verifies
    the exact immutable transition before returning its business result.
    Missing/lost ACK stays unknown for that version, never rewrites its birth.
    A later genuine native mutation creates a separate immutable version.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            async with tracked_memory_mutation(
                pool, conn, table, identifier, memory_schema=memory_schema
            ) as recorded:
                yield conn
    if recorded:
        async with pool.acquire() as committed:
            for item in recorded:
                if item["kind"] == "input":
                    observed = await committed.fetchrow(
                        "SELECT before_digest,parent_count "
                        "FROM chronicler.location_native_memory_mutation_inputs "
                        "WHERE input_generation=$1 AND tool_generation=$2",
                        item["generation"],
                        item["tool"],
                    )
                    count = await committed.fetchval(
                        "SELECT count(*) FROM chronicler.location_native_copy_births "
                        "WHERE copy_generation=$1 AND input_digest=$2",
                        item["generation"],
                        item["digest"],
                    )
                    if (
                        observed is None
                        or observed["before_digest"] != item["digest"]
                        or observed["parent_count"] != item["parents"]
                        or count != item["parents"]
                    ):
                        raise PolicyUnavailableError("Committed native mutation input is unknown")
                elif (
                    await committed.fetchval(
                        "SELECT after_digest FROM chronicler.location_native_memory_mutations "
                        "WHERE mutation_generation=$1 AND artifact_generation=$2",
                        item["generation"],
                        item["artifact"],
                    )
                    != item["digest"]
                ):
                    raise PolicyUnavailableError("Committed native mutation witness is unknown")
