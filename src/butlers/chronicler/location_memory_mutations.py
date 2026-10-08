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
    yield recorded
    after = await conn.fetchrow(f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", identifier)
    if after is None:
        raise PolicyUnavailableError("Native mutation cannot erase its body witness")
    digest = artifact_content_digest(table, after)
    if digest == prior:
        return  # Idempotence/read-reference changes do not create versions.
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
        exclusive and lifecycle_only(table, before, dict(after)),
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
    recorded.append((mutation, witness["artifact_generation"], digest))


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
            for mutation, artifact, digest in recorded:
                if (
                    await committed.fetchval(
                        "SELECT after_digest FROM chronicler.location_native_memory_mutations "
                        "WHERE mutation_generation=$1 AND artifact_generation=$2",
                        mutation,
                        artifact,
                    )
                    != digest
                ):
                    raise PolicyUnavailableError("Committed native mutation witness is unknown")
