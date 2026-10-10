"""Fixed session read-model copies inherit actual native receiving lineage.

Session UUIDs are locators. The registered owning API pool, policy-first
transaction and stored native births bind the returned bodies. ASGI completion
settles only the server response, never its remote recipient.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from butlers.chronicler.location_retention import PolicyUnavailableError


async def capture_session_rows(
    pool: Any, query: str, args: tuple[Any, ...], *, session_id: UUID | None = None
) -> list[Any]:
    from butlers.chronicler.location_export_lifetime import (
        native_export_request,
        register_native_export,
    )
    from butlers.chronicler.location_projection import _digest_value
    from butlers.chronicler.location_retention import _api_capture_configured
    from butlers.chronicler.storage import _lock_location_writes
    from butlers.location_retention import content_digest

    if not _api_capture_configured(pool):
        return await pool.fetch(query, *args)
    request = native_export_request()
    if request is None:
        raise PolicyUnavailableError("Owning session export lifetime is unavailable")
    generations = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_schema()") != "chronicler":
                raise PolicyUnavailableError("Owning session export schema differs")
            await _lock_location_writes(conn)
            rows = await conn.fetch(query, *args)
            for row in rows:
                sid = session_id if session_id is not None else row["id"]
                parents = await conn.fetch(
                    "SELECT output_kind,output_id,bool_and(lineage_known AND exclusive_input) "
                    "AS known FROM location_native_copy_births WHERE receiving_session=$1 "
                    "GROUP BY output_kind,output_id ORDER BY output_kind,output_id",
                    sid,
                )
                # Legacy bodies do not gain invented lineage from a lookup.
                # The native frontier's opaque-session census retains UNKNOWN.
                if not parents:
                    continue
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_retention_plans p "
                    "JOIN location_retention_plan_outputs o USING(decision_id) "
                    "JOIN location_native_copy_births b USING(output_kind,output_id) "
                    "JOIN location_retention_frontiers f USING(decision_id) "
                    "WHERE b.receiving_session=$1 AND p.state<>'complete')",
                    sid,
                ):
                    raise PolicyUnavailableError("Owning session export generation is fenced")
                generation = uuid4()
                digest = content_digest({"session_export": _digest_value(dict(row))})
                for parent in parents:
                    await conn.execute(
                        "INSERT INTO location_native_copy_births "
                        "(copy_generation,output_kind,output_id,input_digest,lineage_known,"
                        "exclusive_input,producer_kind,receiving_server_request) "
                        "VALUES($1,$2,$3,$4,$5,$5,'api_export',$6)",
                        generation,
                        parent["output_kind"],
                        parent["output_id"],
                        digest,
                        parent["known"] is True,
                        request,
                    )
                generations.append((generation, digest, len(parents)))
    async with pool.acquire() as committed:
        for generation, digest, count in generations:
            observed = await committed.fetchval(
                "SELECT count(*) FROM location_native_copy_births WHERE copy_generation=$1 "
                "AND input_digest=$2 AND receiving_server_request=$3 "
                "AND producer_kind='api_export'",
                generation,
                digest,
                request,
            )
            if observed != count:
                raise PolicyUnavailableError("Committed session export birth is unknown")
            register_native_export(pool, "native_read", generation, digest)
    return rows


async def session_body_fan_out(db: Any, query: str, args=(), *, butler_names=None):
    from butlers.api.db import DatabaseManager

    # Non-database legacy adapters preserve their contract and prove no birth.
    if not isinstance(db, DatabaseManager):
        return await db.fan_out_with_status(query, args, butler_names=butler_names)
    names = db.butler_names if butler_names is None else butler_names
    other = [name for name in names if name != "chronicler"]
    results, failed = await db.fan_out_with_status(query, args, butler_names=other)
    if "chronicler" in names:
        try:
            results["chronicler"] = await capture_session_rows(db.pool("chronicler"), query, args)
        except Exception:
            # Source unavailability is a degraded read, never definitive absence.
            results["chronicler"] = []
            failed.append("chronicler")
    return results, failed
