"""Install the restore-drill executor's exclusive persistence boundary.

Revision ID: core_196
Revises: core_195
Create Date: 2026-08-10 00:00:00.000000

The recovery executor may create and remove only its fixed scratch database at
the server level. Its live application-database access is limited to two
fixed-search-path SECURITY DEFINER functions in an executor-only schema.
Their NOLOGIN owner is intentionally unavailable to the shared migration and
dashboard credential, so object ownership cannot bypass EXECUTE ACLs.
"""

from __future__ import annotations

from alembic import op

revision = "core_196"
down_revision = "core_195"
branch_labels = None
depends_on = None

_EXECUTOR_ROLE = "restore_drill_executor"
_OWNER_ROLE = "restore_drill_executor_owner"
_EXECUTOR_SCHEMA = "restore_drill_executor"
_ADMIN_FINALIZER = "restore_drill_executor_admin.finalize_interface"


def upgrade() -> None:
    # ``init-db.sql`` is the normal privileged prerequisite. The superuser-only
    # fallback keeps fresh test databases migration-faithful without granting a
    # shared runtime login the ability to assume the dedicated owner role.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'restore_drill_executor'
            ) THEN
                IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                    RAISE EXCEPTION
                        'run scripts/init-db.sql as the managed privileged bootstrap '
                        'before core_196';
                END IF;
                CREATE ROLE restore_drill_executor
                    NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'restore_drill_executor_owner'
            ) THEN
                IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                    RAISE EXCEPTION
                        'run scripts/init-db.sql as the managed privileged bootstrap '
                        'before core_196';
                END IF;
                CREATE ROLE restore_drill_executor_owner
                    NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_namespace WHERE nspname = 'restore_drill_executor'
            ) THEN
                IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                    RAISE EXCEPTION
                        'run scripts/init-db.sql as the managed privileged bootstrap '
                        'before core_196';
                END IF;
                CREATE SCHEMA IF NOT EXISTS restore_drill_executor
                    AUTHORIZATION restore_drill_executor_owner;
            END IF;
            IF NOT has_schema_privilege(current_user, 'restore_drill_executor', 'CREATE') THEN
                RAISE EXCEPTION
                    'restore-drill migration staging privilege is absent; '
                    'run scripts/init-db.sql first';
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION restore_drill_executor.is_due(
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
        CREATE OR REPLACE FUNCTION restore_drill_executor.record_result(
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
            v_detail TEXT;
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

            -- ``record_result`` is callable directly by the executor login,
            -- so it is the final audit/API privacy boundary. Never rely on
            -- the Python runner to have already sanitized p_detail: a direct
            -- SQL caller can otherwise write raw client output into both
            -- audit_log.error and audit_log.metadata. The SQL surface keeps
            -- no caller-supplied detail at all; the executor's structured
            -- result/table count remains durable while a fixed safe diagnostic
            -- is the only text that crosses this boundary.
            v_detail := 'restore drill diagnostic withheld';

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
                CASE WHEN p_result = 'fail' THEN v_detail ELSE NULL END,
                jsonb_build_object(
                    'backup_file', p_backup_name,
                    'table_count', p_table_count,
                    'detail', v_detail
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
        DO $$
        BEGIN
            IF to_regprocedure('restore_drill_executor_admin.finalize_interface()') IS NOT NULL THEN
                PERFORM restore_drill_executor_admin.finalize_interface();
            ELSIF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                EXECUTE 'GRANT USAGE ON SCHEMA public TO restore_drill_executor_owner';
                EXECUTE 'GRANT SELECT, INSERT ON TABLE public.audit_log '
                    || 'TO restore_drill_executor_owner';
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.audit_log_id_seq '
                    || 'TO restore_drill_executor_owner';
                EXECUTE 'ALTER FUNCTION restore_drill_executor.is_due(INTEGER) '
                    || 'OWNER TO restore_drill_executor_owner';
                EXECUTE 'ALTER FUNCTION restore_drill_executor.record_result('
                    || 'TEXT, TEXT, TEXT, INTEGER) OWNER TO restore_drill_executor_owner';
                EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM PUBLIC';
                EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor '
                    || 'FROM restore_drill_executor';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA '
                    || 'restore_drill_executor FROM PUBLIC';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA '
                    || 'restore_drill_executor FROM restore_drill_executor';
                EXECUTE 'GRANT USAGE ON SCHEMA restore_drill_executor '
                    || 'TO restore_drill_executor';
                EXECUTE 'GRANT EXECUTE ON FUNCTION restore_drill_executor.is_due(INTEGER) '
                    || 'TO restore_drill_executor';
                EXECUTE 'GRANT EXECUTE ON FUNCTION restore_drill_executor.record_result('
                    || 'TEXT, TEXT, TEXT, INTEGER) TO restore_drill_executor';
            ELSE
                RAISE EXCEPTION
                    'restore-drill ownership finalizer is unavailable; '
                    'run scripts/init-db.sql first';
            END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    # The normal migration login deliberately cannot drop objects owned by the
    # isolated NOLOGIN role. Production rollback therefore remains a managed
    # privileged-bootstrap operation; direct superuser test databases can still
    # exercise the reversible SQL shape.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                RAISE EXCEPTION
                    'core_196 downgrade requires the managed privileged bootstrap owner';
            END IF;
            REVOKE ALL ON FUNCTION restore_drill_executor.is_due(INTEGER)
                FROM restore_drill_executor;
            REVOKE ALL ON FUNCTION restore_drill_executor.record_result(
                TEXT, TEXT, TEXT, INTEGER
            ) FROM restore_drill_executor;
            DROP FUNCTION IF EXISTS restore_drill_executor.record_result(
                TEXT, TEXT, TEXT, INTEGER
            );
            DROP FUNCTION IF EXISTS restore_drill_executor.is_due(INTEGER);
        END;
        $$;
        """
    )
