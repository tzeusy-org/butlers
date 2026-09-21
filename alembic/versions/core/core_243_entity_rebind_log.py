"""Add the fleet entity-rebind receipt ledger.

Revision ID: core_243
Revises: core_242
Create Date: 2026-09-21 00:00:00.000000
"""

from __future__ import annotations

from alembic import context, op
from butlers.migration_preflight import preflight_runtime_attention_downgrade

revision = "core_243"
down_revision = "core_242"
branch_labels = None
depends_on = None

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)


def upgrade() -> None:
    # The core chain is replayed once per target schema and those replays can
    # overlap.  Serialize this shared-public DDL so concurrent IF NOT EXISTS
    # checks cannot race in PostgreSQL's catalogs.
    op.execute("SELECT pg_advisory_xact_lock(hashtext('core_243_entity_rebind_log'))")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.entity_rebind_log (
            rebind_id UUID NOT NULL,
            source_entity_id UUID NOT NULL REFERENCES public.entities(id),
            target_entity_id UUID NOT NULL REFERENCES public.entities(id),
            target_schema TEXT NOT NULL,
            references_rebound INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            error_class TEXT,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (rebind_id, target_schema),
            CONSTRAINT ck_entity_rebind_log_distinct_entities
                CHECK (source_entity_id <> target_entity_id),
            CONSTRAINT ck_entity_rebind_log_status
                CHECK (status IN ('active', 'failed', 'pending', 'skipped_no_table')),
            CONSTRAINT ck_entity_rebind_log_completion
                CHECK (
                    (status = 'pending' AND completed_at IS NULL AND error_class IS NULL)
                    OR
                    (status = 'active' AND completed_at IS NOT NULL AND error_class IS NULL)
                    OR
                    (status = 'skipped_no_table' AND completed_at IS NOT NULL
                        AND error_class = 'UndefinedTableError')
                    OR
                    (status = 'failed' AND completed_at IS NOT NULL AND error_class IS NOT NULL)
                )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entity_rebind_log_target_status
        ON public.entity_rebind_log (target_schema, status, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_entity_rebind_log_entities
        ON public.entity_rebind_log (target_entity_id, source_entity_id, created_at DESC)
        """
    )
    for role in _ALL_RUNTIME_ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    GRANT SELECT ON public.entity_rebind_log TO "{role}";
                    GRANT UPDATE (
                        references_rebound, status, error_class, completed_at, updated_at
                    ) ON public.entity_rebind_log TO "{role}";
                END IF;
            EXCEPTION
                WHEN insufficient_privilege OR undefined_object THEN NULL;
            END
            $$
            """
        )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'butler_relationship_rw'
            ) THEN
                GRANT INSERT ON public.entity_rebind_log TO butler_relationship_rw;
            END IF;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object THEN NULL;
        END
        $$
        """
    )

    # Reads are fleet-wide, cohort creation belongs only to Relationship, and
    # every other runtime role may settle only the row for its own schema.
    # ENABLE (rather than FORCE) intentionally preserves migration/backup owner
    # access while fencing every non-owner runtime identity.
    op.execute("ALTER TABLE public.entity_rebind_log ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS entity_rebind_log_read ON public.entity_rebind_log")
    op.execute(
        "CREATE POLICY entity_rebind_log_read ON public.entity_rebind_log FOR SELECT USING (true)"
    )
    op.execute(
        "DROP POLICY IF EXISTS entity_rebind_log_insert_relationship ON public.entity_rebind_log"
    )
    op.execute(
        "CREATE POLICY entity_rebind_log_insert_relationship ON public.entity_rebind_log "
        "FOR INSERT WITH CHECK (current_user = 'butler_relationship_rw')"
    )
    op.execute("DROP POLICY IF EXISTS entity_rebind_log_update_own ON public.entity_rebind_log")
    op.execute(
        """
        CREATE POLICY entity_rebind_log_update_own ON public.entity_rebind_log
        FOR UPDATE
        USING (
            current_user = 'butler_' ||
                CASE WHEN target_schema = 'chronicler_mem'
                    THEN 'chronicler' ELSE target_schema END || '_rw'
        )
        WITH CHECK (
            current_user = 'butler_' ||
                CASE WHEN target_schema = 'chronicler_mem'
                    THEN 'chronicler' ELSE target_schema END || '_rw'
        )
        """
    )

    # Column grants normally prevent identity rewrites, but bootstrap can
    # deliberately re-widen public-table grants.  Keep cohort identity
    # immutable at the table boundary even after such a re-grant.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.guard_entity_rebind_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.rebind_id IS DISTINCT FROM OLD.rebind_id
               OR NEW.source_entity_id IS DISTINCT FROM OLD.source_entity_id
               OR NEW.target_entity_id IS DISTINCT FROM OLD.target_entity_id
               OR NEW.target_schema IS DISTINCT FROM OLD.target_schema
            THEN
                RAISE EXCEPTION 'entity rebind receipt identity is immutable';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.entity_rebind_log') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS trg_entity_rebind_identity
                    ON public.entity_rebind_log;
            END IF;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_entity_rebind_identity BEFORE UPDATE "
        "ON public.entity_rebind_log FOR EACH ROW "
        "EXECUTE FUNCTION public.guard_entity_rebind_identity()"
    )


def downgrade() -> None:
    preflight_runtime_attention_downgrade(op, context)
    op.execute(
        """
        DO $$
        DECLARE
            has_receipts BOOLEAN;
        BEGIN
            IF to_regclass('public.entity_rebind_log') IS NOT NULL THEN
                EXECUTE 'SELECT EXISTS (SELECT 1 FROM public.entity_rebind_log)'
                    INTO has_receipts;
                IF has_receipts THEN
                    RAISE EXCEPTION
                        'cannot downgrade core_243 while entity rebind receipts exist';
                END IF;
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.entity_rebind_log') IS NOT NULL THEN
                DROP TRIGGER IF EXISTS trg_entity_rebind_identity
                    ON public.entity_rebind_log;
            END IF;
        END
        $$
        """
    )
    op.execute("DROP TABLE IF EXISTS public.entity_rebind_log")
    op.execute("DROP FUNCTION IF EXISTS public.guard_entity_rebind_identity()")
