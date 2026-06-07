"""home_assistant_persons: mapping table from HA person entity IDs to contacts.

Revision ID: core_116
Revises: core_115
Create Date: 2026-06-07 00:00:00.000000

Creates ``connectors.home_assistant_persons`` — a lookup table that maps
Home Assistant person entity IDs (e.g. ``person.alice``) to
``public.contacts`` rows.

Motivation (bu-v7hen)
---------------------
The Home Assistant history adapter projects ``presence_episode`` rows from
``person.*`` entity state changes.  Unlike every other adapter, the entity
that should be stamped on each episode is NOT the owner — it is the person
whose presence is being tracked.  ``person.alice``'s presence episode should
carry Alice's entity_id, not the owner's.

This table is the resolution source:
  ``connectors.home_assistant_persons.ha_entity_id``  (e.g. ``person.alice``)
  → ``connectors.home_assistant_persons.contact_id``
  → ``public.contacts.entity_id``

The final ``entity_id`` (a UUID in the memory butler's entity graph) is
derived by joining through ``public.contacts``, not stored directly here,
so that entity graph reassignments only need to update ``public.contacts``.

Bootstrap
---------
For a **single-person household** (person maps to the owner contact):

    INSERT INTO connectors.home_assistant_persons (ha_entity_id, contact_id)
    SELECT 'person.alice', id FROM public.contacts WHERE 'owner' = ANY(roles)
    ON CONFLICT DO NOTHING;

For a **multi-person household**, one row per person:

    INSERT INTO connectors.home_assistant_persons (ha_entity_id, contact_id)
    VALUES
        ('person.alice', '<alice_contact_uuid>'),
        ('person.bob',   '<bob_contact_uuid>')
    ON CONFLICT (ha_entity_id) DO UPDATE SET contact_id = EXCLUDED.contact_id;

Unmapped persons (no row in this table) degrade to ``entity_id = NULL`` on
the projected episode — a safe, observable fallback.

Roles
-----
- ``butler_chronicler_rw``: read-only (projection path — reads mapping, never writes).
- Administrator (migration author): INSERT/UPDATE for bootstrap and re-mapping.
  No connector or butler role needs write access; mapping is operator-managed.

RFC 0014 §D8 — explicit per-table grants required for non-superuser roles.
"""

from __future__ import annotations

from alembic import op

revision = "core_116"
down_revision = "core_115"
branch_labels = None
depends_on = None

_CHRONICLER_ROLE = "butler_chronicler_rw"
_TABLE = "connectors.home_assistant_persons"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    """Execute a DDL statement only when the prerequisite role exists.

    Silently skips if the role is missing (non-prod DB without all roles).
    """
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"
    else:
        condition = "TRUE"
    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                {statement};
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create connectors.home_assistant_persons
    # -------------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            ha_entity_id  TEXT        PRIMARY KEY,
            contact_id    UUID        NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Index on contact_id for reverse lookups (is this contact mapped to an HA person?).
    op.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_ha_persons_contact_id
            ON {_TABLE} (contact_id)
    """)

    # -------------------------------------------------------------------------
    # 2. Grants
    # -------------------------------------------------------------------------

    # butler_chronicler_rw: read-only (projection path reads the mapping).
    _execute_best_effort(
        f"GRANT SELECT ON TABLE {_TABLE} TO {_quote_ident(_CHRONICLER_ROLE)}",
        role_name=_CHRONICLER_ROLE,
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")
