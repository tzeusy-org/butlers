"""Native processing reservations at the actual claim/read boundaries.

Only constructor-registered owning pools participate. Stored UUIDs select an
immutable claim; neither a prompt nor a caller's session string installs it.
The terminal receipt attests this native Python processing lifetime only.
Runtime, catalog and persisted descendants retain their separate holders.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_retention import PolicyUnavailableError
from butlers.location_retention import content_digest


@dataclass
class _Processing:
    pool: Any
    domain: Any
    schema: str
    role: str
    claims: list[tuple[UUID, bytes]] = field(default_factory=list)
    active: bool = True


_processing: ContextVar[_Processing | None] = ContextVar("native_memory_processing", default=None)


def _registered(pool: Any):
    from butlers.chronicler.location_memory_copies import _receivers

    return next(
        (
            (domain, schema, role)
            for domain, (memory, schema, role) in _receivers.items()
            if memory is pool
        ),
        None,
    )


@asynccontextmanager
async def processing_lifetime(pool: Any):
    registered = _registered(pool)
    if registered is None:
        yield
        return
    domain, schema, role = registered
    binding = _Processing(pool, domain, schema, role)
    token = _processing.set(binding)
    try:
        yield
    finally:
        # The nested native runner has returned/unwound: its prompt, selected
        # row bundles and output-processing locals are no longer processing.
        # A crash before this commit leaves a genuine unfinished holder.
        binding.active = False
        _processing.reset(token)
        from butlers.chronicler.location_memory_copies import _lock

        receipts = []
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _lock(conn, schema, role)
                for claim, digest in binding.claims:
                    receipt = uuid4()
                    # Failed/rolled-back claim transactions created no admitted
                    # durable reservation. Never insert a receipt for them.
                    exists = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_processing_claims "
                        "WHERE claim_id=$1 AND bundle_digest=$2)",
                        claim,
                        digest,
                    )
                    if exists is not True:
                        continue
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_processing_finished "
                        "(claim_id,bundle_digest,receipt_id) VALUES($1,$2,$3)",
                        claim,
                        digest,
                        receipt,
                    )
                    receipts.append(receipt)
        async with pool.acquire() as committed:
            observed = await committed.fetchval(
                "SELECT count(*) FROM chronicler.location_native_processing_finished "
                "WHERE receipt_id=ANY($1::uuid[])",
                receipts,
            )
        if observed != len(receipts):
            raise PolicyUnavailableError("Committed native processing completion is unknown")


async def lock_claim(pool: Any, conn: Any) -> None:
    """Policy lock precedes every native episode/slot lease lock."""
    binding = _processing.get()
    if binding is None:
        return
    if not binding.active or binding.pool is not pool:
        raise PolicyUnavailableError("Native processing writer differs")
    from butlers.chronicler.location_memory_copies import _lock

    await _lock(conn, binding.schema, binding.role)


async def capture_claim(conn: Any, episodes: list[Any], facts=None, rules=None) -> None:
    binding = _processing.get()
    if binding is None or not episodes:
        return
    if not binding.active:
        raise PolicyUnavailableError("Native processing lifetime ended")
    from butlers.chronicler.location_projection import _digest_value

    ids = [UUID(str(row["id"])) for row in episodes]
    parents = await conn.fetch(
        "SELECT DISTINCT p.copy_generation,p.input_digest "
        "FROM chronicler.location_native_memory_commits c "
        "JOIN chronicler.location_native_memory_parents p USING(reservation_id) "
        "WHERE c.episode_id=ANY($1::uuid[])",
        ids,
    )
    # Dedup facts/rules can themselves be prior location descendants. Capture
    # their actual native ancestry too, rather than inferring it from text.
    for table, selected in (("facts", facts or []), ("rules", rules or [])):
        parents = list(parents) + list(
            await conn.fetch(
                "SELECT DISTINCT b.copy_generation,b.input_digest "
                "FROM chronicler.location_native_memory_artifacts a "
                "JOIN chronicler.location_native_dispatch_parents d USING(input_generation) "
                "JOIN chronicler.location_native_copy_births b USING(copy_generation) "
                "WHERE a.memory_table=$1 AND a.artifact_id=ANY($2::uuid[])",
                table,
                [UUID(str(row["id"])) for row in selected],
            )
        )
    unique = {(p["copy_generation"], p["input_digest"]) for p in parents}
    if not unique:
        return  # Independent processing has no invented location ancestry.
    for parent, _ in sorted(unique):
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_dispositions "
            "WHERE copy_generation=$1)",
            parent,
        ):
            raise PolicyUnavailableError("Native processing input was disposed")
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_births b "
            "JOIN chronicler.location_retention_plan_outputs o USING(output_kind,output_id) "
            "JOIN chronicler.location_retention_frontiers f USING(decision_id) "
            "WHERE b.copy_generation=$1)",
            parent,
        ):
            raise PolicyUnavailableError("Native processing input is fenced")
    digest = content_digest(
        {
            "processing_bundle": _digest_value(
                {
                    "episodes": [dict(row) for row in episodes],
                    "facts": facts or [],
                    "rules": rules or [],
                }
            )
        }
    )
    claim = uuid4()
    await conn.execute(
        "INSERT INTO chronicler.location_native_processing_claims "
        "(claim_id,bundle_digest,parent_count) VALUES($1,$2,$3)",
        claim,
        digest,
        len(unique),
    )
    for parent, source_digest in sorted(unique):
        await conn.execute(
            "INSERT INTO chronicler.location_native_processing_parents "
            "(claim_id,copy_generation,input_digest) VALUES($1,$2,$3)",
            claim,
            parent,
            source_digest,
        )
    binding.claims.append((claim, digest))


async def verify_claims(pool: Any) -> None:
    binding = _processing.get()
    if binding is None:
        return
    if not binding.active or binding.pool is not pool:
        raise PolicyUnavailableError("Native processing readback differs")
    async with pool.acquire() as committed:
        for claim, digest in binding.claims:
            if (
                await committed.fetchval(
                    "SELECT count(*)=c.parent_count FROM "
                    "chronicler.location_native_processing_claims c JOIN "
                    "chronicler.location_native_processing_parents p USING(claim_id) "
                    "WHERE c.claim_id=$1 AND c.bundle_digest=$2 GROUP BY c.parent_count",
                    claim,
                    digest,
                )
                is not True
            ):
                raise PolicyUnavailableError("Committed processing input is unknown")


async def read_dedup_bundle(pool: Any, episodes: list[dict], butler: str, tenant: str):
    """Capture the actual full read under its owning locks before rendering."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await lock_claim(pool, conn)
            facts = [
                dict(row)
                for row in await conn.fetch(
                    "SELECT id,subject,predicate,content,permanence,entity_id,valid_at FROM facts "
                    "WHERE validity IN ('active','fading') AND source_butler=$1 AND tenant_id=$2 "
                    "ORDER BY created_at DESC LIMIT 100",
                    butler,
                    tenant,
                )
            ]
            rules = [
                dict(row)
                for row in await conn.fetch(
                    "SELECT id,content,maturity FROM rules WHERE maturity NOT IN ('anti_pattern') "
                    "AND (metadata->>'forgotten')::boolean IS NOT TRUE "
                    "AND source_butler=$1 AND tenant_id=$2 ORDER BY created_at DESC LIMIT 50",
                    butler,
                    tenant,
                )
            ]
            await capture_claim(conn, episodes, facts, rules)
    await verify_claims(pool)
    return facts, rules
