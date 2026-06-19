"""Entity resolution helper for relationship butler SPO fact writes.

Resolves a contact_id or entity UUID to its canonical entity_id in
``public.entities`` without reading ``public.contacts``.

Resolution order (``public.contacts`` is intentionally not queried —
contacts-schema retirement, Phase 7, driving toward bu-y6o7q DROP):

1. ``relationship.contacts_source_links`` — looks up ``local_entity_id`` by
   ``local_contact_id`` for externally-synced contacts (Google, Telegram).
   Raises ``ValueError`` if a link is found but ``local_entity_id`` is NULL
   (data integrity issue: every source-linked contact must carry an entity
   anchor after the bu-tzyuh / contacts_003 migration).
   Skipped gracefully if the contacts module is not loaded (``UndefinedTableError``).

2. ``relationship.contact_entity_map`` (rel_029) — covers ALL contacts including
   manually-created CRM contacts.  ``contact_create`` populates this table at
   creation time; rel_029 backfills existing contacts from ``public.contacts`` at
   migration time.

3. ``public.entities`` — treats the input UUID as an entity_id directly.
   This handles callers that already hold an entity UUID (entity-first
   contacts post-bu-tzyuh, or callers migrated to pass entity_id).

Returns ``None`` when the UUID is not found via any path.  Callers should
treat ``None`` as "identity not yet resolved" and continue gracefully; facts
stored with ``entity_id=None`` will not appear on the entity detail page but
the data is otherwise preserved.

Schema-qualification note
-------------------------
SQL below uses unqualified table names (``contacts_source_links``,
``contact_entity_map``).  This is intentional:

- In production the asyncpg pool's ``search_path`` is set to
  ``relationship,public`` (see ``butlers.db.schema_search_path``), so
  unqualified names resolve to the ``relationship`` schema tables correctly.
- Integration tests (e.g. ``tests/features/test_vcard.py``) run the
  relationship migration chain without a schema override, landing all tables in
  ``public``.  Hard-coding ``relationship.`` would break those tests while
  providing no benefit in production where search_path already handles it.

``UndefinedTableError`` catches here mean "table does not exist" (contacts
module not loaded, or rel_029 not yet applied), not "table not in search_path".
"""

from __future__ import annotations

import logging
import uuid

import asyncpg

logger = logging.getLogger(__name__)


async def resolve_contact_entity_id(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
) -> uuid.UUID | None:
    """Return the entity_id for *contact_id*, or None if unavailable.

    See module docstring for resolution order.

    Raises ``ValueError`` only when a ``contacts_source_links`` row exists for
    the contact but ``local_entity_id`` is NULL — a data integrity issue that
    should be investigated (the backfill migration contacts_003 populates this
    column from existing ``public.contacts`` rows).
    """
    # ------------------------------------------------------------------
    # Step 1: contacts_source_links (externally-synced contacts)
    # Unqualified — search_path handles schema resolution; see module docstring.
    # Skipped gracefully if the contacts module is not loaded.
    # ------------------------------------------------------------------
    try:
        row = await pool.fetchrow(
            """
            SELECT local_entity_id
            FROM contacts_source_links
            WHERE local_contact_id = $1
              AND deleted_at IS NULL
            LIMIT 1
            """,
            contact_id,
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        # contacts module not loaded or contacts_003 not applied yet;
        # fall through to contact_entity_map.
        row = None
    except asyncpg.PostgresError:
        return None

    if row is not None:
        entity_id = row["local_entity_id"]
        if entity_id is None:
            raise ValueError(
                f"Contact {contact_id} has a source link but no linked entity_id. "
                "All source-linked contacts must resolve to an entity — "
                "this is a data integrity issue (contacts_003 backfill may be pending)."
            )
        return entity_id

    # ------------------------------------------------------------------
    # Step 2: contact_entity_map (ALL contacts — CRM + synced, rel_029)
    # Unqualified — search_path handles schema resolution; see module docstring.
    # Populated by contact_create at write time; backfilled by rel_029.
    # Skipped gracefully if rel_029 migration has not been applied yet.
    # ------------------------------------------------------------------
    try:
        map_row = await pool.fetchrow(
            "SELECT entity_id FROM contact_entity_map WHERE contact_id = $1",
            contact_id,
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        map_row = None
    except asyncpg.PostgresError:
        return None

    if map_row is not None:
        return map_row["entity_id"]

    # ------------------------------------------------------------------
    # Step 3: direct entity_id pass-through
    # Handles callers that already hold an entity UUID (entity-first
    # contacts or migrated call sites that now pass entity_id).
    # ------------------------------------------------------------------
    try:
        entity_row = await pool.fetchrow(
            "SELECT id FROM public.entities WHERE id = $1",
            contact_id,
        )
    except asyncpg.PostgresError:
        return None

    if entity_row is not None:
        return entity_row["id"]

    return None
