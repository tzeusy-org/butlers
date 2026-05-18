"""entity_facts: triple-store for contact and relational predicates.

Revision ID: rel_013
Revises: rel_012
Create Date: 2026-05-18 00:00:00.000000

Phase: entity-redesign (bu-892tf).

Creates ``relationship.entity_facts`` — the canonical RDF (subject-predicate-object)
triple store owned by the relationship butler.  This table supersedes
``public.contact_info`` as the canonical channel-identity registry and
introduces a single, unified store for both contact predicates (``has-email``,
``has-phone``, etc.) and relational predicates (``knows``, ``family-of``, etc.)
per ``specs/relationship-facts/spec.md``.

Schema
------
id          UUID PK       gen_random_uuid()
subject     UUID NOT NULL FK → public.entities(id)
predicate   TEXT NOT NULL From relationship.entity_predicate_registry
object      TEXT NOT NULL Literal value or entity_id::text
object_kind TEXT NOT NULL 'literal' | 'entity'
src         TEXT NOT NULL Authoring butler slug
conf        FLOAT         0..1 confidence (DEFAULT 1.0)
last_seen   TIMESTAMPTZ   NULL — populated by ingestion jobs
weight      INT           NULL — relational aggregation weight
verified    BOOL          Owner-confirmed (DEFAULT false)
"primary"   BOOL          NULL — primary-of-kind for multi-valued preds
validity    TEXT NOT NULL 'active' | 'retracted' | 'superseded' (DEFAULT 'active')
created_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now()

Indexes
-------
idx_ef_subject_predicate        (subject, predicate)
idx_ef_predicate_object_literal (predicate, object) WHERE object_kind='literal'
idx_ef_predicate_active         (predicate) WHERE validity='active'
idx_ef_last_seen                (last_seen DESC)
idx_ef_subject_has_active       (subject) WHERE validity='active' AND predicate LIKE 'has-%'

Uniqueness (partial index)
--------------------------
uq_ef_spo_active  UNIQUE (subject, predicate, object) WHERE validity='active'

This partial index supports the central writer's idempotency contract
(Amendment 14): ``INSERT … ON CONFLICT (subject, predicate, object)
WHERE validity='active' DO UPDATE``. Tombstoned rows (validity != 'active')
are excluded from the constraint, allowing re-assertion of retracted triples.

Schema creation
---------------
The migration ensures ``relationship`` schema exists before creating any
objects.  Downgrade drops the table (indexes cascade) but does NOT drop the
schema, because the schema may be in use by other relationship-butler tables
that land in later migrations (e.g. predicate_registry, credentials).

Grants
------
SELECT, INSERT, UPDATE, DELETE on relationship.entity_facts granted to
butler_relationship_rw only.  Other butlers access facts exclusively through
the relationship butler's MCP tool surface (RFC 0006 schema isolation).
"""

from __future__ import annotations

from alembic import op

revision = "rel_013"
down_revision = "rel_012"
branch_labels = None
depends_on = None

_RELATIONSHIP_ROLE = "butler_relationship_rw"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_TABLE_FQN = "relationship.entity_facts"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object      THEN NULL;
            WHEN undefined_table       THEN NULL;
            WHEN invalid_schema_name   THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # 1. Ensure schema exists (idempotent — other rel migrations may have
    #    already created it, or this may be the first to land).
    op.execute("CREATE SCHEMA IF NOT EXISTS relationship")

    # 2. Create the facts table.
    op.execute("""
        CREATE TABLE IF NOT EXISTS relationship.entity_facts (
            id          UUID        NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
            subject     UUID        NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
            predicate   TEXT        NOT NULL,
            object      TEXT        NOT NULL,
            object_kind TEXT        NOT NULL CHECK (object_kind IN ('literal', 'entity')),
            src         TEXT        NOT NULL,
            conf        FLOAT       NOT NULL DEFAULT 1.0 CHECK (conf >= 0.0 AND conf <= 1.0),
            last_seen   TIMESTAMPTZ,
            weight      INT,
            verified    BOOL        NOT NULL DEFAULT false,
            "primary"   BOOL,
            validity    TEXT        NOT NULL DEFAULT 'active'
                            CHECK (validity IN ('active', 'retracted', 'superseded')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # 3. Indexes per spec §"Indexes (required)".

    # Primary access pattern — outbound fact lookup for a subject.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ef_subject_predicate
            ON relationship.entity_facts (subject, predicate)
    """)

    # Reverse-lookup for ingestion routing:
    # "incoming Telegram chat 12345 → which entity"
    # Partial on object_kind='literal' keeps the index tight.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ef_predicate_object_literal
            ON relationship.entity_facts (predicate, object)
            WHERE object_kind = 'literal'
    """)

    # Concentration aggregation — enumerate all active facts for a predicate.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ef_predicate_active
            ON relationship.entity_facts (predicate)
            WHERE validity = 'active'
    """)

    # Stale detection and Finder tie-break.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ef_last_seen
            ON relationship.entity_facts (last_seen DESC)
    """)

    # Contacts endpoint — all active has-* facts for a subject.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ef_subject_has_active
            ON relationship.entity_facts (subject)
            WHERE validity = 'active'
              AND predicate LIKE 'has-%'
    """)

    # 4. Uniqueness partial index (Amendment 14 idempotency contract).
    # Supports: INSERT … ON CONFLICT (subject, predicate, object)
    #           WHERE validity='active' DO UPDATE
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_ef_spo_active
            ON relationship.entity_facts (subject, predicate, object)
            WHERE validity = 'active'
    """)

    # 5. Grants — relationship butler only (RFC 0006 schema isolation).
    _grant_best_effort(_TABLE_FQN, _TABLE_PRIVILEGES, _RELATIONSHIP_ROLE)


def downgrade() -> None:
    # Drop indexes explicitly (they cascade with DROP TABLE, but being
    # explicit is clearer and consistent with the upgrade path).
    op.execute("DROP INDEX IF EXISTS relationship.uq_ef_spo_active")
    op.execute("DROP INDEX IF EXISTS relationship.idx_ef_subject_has_active")
    op.execute("DROP INDEX IF EXISTS relationship.idx_ef_last_seen")
    op.execute("DROP INDEX IF EXISTS relationship.idx_ef_predicate_active")
    op.execute("DROP INDEX IF EXISTS relationship.idx_ef_predicate_object_literal")
    op.execute("DROP INDEX IF EXISTS relationship.idx_ef_subject_predicate")
    op.execute("DROP TABLE IF EXISTS relationship.entity_facts")
    # NOTE: we intentionally do NOT drop the relationship schema here.
    # Other relationship-butler tables (predicate_registry, credentials) may
    # coexist in the schema, and this migration does not own them.
