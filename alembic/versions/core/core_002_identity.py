"""identity: create public.entities, public.contacts, public.contact_info, public.entity_info

Revision ID: core_002
Revises: core_001
Create Date: 2026-03-26 00:00:00.000000

Collapsed from: core_007, core_011, core_012, core_014, core_017,
                rel_001 (contacts), rel_002e (stay_in_touch_days),
                rel_003 (contacts rework), mem_018/021 (partial unique entities).

Creates the four cross-butler identity tables in the PUBLIC schema:

  1. public.entities   -- canonical entity registry
  2. public.contacts   -- contact registry (FK -> entities)
  3. public.contact_info -- per-channel identifiers (FK -> contacts, self-FK)
  4. public.entity_info  -- entity-level metadata (FK -> entities)

All tables are granted SELECT, INSERT, UPDATE, DELETE to every butler role.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_002"
down_revision = "core_001"
branch_labels = None
depends_on = None

_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

_PUBLIC_TABLES = (
    "public.entities",
    "public.contacts",
    "public.contact_info",
    "public.entity_info",
)


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerates missing role/table."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # --------------------------------------------------------------------- #
    # 1. public.entities
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.entities (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       TEXT NOT NULL,
            canonical_name  VARCHAR NOT NULL,
            entity_type     VARCHAR NOT NULL DEFAULT 'other',
            aliases         TEXT[] NOT NULL DEFAULT '{}',
            metadata        JSONB DEFAULT '{}'::jsonb,
            roles           TEXT[] NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_shared_entities_entity_type CHECK (
                entity_type IN ('person', 'organization', 'place', 'other')
            )
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entities_tenant_canonical
        ON public.entities (tenant_id, canonical_name)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entities_aliases
        ON public.entities USING gin(aliases)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entities_metadata
        ON public.entities USING gin(metadata)
    """)

    # Owner singleton: at most one entity with 'owner' in roles.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_entities_owner_singleton
        ON public.entities ((true))
        WHERE 'owner' = ANY(roles)
    """)

    # Partial unique: prevent duplicate live entities per (tenant, name, type).
    # Excludes tombstoned entities (merged or soft-deleted).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_tenant_canonical_type_live
        ON public.entities (tenant_id, canonical_name, entity_type)
        WHERE (metadata->>'merged_into') IS NULL
          AND (metadata->>'deleted_at') IS NULL
    """)

    # --------------------------------------------------------------------- #
    # 2. public.contacts
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.contacts (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name              TEXT NOT NULL,
            details           JSONB DEFAULT '{}',
            first_name        VARCHAR,
            last_name         VARCHAR,
            nickname          VARCHAR,
            company           VARCHAR,
            job_title         VARCHAR,
            gender            VARCHAR,
            pronouns          VARCHAR,
            avatar_url        VARCHAR,
            listed            BOOLEAN NOT NULL DEFAULT true,
            archived_at       TIMESTAMPTZ,
            metadata          JSONB,
            stay_in_touch_days INTEGER,
            entity_id         UUID REFERENCES public.entities(id) ON DELETE SET NULL,
            preferred_channel VARCHAR
                CONSTRAINT contacts_preferred_channel_check
                CHECK (preferred_channel IN ('telegram', 'email')),
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contacts_name
        ON public.contacts (name)
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ix_contacts_entity_id'
            ) THEN
                CREATE INDEX ix_contacts_entity_id
                ON public.contacts (entity_id)
                WHERE entity_id IS NOT NULL;
            END IF;
        END
        $$;
    """)

    # --------------------------------------------------------------------- #
    # 3. public.contact_info
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.contact_info (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id  UUID NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE,
            type        VARCHAR NOT NULL,
            value       TEXT NOT NULL,
            label       VARCHAR,
            is_primary  BOOLEAN DEFAULT false,
            secured     BOOLEAN NOT NULL DEFAULT false,
            parent_id   UUID REFERENCES public.contact_info(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_shared_contact_info_type_value UNIQUE (type, value)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_contact_info_contact_id
        ON public.contact_info (contact_id)
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE indexname = 'ix_shared_contact_info_parent_id'
            ) THEN
                CREATE INDEX ix_shared_contact_info_parent_id
                ON public.contact_info (parent_id)
                WHERE parent_id IS NOT NULL;
            END IF;
        END
        $$;
    """)

    # --------------------------------------------------------------------- #
    # 4. public.entity_info
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.entity_info (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id   UUID NOT NULL
                REFERENCES public.entities(id) ON DELETE CASCADE,
            type        VARCHAR NOT NULL,
            value       TEXT NOT NULL,
            label       VARCHAR,
            is_primary  BOOLEAN DEFAULT false,
            secured     BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_shared_entity_info_entity_type UNIQUE (entity_id, type)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entity_info_entity_id
        ON public.entity_info (entity_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entity_info_type
        ON public.entity_info (type)
    """)

    # --------------------------------------------------------------------- #
    # 5. Grants
    # --------------------------------------------------------------------- #
    for table in _PUBLIC_TABLES:
        for role in _ALL_BUTLER_ROLES:
            _grant_best_effort(table, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.entity_info CASCADE")
    op.execute("DROP TABLE IF EXISTS public.contact_info CASCADE")
    op.execute("DROP TABLE IF EXISTS public.contacts CASCADE")
    op.execute("DROP TABLE IF EXISTS public.entities CASCADE")
