"""Sweep that keeps Chronicler's public projection tables equal to their source view.

``chronicler_031`` holds the rules: ``v_episodes_public_source`` defines the rows, and a trigger
rewrites them in each override's transaction. This sweep adds new episodes, repairs drift and
removes rows whose episode left the view (RFC 0039 D2, REQ-public-mcp-projection-006).
Deterministic, no LLM. ``category`` is the one value only Python knows, so it is resolved here and
passed in.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from butlers.chronicler.aggregations import category_for

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:chronicler:public-projection', 0))"


async def sweep_public_projection(pool: asyncpg.Pool) -> dict[str, Any]:
    """Rewrite ``episodes_public`` and ``rollups_public`` from the source view."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            # The lock comes first: each later statement then reads every override committed
            # before it was granted, and none can commit until this transaction ends.
            await conn.execute(_LOCK)
            triples = await conn.fetch(
                "SELECT DISTINCT source_name, kind, trigger_source FROM v_episodes_public_source"
            )
            upserted = await conn.fetchval(
                """
                WITH categories AS (
                    SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::text[])
                        AS c(source_name, kind, trigger_source, category)
                ),
                written AS (
                    INSERT INTO episodes_public AS p (
                        id, started_at, ended_at, kind, title, category,
                        effective_privacy, tombstoned
                    )
                    SELECT s.id, s.started_at, s.ended_at, s.kind, s.title, c.category,
                           s.effective_privacy, s.tombstoned
                    FROM v_episodes_public_source AS s
                    JOIN categories AS c
                      ON c.source_name = s.source_name AND c.kind = s.kind
                     AND c.trigger_source IS NOT DISTINCT FROM s.trigger_source
                    ON CONFLICT (id) DO UPDATE
                    SET started_at = EXCLUDED.started_at,
                        ended_at = EXCLUDED.ended_at,
                        kind = EXCLUDED.kind,
                        title = EXCLUDED.title,
                        category = EXCLUDED.category,
                        effective_privacy = EXCLUDED.effective_privacy,
                        tombstoned = EXCLUDED.tombstoned
                    WHERE (p.started_at, p.ended_at, p.kind, p.title, p.category,
                           p.effective_privacy, p.tombstoned)
                          IS DISTINCT FROM
                          (EXCLUDED.started_at, EXCLUDED.ended_at, EXCLUDED.kind, EXCLUDED.title,
                           EXCLUDED.category, EXCLUDED.effective_privacy, EXCLUDED.tombstoned)
                    RETURNING 1
                )
                SELECT count(*) FROM written
                """,
                [row["source_name"] for row in triples],
                [row["kind"] for row in triples],
                [row["trigger_source"] for row in triples],
                [
                    category_for(
                        row["source_name"], row["kind"], trigger_source=row["trigger_source"]
                    )
                    for row in triples
                ],
            )
            removed = await conn.fetchval(
                """
                WITH gone AS (
                    DELETE FROM episodes_public AS p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM v_episodes_public_source AS s WHERE s.id = p.id
                    )
                    RETURNING 1
                )
                SELECT count(*) FROM gone
                """
            )
            await conn.execute("SELECT refresh_rollups_public(NULL, NULL)")
    return {"episodes_written": upserted, "episodes_removed": removed}
