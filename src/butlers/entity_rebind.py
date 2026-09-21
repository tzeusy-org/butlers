"""Subscriber-local entity-reference rebinds and durable receipt updates.

Every call uses the caller's schema-scoped pool.  The only shared write is to
``public.memory_catalog`` (the fleet discovery projection) and the public
receipt ledger; this module never opens or names a sibling butler schema.
"""

from __future__ import annotations

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


async def _run_optional_table_step(pool: Any, operation: Any) -> tuple[int, bool]:
    """Run one local-table step, distinguishing absence from real failure."""
    try:
        async with pool.acquire() as conn, conn.transaction():
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


async def _write_receipt(
    pool: Any,
    *,
    rebind_id: UUID,
    source: UUID,
    target: UUID,
    target_schema: str,
    references_rebound: int,
    status: str,
    error_class: str | None,
) -> EntityRebindReceipt:
    await pool.execute(
        """
        INSERT INTO public.entity_rebind_log (
            rebind_id, source_entity_id, target_entity_id, target_schema,
            references_rebound, status, error_class, completed_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, now())
        ON CONFLICT (rebind_id, target_schema) DO UPDATE
        SET references_rebound = EXCLUDED.references_rebound,
            status = EXCLUDED.status,
            error_class = EXCLUDED.error_class,
            completed_at = EXCLUDED.completed_at,
            updated_at = now()
        """,
        rebind_id,
        source,
        target,
        target_schema,
        references_rebound,
        status,
        error_class,
    )
    return EntityRebindReceipt(
        rebind_id=rebind_id,
        target_schema=target_schema,
        references_rebound=references_rebound,
        status=status,
        error_class=error_class,
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
    total = 0
    tables_seen = 0
    try:
        for operation in (_repoint_facts, _repoint_calendar, _repoint_episodes):
            count, table_exists = await _run_optional_table_step(
                pool,
                lambda conn, operation=operation: operation(
                    conn, source_entity_id, target_entity_id
                ),
            )
            total += count
            tables_seen += int(table_exists)

        # The catalog is a shared projection, but ownership remains local: a
        # daemon may update only rows attributed to its own source schema.
        catalog_tag = await pool.execute(
            """
            UPDATE public.memory_catalog
            SET entity_id = CASE WHEN entity_id = $1 THEN $2 ELSE entity_id END,
                object_entity_id = CASE
                    WHEN object_entity_id = $1 THEN $2 ELSE object_entity_id END,
                updated_at = now()
            WHERE source_schema = $3
              AND (entity_id = $1 OR object_entity_id = $1)
            """,
            source_entity_id,
            target_entity_id,
            target_schema,
        )
        total += _rowcount(catalog_tag)
    except Exception as exc:
        logger.exception("Entity rebind failed for schema %s", target_schema)
        return await _write_receipt(
            pool,
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
        pool,
        rebind_id=rebind_id,
        source=source_entity_id,
        target=target_entity_id,
        target_schema=target_schema,
        references_rebound=total,
        status=status,
        error_class=None if status == "active" else "UndefinedTableError",
    )


async def process_pending_entity_rebinds(
    pool: Any, *, target_schema: str
) -> list[EntityRebindReceipt]:
    """Replay this schema's pending receipts during daemon startup."""
    rows = await pool.fetch(
        """
        SELECT rebind_id, source_entity_id, target_entity_id
        FROM public.entity_rebind_log
        WHERE target_schema = $1 AND status = 'pending'
        ORDER BY created_at, rebind_id
        """,
        target_schema,
    )
    return [
        await rebind_entity_references(
            pool,
            rebind_id=row["rebind_id"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            target_schema=target_schema,
        )
        for row in rows
    ]


__all__ = [
    "ENTITY_REBOUND_EVENT_TYPE",
    "EntityRebindReceipt",
    "process_pending_entity_rebinds",
    "rebind_entity_references",
]
