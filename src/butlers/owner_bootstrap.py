"""Owner entity bootstrap for the Butler daemon.

Ensures the owner entity exists in public.entities on daemon startup.
This is idempotent and safe to call at any point during initialization.
"""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


async def _ensure_owner_entity(pool: asyncpg.Pool) -> None:
    """Bootstrap the owner entity (idempotent).

    1. Create owner entity in public.entities with roles=['owner'] (if table exists).

    Safe to call if:
    - public.entities does not yet exist (skips silently)
    - owner entity already exists (ON CONFLICT DO NOTHING)
    - migration has not yet run (graceful no-op)
    """
    try:
        async with pool.acquire() as conn:
            # ------------------------------------------------------------------
            # Phase 1: Ensure owner entity in public.entities
            # ------------------------------------------------------------------
            entities_table_exists = await conn.fetchval(
                "SELECT to_regclass('public.entities') IS NOT NULL"
            )

            if entities_table_exists:
                roles_on_entities = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'entities'
                          AND column_name = 'roles'
                    )
                    """
                )
                if roles_on_entities:
                    owner_entity_id = await conn.fetchval(
                        """
                        SELECT id FROM public.entities
                        WHERE 'owner' = ANY(roles)
                        LIMIT 1
                        """
                    )
                    if owner_entity_id is not None:
                        return

                    owner_entity_id = await conn.fetchval(
                        """
                        INSERT INTO public.entities
                            (canonical_name, entity_type, roles)
                        VALUES ('Owner', 'person', $1)
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """,
                        ["owner"],
                    )
                    if owner_entity_id is None:
                        # ON CONFLICT hit (entity already exists); fetch the
                        # existing id so callers always have a valid reference.
                        owner_entity_id = await conn.fetchval(
                            """
                            SELECT id FROM public.entities
                            WHERE 'owner' = ANY(roles)
                            LIMIT 1
                            """
                        )
                    if owner_entity_id is None:
                        logger.warning(
                            "Owner entity not found after insert attempt — "
                            "bootstrap may be incomplete"
                        )

    except Exception:  # noqa: BLE001
        logger.warning("Owner entity bootstrap skipped (non-fatal)", exc_info=True)
