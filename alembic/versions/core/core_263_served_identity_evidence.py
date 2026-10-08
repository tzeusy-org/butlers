"""Content-blind, attempt-owned runtime serving evidence.

Revision ID: core_263
Revises: core_261
"""

from alembic import op

revision = "core_263"
down_revision = "core_261"
branch_labels = None
depends_on = None
_LOCK = "butlers:core_263:served-identity"


def upgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_LOCK}', 0))")
    op.execute("""
        ALTER TABLE public.model_dispatch_attempts
          ADD COLUMN IF NOT EXISTS served_identity JSONB,
          ADD COLUMN IF NOT EXISTS receipt_sha256 TEXT,
          ADD COLUMN IF NOT EXISTS attempt_key UUID;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_dispatch_attempt_owned_key
          ON public.model_dispatch_attempts(attempt_key) WHERE attempt_key IS NOT NULL;
        DO $$ BEGIN
          IF NOT EXISTS(SELECT 1 FROM pg_constraint
                        WHERE conrelid='public.model_dispatch_attempts'::regclass
                          AND conname='ck_dispatch_served_identity') THEN
            ALTER TABLE public.model_dispatch_attempts ADD CONSTRAINT ck_dispatch_served_identity
              CHECK (served_identity IS NULL OR (
                jsonb_typeof(served_identity)='object'
                AND octet_length(served_identity::text) <= 16384
                AND served_identity->>'schema_version'='1'
                AND jsonb_typeof(served_identity->'executions')='array'
                AND jsonb_array_length(served_identity->'executions') <= 8) IS TRUE);
            ALTER TABLE public.model_dispatch_attempts ADD CONSTRAINT ck_dispatch_receipt_digest
              CHECK (receipt_sha256 IS NULL OR receipt_sha256 ~ '^[a-f0-9]{64}$');
          END IF;
        END $$;
    """)
    # A managed downgrade/re-upgrade may run under the bootstrap identity,
    # while the established attempt table stays owned by the normal migration
    # identity. Create its evidence child as that same actual owner so its
    # existing per-creator default ACLs apply; do not introduce a grant profile
    # or leave ordinary writers unable to see the recreated table.
    op.execute("""
        DO $$
        DECLARE
          calling_role TEXT := current_user;
          parent_owner TEXT;
        BEGIN
          SELECT pg_catalog.pg_get_userbyid(relowner) INTO STRICT parent_owner
            FROM pg_catalog.pg_class
            WHERE oid='public.model_dispatch_attempts'::regclass;
          EXECUTE pg_catalog.format('SET LOCAL ROLE %I', parent_owner);
          CREATE TABLE IF NOT EXISTS public.model_served_usage (
          attempt_id BIGINT NOT NULL REFERENCES public.model_dispatch_attempts(id)
            ON DELETE CASCADE,
          execution_index SMALLINT NOT NULL CHECK(execution_index BETWEEN 0 AND 7),
          model_ordinal SMALLINT NOT NULL CHECK(model_ordinal BETWEEN 0 AND 15),
          model_id TEXT NOT NULL CHECK(octet_length(model_id) <= 128),
          identity_authority TEXT NOT NULL CHECK(identity_authority IN
            ('provider_response','cli_usage_breakdown','configured_only','unavailable')),
          provenance TEXT NOT NULL CHECK(provenance IN
            ('provider_response','provider_or_request_fallback','cli_reported_unproven')),
          input_tokens BIGINT CHECK(input_tokens >= 0),
          output_tokens BIGINT CHECK(output_tokens >= 0),
          cache_read_input_tokens BIGINT CHECK(cache_read_input_tokens >= 0),
          cache_creation_input_tokens BIGINT CHECK(cache_creation_input_tokens >= 0),
          reported_cost_usd NUMERIC(19,9) CHECK(reported_cost_usd BETWEEN 0 AND 1000000000),
          cost_scope TEXT NOT NULL CHECK(cost_scope IN
            ('invocation','conversation_cumulative','unknown')),
          usage_source TEXT NOT NULL DEFAULT 'provider_breakdown'
            CHECK(usage_source='provider_breakdown'),
          evidence_state TEXT NOT NULL CHECK(evidence_state IN
            ('observed','partial','cumulative_unknown','unknown','malformed','truncated')),
          recorded_at TIMESTAMPTZ NOT NULL,
          PRIMARY KEY(attempt_id,execution_index,model_ordinal),
          UNIQUE(attempt_id,execution_index,model_id)
          );
          EXECUTE pg_catalog.format('SET LOCAL ROLE %I', calling_role);
        END $$;
    """)


def downgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_LOCK}', 0))")
    bind = op.get_bind()
    schemas = bind.exec_driver_sql("""
        SELECT namespace.nspname FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid=version_table.relnamespace
        WHERE version_table.relname='alembic_version'
          AND version_table.relkind IN ('r','p') AND namespace.nspname NOT LIKE 'pg_%%'
    """).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = sum(
        int(
            bool(
                bind.exec_driver_sql(
                    f"SELECT EXISTS(SELECT 1 FROM {quote(schema)}.alembic_version "
                    "WHERE CASE WHEN version_num ~ '^core_[0-9]+$' "
                    "THEN substring(version_num FROM 6)::integer >= 263 ELSE false END)"
                ).scalar()
            )
        )
        for schema in schemas
    )
    if count > 1:
        return
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM public.model_dispatch_attempts
                    WHERE served_identity IS NOT NULL OR attempt_key IS NOT NULL
                       OR receipt_sha256 IS NOT NULL)
             OR EXISTS(SELECT 1 FROM public.model_served_usage) THEN
            RAISE EXCEPTION 'serving evidence exists; roll forward instead of erasing evidence';
          END IF;
        END $$;
        DROP TABLE public.model_served_usage;
        DROP INDEX public.ux_dispatch_attempt_owned_key;
        ALTER TABLE public.model_dispatch_attempts DROP CONSTRAINT ck_dispatch_served_identity,
          DROP CONSTRAINT ck_dispatch_receipt_digest,
          DROP COLUMN served_identity, DROP COLUMN receipt_sha256, DROP COLUMN attempt_key;
    """)
