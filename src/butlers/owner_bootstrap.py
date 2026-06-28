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
                    if owner_entity_id is None:
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
                    else:
                        # Mirror the owner's entity_info telegram_chat_id into a
                        # resolvable has-handle triple (see _seed_owner_telegram_handle).
                        await _seed_owner_telegram_handle(conn, owner_entity_id)

    except Exception:  # noqa: BLE001
        logger.warning("Owner entity bootstrap skipped (non-fatal)", exc_info=True)


async def _seed_owner_telegram_handle(
    conn: asyncpg.Connection,
    owner_entity_id: object,
) -> None:
    """Mirror the owner's Telegram chat id into a resolvable ``has-handle`` triple.

    The owner registers their Telegram identity through the dashboard, which
    writes a non-secured ``telegram_chat_id`` row to ``public.entity_info``.  But
    identity reverse-resolution and the approval gate's owner auto-approve bypass
    read ``relationship.entity_facts`` (``has-handle`` triples), NOT
    ``entity_info`` — so the owner is invisible to those paths and their own
    notifications get parked for approval and never delivered.

    This seeds the canonical ``telegram:<chat_id>`` handle (primary) into
    ``entity_facts`` directly, bypassing the RFC-0017 owner carve-out (which would
    otherwise park this write for human approval and leave the owner unresolvable
    indefinitely).  Idempotent: skipped when the triple already exists (partial
    unique index ``uq_ef_spo_active``) or when the prerequisite tables / chat-id
    row are absent.
    """
    try:
        tables_ready = await conn.fetchval(
            """
            SELECT CASE
                WHEN current_schema() = 'relationship' THEN
                    to_regclass('relationship.entity_facts') IS NOT NULL
                    AND to_regclass('public.entity_info') IS NOT NULL
                ELSE false
            END
            """
        )
        if not tables_ready:
            return

        chat_id = await conn.fetchval(
            """
            SELECT value FROM public.entity_info
            WHERE entity_id = $1 AND type = 'telegram_chat_id'
              AND value IS NOT NULL AND value <> ''
            LIMIT 1
            """,
            owner_entity_id,
        )
        if not chat_id:
            return

        handle = f"telegram:{str(chat_id).strip()}"
        await conn.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, src, "primary", verified, validity)
            VALUES ($1, 'has-handle', $2, 'literal', 'owner-bootstrap', true, true, 'active')
            ON CONFLICT DO NOTHING
            """,
            owner_entity_id,
            handle,
        )
    except asyncpg.InsufficientPrivilegeError:
        return
    except Exception:  # noqa: BLE001
        logger.warning("Owner Telegram handle seed skipped (non-fatal)", exc_info=True)
