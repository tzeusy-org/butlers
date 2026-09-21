"""Subscriber-local entity-reference rebinds and durable receipt updates.

Every call uses the caller's schema-scoped pool.  The only shared write is to
``public.memory_catalog`` (the fleet discovery projection) and the public
receipt ledger; this module never opens or names a sibling butler schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from butlers.entity_fact_repoint import repoint_facts_on_conn

logger = logging.getLogger(__name__)

ENTITY_REBOUND_EVENT_TYPE = "entity.rebound.v1"


@dataclass(frozen=True)
class EntityRebindReceipt:
    rebind_id: UUID
    target_schema: str
    references_rebound: int
    status: str
    error_class: str | None = None


def _rowcount(command_tag: Any) -> int:
    try:
        return int(str(command_tag).rsplit(" ", 1)[-1])
    except (TypeError, ValueError):
        return 0


async def _run_optional_table_step(conn: Any, operation: Any) -> tuple[int, bool]:
    """Run one local-table step, distinguishing absence from real failure."""
    try:
        # Each optional table gets a savepoint. PostgreSQL aborts the current
        # transaction on UndefinedTable, so catching without this nested
        # transaction would make receipt settlement impossible.
        async with conn.transaction():
            return int(await operation(conn)), True
    except asyncpg.UndefinedTableError:
        return 0, False


async def _repoint_facts(conn: Any, source: UUID, target: UUID) -> int:
    counts = await repoint_facts_on_conn(conn, source, target)
    return sum(counts.values())


async def _repoint_calendar(conn: Any, source: UUID, target: UUID) -> int:
    deleted = await conn.execute(
        """
        DELETE FROM calendar_event_entities src
        WHERE src.entity_id = $1
          AND EXISTS (
              SELECT 1 FROM calendar_event_entities tgt
              WHERE tgt.event_id = src.event_id AND tgt.entity_id = $2
          )
        """,
        source,
        target,
    )
    updated = await conn.execute(
        "UPDATE calendar_event_entities SET entity_id = $2 WHERE entity_id = $1",
        source,
        target,
    )
    return _rowcount(deleted) + _rowcount(updated)


async def _repoint_episodes(conn: Any, source: UUID, target: UUID) -> int:
    await conn.execute(
        """
        UPDATE episode_entities tgt
        SET role = src.role
        FROM episode_entities src
        WHERE src.episode_id = tgt.episode_id
          AND src.entity_id = $1
          AND tgt.entity_id = $2
          AND CASE src.role
                WHEN 'owner' THEN 0 WHEN 'organizer' THEN 1
                WHEN 'participant' THEN 2 ELSE 99 END
              < CASE tgt.role
                WHEN 'owner' THEN 0 WHEN 'organizer' THEN 1
                WHEN 'participant' THEN 2 ELSE 99 END
        """,
        source,
        target,
    )
    deleted = await conn.execute(
        """
        DELETE FROM episode_entities src
        WHERE src.entity_id = $1
          AND EXISTS (
              SELECT 1 FROM episode_entities tgt
              WHERE tgt.episode_id = src.episode_id AND tgt.entity_id = $2
          )
        """,
        source,
        target,
    )
    updated = await conn.execute(
        "UPDATE episode_entities SET entity_id = $2 WHERE entity_id = $1",
        source,
        target,
    )
    return _rowcount(deleted) + _rowcount(updated)


async def _repoint_catalog(conn: Any, source: UUID, target: UUID, target_schema: str) -> int:
    tag = await conn.execute(
        """
        UPDATE public.memory_catalog
        SET entity_id = CASE WHEN entity_id = $1 THEN $2 ELSE entity_id END,
            object_entity_id = CASE
                WHEN object_entity_id = $1 THEN $2 ELSE object_entity_id END
        WHERE source_schema = $3
          AND (entity_id = $1 OR object_entity_id = $1)
        """,
        source,
        target,
        target_schema,
    )
    return _rowcount(tag)


def _receipt_from_row(row: Any) -> EntityRebindReceipt:
    return EntityRebindReceipt(
        rebind_id=row["rebind_id"],
        target_schema=row["target_schema"],
        references_rebound=row["references_rebound"],
        status=row["status"],
        error_class=row["error_class"],
    )


async def _write_receipt(
    conn: Any,
    *,
    rebind_id: UUID,
    source: UUID,
    target: UUID,
    target_schema: str,
    references_rebound: int,
    status: str,
    error_class: str | None,
) -> EntityRebindReceipt:
    row = await conn.fetchrow(
        """
        UPDATE public.entity_rebind_log
        SET references_rebound = $5,
            status = $6,
            error_class = $7,
            completed_at = now(),
            updated_at = now()
        WHERE rebind_id = $1
          AND source_entity_id = $2
          AND target_entity_id = $3
          AND target_schema = $4
          AND status = 'pending'
        RETURNING rebind_id, target_schema, references_rebound, status, error_class
        """,
        rebind_id,
        source,
        target,
        target_schema,
        references_rebound,
        status,
        error_class,
    )
    if row is None:
        raise RuntimeError("entity rebind receipt could not be settled")
    return _receipt_from_row(row)


async def _rebind_entity_references_on_conn(
    conn: Any,
    *,
    rebind_id: UUID,
    source_entity_id: UUID,
    target_entity_id: UUID,
    target_schema: str,
) -> EntityRebindReceipt:
    async with conn.transaction():
        # This row lock is the claim. A concurrent startup replay or live event
        # waits here, then observes the settled row and returns its truthful
        # count instead of running again and overwriting it with zero.
        row = await conn.fetchrow(
            """
            SELECT rebind_id, source_entity_id, target_entity_id, target_schema,
                   references_rebound, status, error_class
            FROM public.entity_rebind_log
            WHERE rebind_id = $1 AND target_schema = $2
            FOR UPDATE
            """,
            rebind_id,
            target_schema,
        )
        if row is None:
            raise RuntimeError("entity rebind receipt does not exist")
        if (
            row["source_entity_id"] != source_entity_id
            or row["target_entity_id"] != target_entity_id
        ):
            raise RuntimeError("entity rebind receipt identity mismatch")
        if row["status"] != "pending":
            return _receipt_from_row(row)

        total = int(row["references_rebound"])
        tables_seen = 0
        try:
            for operation in (_repoint_facts, _repoint_calendar, _repoint_episodes):
                count, table_exists = await _run_optional_table_step(
                    conn,
                    lambda step_conn, operation=operation: operation(
                        step_conn, source_entity_id, target_entity_id
                    ),
                )
                total += count
                tables_seen += int(table_exists)

            catalog_count, catalog_exists = await _run_optional_table_step(
                conn,
                lambda step_conn: _repoint_catalog(
                    step_conn,
                    source_entity_id,
                    target_entity_id,
                    target_schema,
                ),
            )
            total += catalog_count
            tables_seen += int(catalog_exists)
        except Exception as exc:
            logger.exception("Entity rebind failed for schema %s", target_schema)
            return await _write_receipt(
                conn,
                rebind_id=rebind_id,
                source=source_entity_id,
                target=target_entity_id,
                target_schema=target_schema,
                references_rebound=total,
                status="failed",
                error_class=type(exc).__name__,
            )

        status = "active" if tables_seen or total else "skipped_no_table"
        return await _write_receipt(
            conn,
            rebind_id=rebind_id,
            source=source_entity_id,
            target=target_entity_id,
            target_schema=target_schema,
            references_rebound=total,
            status=status,
            error_class=None if status == "active" else "UndefinedTableError",
        )


async def rebind_entity_references(
    pool: Any,
    *,
    rebind_id: UUID,
    source_entity_id: UUID,
    target_entity_id: UUID,
    target_schema: str,
) -> EntityRebindReceipt:
    """Rebind references owned by ``target_schema`` and settle its receipt."""
    async with pool.acquire() as conn:
        return await _rebind_entity_references_on_conn(
            conn,
            rebind_id=rebind_id,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            target_schema=target_schema,
        )


async def process_pending_entity_rebinds(
    pool: Any, *, target_schema: str
) -> list[EntityRebindReceipt]:
    """Replay this schema's pending receipts during daemon startup."""
    async with pool.acquire() as conn:
        return await _process_pending_entity_rebinds_on_conn(
            conn,
            target_schema=target_schema,
        )


