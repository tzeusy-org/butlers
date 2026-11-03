"""Atomically invalidate exact memory projections with caller authority.

Revision ID: core_314
Revises: core_313

The shared function and narrow additive grants survive downgrade: another
schema or an older compatible runtime can still use this database-global
boundary. No canonical memories are mutated or projection rows broadly deleted.
"""

from __future__ import annotations

from alembic import op

revision = "core_314"
down_revision = "core_313"
branch_labels = None
depends_on = None

_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_concierge_rw",
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
_SIGNATURE = "public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)"


def upgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:memory-projection-invalidation',0))"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION public.invalidate_memory_projection(
            p_source_schema TEXT,
            p_source_table TEXT,
            p_source_ids UUID[],
            p_invalid_at TIMESTAMPTZ,
            p_include_graph BOOLEAN,
            p_redact_text BOOLEAN DEFAULT FALSE
        ) RETURNS VOID
        LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog
        AS $body$
        BEGIN
            IF p_source_schema IS NULL OR p_source_schema !~ '^[A-Za-z_][A-Za-z0-9_]*$'
               OR p_source_table IS NULL OR p_source_table NOT IN ('facts','rules')
               OR p_source_ids IS NULL OR array_position(p_source_ids, NULL::uuid) IS NOT NULL
               OR p_invalid_at IS NULL OR p_include_graph IS NULL OR p_redact_text IS NULL
               OR (p_redact_text AND NOT p_include_graph)
            THEN
                RAISE EXCEPTION 'Invalid memory projection invalidation arguments'
                    USING ERRCODE = '22023';
            END IF;
            IF cardinality(p_source_ids) = 0 THEN
                RETURN;
            END IF;
            UPDATE public.memory_catalog AS c
            SET confidence = 0,
                invalid_at = p_invalid_at,
                updated_at = now(),
                summary = CASE WHEN p_redact_text THEN '' ELSE c.summary END,
                title = CASE WHEN p_redact_text THEN NULL ELSE c.title END,
                embedding = CASE WHEN p_redact_text THEN NULL ELSE c.embedding END,
                search_vector = CASE WHEN p_redact_text THEN NULL ELSE c.search_vector END
            WHERE c.source_schema = p_source_schema
              AND c.source_table = p_source_table
              AND c.source_id = ANY(p_source_ids);
            IF p_include_graph THEN
                DELETE FROM public.entity_graph_edges AS e
                WHERE e.source_schema = p_source_schema
                  AND e.source_table = p_source_table
                  AND e.source_id = ANY(p_source_ids);
            END IF;
        END
        $body$;
    """)
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    for role in _RUNTIME_ROLES:
        op.execute(f"""
            DO $grants$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    GRANT USAGE ON SCHEMA public TO {role};
                    GRANT SELECT, UPDATE ON public.memory_catalog TO {role};
                    GRANT SELECT, DELETE ON public.entity_graph_edges TO {role};
                    GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO {role};
                ELSE
                    RAISE NOTICE 'Memory projection role % absent; bootstrap replay required',
                        '{role}';
                END IF;
            END
            $grants$;
        """)


def downgrade() -> None:
    # A schema-local rollback must not remove a shared primitive or erase
    # identical preexisting privileges of other consumers. Retain both.
    pass
