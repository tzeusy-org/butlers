"""Install the restore-drill executor's narrow persistence boundary.

Revision ID: core_196
Revises: core_195
Create Date: 2026-08-10 00:00:00.000000

The isolated executor may create and remove only its fixed scratch database at
the server level. It receives no direct audit-table privilege: these two
fixed-search-path security-definer functions are its entire live application
database interface.
"""

from __future__ import annotations

from alembic import op

revision = "core_196"
down_revision = "core_195"
branch_labels = None
depends_on = None

_EXECUTOR_ROLE = "restore_drill_executor"


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.restore_drill_executor_is_due(
            p_interval_seconds INTEGER
        )
        RETURNS BOOLEAN
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_last_recorded_at TIMESTAMPTZ;
        BEGIN
            IF p_interval_seconds IS NULL OR p_interval_seconds <= 0 THEN
                RAISE EXCEPTION 'p_interval_seconds must be positive';
            END IF;

            SELECT max(ts)
            INTO v_last_recorded_at
            FROM public.audit_log
            WHERE actor = 'restore_drill'
              AND action = 'restore_drill_result';

            RETURN v_last_recorded_at IS NULL
                OR v_last_recorded_at <= clock_timestamp()
                    - make_interval(secs => p_interval_seconds);
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.record_restore_drill_executor_result(
            p_backup_name TEXT,
            p_result TEXT,
            p_detail TEXT,
            p_table_count INTEGER
        )
        RETURNS BIGINT
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            v_audit_id BIGINT;
        BEGIN
            IF p_backup_name IS NULL OR btrim(p_backup_name) = ''
               OR octet_length(p_backup_name) > 512 THEN
                RAISE EXCEPTION 'p_backup_name must be a non-empty value up to 512 bytes';
            END IF;
            IF p_result NOT IN ('pass', 'fail') THEN
                RAISE EXCEPTION 'p_result must be pass or fail';
            END IF;
            IF p_table_count IS NOT NULL AND p_table_count < 0 THEN
                RAISE EXCEPTION 'p_table_count must not be negative';
            END IF;

            INSERT INTO public.audit_log (
                actor,
                action,
                target,
                result,
                error,
                metadata
            )
            VALUES (
                'restore_drill',
                'restore_drill_result',
                p_backup_name,
                p_result,
                CASE WHEN p_result = 'fail' THEN p_detail ELSE NULL END,
                jsonb_build_object(
                    'backup_file', p_backup_name,
                    'table_count', p_table_count,
                    'detail', p_detail
                )
            )
            RETURNING id INTO v_audit_id;

            RETURN v_audit_id;
        END;
        $$;
        """
    )
    op.execute(
        """
        REVOKE ALL ON FUNCTION public.restore_drill_executor_is_due(INTEGER) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.record_restore_drill_executor_result(
            TEXT, TEXT, TEXT, INTEGER
        ) FROM PUBLIC;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_EXECUTOR_ROLE}') THEN
                GRANT EXECUTE ON FUNCTION public.restore_drill_executor_is_due(INTEGER)
                    TO {_EXECUTOR_ROLE};
                GRANT EXECUTE ON FUNCTION public.record_restore_drill_executor_result(
                    TEXT, TEXT, TEXT, INTEGER
                ) TO {_EXECUTOR_ROLE};
            END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_EXECUTOR_ROLE}') THEN
                REVOKE ALL ON FUNCTION public.restore_drill_executor_is_due(INTEGER)
                    FROM {_EXECUTOR_ROLE};
                REVOKE ALL ON FUNCTION public.record_restore_drill_executor_result(
                    TEXT, TEXT, TEXT, INTEGER
                ) FROM {_EXECUTOR_ROLE};
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS public.record_restore_drill_executor_result(
            TEXT, TEXT, TEXT, INTEGER
        );
        DROP FUNCTION IF EXISTS public.restore_drill_executor_is_due(INTEGER);
        """
    )
