"""entity_graph_edges: public entity-to-entity graph projection substrate

Revision ID: core_215
Revises: core_214
Create Date: 2026-09-05 00:00:00.000000

Creates ``public.entity_graph_edges`` -- a write-behind projection of
entity-to-entity relationships sourced from each butler's canonical fact
stores (relationship.entity_facts, memory facts/rules, commitments). This is
Slice 1 (substrate + RFC) of bu-8cdl1.8: the table shape, indexes, and grants
land here; the writers that populate it, the backfill job, and the
zero-LLM traversal tools land in later slices. See
about/legends-and-lore/rfcs/0031-public-entity-graph-projection.md for the
full design and slice plan.

Table design:
  - UNIQUE natural key on (source_schema, source_table, source_id) -- one
    projected edge per canonical source row, and the key a future backfill
    job upserts against to stay idempotent.
  - Every row is anchored to a real ``public.entities`` row via
    ``subject_entity_id`` (ON DELETE CASCADE -- an edge with no subject is
    meaningless).
  - A row is either a live edge (``predicate`` + ``object_entity_id`` set,
    ``withheld_reason`` NULL) or a withheld stub (``withheld_reason =
    'sensitivity'``, both payload columns NULL). The
    ``chk_entity_graph_edges_payload_xor_withheld`` constraint makes that
    invariant a schema fact rather than a writer convention, so a
    sensitivity-excluded fact still counts toward graph coverage without
    ever persisting its predicate or target.
  - ``sensitivity`` records the originating fact's tier (reusing the
    memory-catalog vocabulary: normal/pii/confidential) on every row,
    live or withheld, so a future read-time ceiling can filter live edges
    the same way ``public.memory_catalog`` already does.

Grant model:
  All butler roles with a memory/relationship/commitments write surface
  receive SELECT, INSERT, UPDATE, DELETE. Unlike ``public.memory_catalog``
  (centrally GC'd, so DELETE is withheld), each edge here is 1:1 owned by
  the writer that projected it -- the same transaction that retracts or
  deletes the source fact must be able to retract or delete its edge, or
  the projection silently diverges from source data.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_215"
down_revision = "core_214"
branch_labels = None
depends_on = None

# Butler roles with a memory/relationship/commitments write surface that can
# project entity-graph edges. Mirrors the current _ALL_BUTLER_ROLES list from
# core_210_expected_signals.py (the most recently landed canonical set).
_ALL_BUTLER_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_FQN = "public.entity_graph_edges"
_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_if_role_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    safe_table_fqn = table_fqn.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{safe_table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
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


def _grant_schema_usage_if_exists(schema: str, role: str) -> None:
    """GRANT USAGE ON SCHEMA only when schema and role exist."""
    safe_schema = schema.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = '{safe_schema}'
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
            THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {_quote_ident(schema)} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create public.entity_graph_edges.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.entity_graph_edges (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Provenance -- the canonical source row this edge projects.
            source_schema       TEXT NOT NULL,
            source_table        TEXT NOT NULL,
            source_id           UUID NOT NULL,

            -- Graph anchor. The subject is always a real entity; the object
            -- is a real entity only for a live (non-withheld) edge.
            subject_entity_id   UUID NOT NULL
                REFERENCES public.entities(id) ON DELETE CASCADE,
            predicate           TEXT,
            object_entity_id    UUID
                REFERENCES public.entities(id) ON DELETE CASCADE,

            -- Sensitivity tier of the originating fact (reuses the
            -- public.memory_catalog vocabulary). Recorded on every row,
            -- live or withheld.
            sensitivity         TEXT NOT NULL DEFAULT 'normal'
                CHECK (sensitivity IN ('normal', 'pii', 'confidential')),

            -- NULL for a live edge. 'sensitivity' marks a count-only stub:
            -- the fact existed and was excluded from the graph payload, but
            -- its existence still counts toward coverage.
            withheld_reason     TEXT
                CHECK (withheld_reason IN ('sensitivity')),

            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- One projected edge per canonical source row; the natural key
            -- a backfill job upserts against to stay idempotent.
            CONSTRAINT uq_entity_graph_edges_source
                UNIQUE (source_schema, source_table, source_id),

            -- A row is either a live edge (full payload, no withheld
            -- reason) or a withheld stub (no payload, a withheld reason).
            -- No row may be both or neither.
            CONSTRAINT chk_entity_graph_edges_payload_xor_withheld CHECK (
                (withheld_reason IS NULL
                    AND predicate IS NOT NULL
                    AND object_entity_id IS NOT NULL)
                OR
                (withheld_reason IS NOT NULL
                    AND predicate IS NULL
                    AND object_entity_id IS NULL)
            )
        )
    """)

    # -------------------------------------------------------------------------
    # 2. Indexes.
    # -------------------------------------------------------------------------

    # Outbound traversal: walk edges from a subject entity.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_graph_edges_subject
        ON public.entity_graph_edges (subject_entity_id)
    """)

    # Inbound traversal: find edges pointing at an object entity. Partial
    # because withheld stubs never populate object_entity_id.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_graph_edges_object
        ON public.entity_graph_edges (object_entity_id)
        WHERE object_entity_id IS NOT NULL
    """)

    # Coverage counting: how many withheld stubs exist per subject, without
    # scanning live edges.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entity_graph_edges_withheld
        ON public.entity_graph_edges (subject_entity_id)
        WHERE withheld_reason IS NOT NULL
    """)

    # -------------------------------------------------------------------------
    # 3. Grant access to butler roles with a write-behind surface.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_role_exists(_TABLE_FQN, _TABLE_PRIVILEGES, role)
        _grant_schema_usage_if_exists("public", role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_entity_graph_edges_withheld")
    op.execute("DROP INDEX IF EXISTS public.idx_entity_graph_edges_object")
    op.execute("DROP INDEX IF EXISTS public.idx_entity_graph_edges_subject")
    op.execute("DROP TABLE IF EXISTS public.entity_graph_edges CASCADE")