async def _process_pending_entity_rebinds_on_conn(
    conn: Any, *, target_schema: str
) -> list[EntityRebindReceipt]:
    """Replay pending receipts on an already-retained schema connection."""
    rows = await conn.fetch(
        """
        SELECT rebind_id, source_entity_id, target_entity_id
        FROM public.entity_rebind_log
        WHERE target_schema = $1 AND status = 'pending'
        ORDER BY created_at, rebind_id
        """,
        target_schema,
    )
    return [
        await _rebind_entity_references_on_conn(
            conn,
            rebind_id=row["rebind_id"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            target_schema=target_schema,
        )
        for row in rows
    ]


async def run_entity_rebind_listener(
    pool: Any,
    *,
    target_schema: str,
    health_poll_interval_s: float = 5.0,
    reconnect_delay_s: float = 1.0,
    ready_event: asyncio.Event | None = None,
) -> None:
    """React to live ``entity.rebound.v1`` events for one local schema.

    LISTEN is connection-scoped, so each attempt retains one pool connection.
    Registration precedes the recovery drain: events committed during replay
    are queued, closing the drain-before-LISTEN loss window. A failed retained
    connection is reacquired and replayed before live processing resumes.
    """
    from butlers.fleet_events import FLEET_EVENTS_CHANNEL

    queue: asyncio.Queue[UUID] = asyncio.Queue()

    def _on_notify(_conn: Any, _pid: int, channel: str, payload: str) -> None:
        if channel != FLEET_EVENTS_CHANNEL:
            return
        try:
            envelope = json.loads(payload)
            if envelope.get("type") != ENTITY_REBOUND_EVENT_TYPE:
                return
            rebind_id = UUID(str(envelope["data"]["rebind_id"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Malformed entity rebind fleet event dropped")
            return
        queue.put_nowait(rebind_id)

    first_attempt = True
    while True:
        try:
            async with pool.acquire() as conn:
                await conn.add_listener(FLEET_EVENTS_CHANNEL, _on_notify)
                try:
                    await _process_pending_entity_rebinds_on_conn(
                        conn,
                        target_schema=target_schema,
                    )
                    if ready_event is not None and not ready_event.is_set():
                        ready_event.set()
                    first_attempt = False

                    while True:
                        try:
                            rebind_id = await asyncio.wait_for(
                                queue.get(), timeout=health_poll_interval_s
                            )
                        except TimeoutError:
                            if conn.is_closed():
                                raise RuntimeError("entity rebind listener connection closed")
                            continue

                        row = await conn.fetchrow(
                            """
                            SELECT source_entity_id, target_entity_id
                            FROM public.entity_rebind_log
                            WHERE rebind_id = $1 AND target_schema = $2
                            """,
                            rebind_id,
                            target_schema,
                        )
                        if row is None:
                            continue
                        await _rebind_entity_references_on_conn(
                            conn,
                            rebind_id=rebind_id,
                            source_entity_id=row["source_entity_id"],
                            target_entity_id=row["target_entity_id"],
                            target_schema=target_schema,
                        )
                finally:
                    if not conn.is_closed():
                        try:
                            await conn.remove_listener(FLEET_EVENTS_CHANNEL, _on_notify)
                        except Exception:
                            logger.debug("Entity rebind listener cleanup failed", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Entity rebind listener failed for schema %s; reconnecting",
                target_schema,
                exc_info=True,
            )
        finally:
            if first_attempt and ready_event is not None and not ready_event.is_set():
                # Startup must not hang on a failed first attempt. The retained
                # supervisor keeps retrying and each reconnect drains the ledger.
                ready_event.set()
                first_attempt = False

        await asyncio.sleep(reconnect_delay_s)


__all__ = [
    "ENTITY_REBOUND_EVENT_TYPE",
    "EntityRebindReceipt",
    "process_pending_entity_rebinds",
    "rebind_entity_references",
    "run_entity_rebind_listener",
]
