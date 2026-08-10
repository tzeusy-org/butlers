"""Install the restore-drill executor's exclusive persistence boundary.

Revision ID: core_196
Revises: core_195
Create Date: 2026-08-10 00:00:00.000000

The recovery executor may create and remove only its fixed scratch database at
the server level. Its live application-database access is limited to three
fixed-search-path SECURITY DEFINER functions in an executor-only schema.
Their NOLOGIN owner is intentionally unavailable to the shared migration and
dashboard credential, so object ownership cannot bypass EXECUTE ACLs. The
executor-owner ledger is the sole restore-result authority: ``public.audit_log``
keeps a fixed audit projection but remains deliberately unauthoritative because
normal application roles have broad public audit DML.
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
        DO $$
        BEGIN
            -- The shared migration login temporarily has CREATE on this schema
            -- solely to stage this revision.  A relation it created before the
            -- migration could carry triggers that survive an IF NOT EXISTS
            -- table declaration and would then be trusted by the ownership
            -- finalizer.  The authority ledger must originate in this
            -- transaction or the migration fails before it exposes any trusted
            -- interface.
            IF to_regclass('restore_drill_executor.restore_drill_results') IS NOT NULL THEN
                RAISE EXCEPTION
                    'restore-drill result authority ledger must be created by core_196';
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TABLE restore_drill_executor.restore_drill_results (
            id BIGSERIAL PRIMARY KEY,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            result TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
            detail TEXT
        )
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

            SELECT max(recorded_at)
            INTO v_last_recorded_at
            FROM restore_drill_executor.restore_drill_results;

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
            v_result_id BIGINT;
            v_detail TEXT;
        BEGIN
            IF p_result IS NULL OR p_result NOT IN ('pass', 'fail') THEN
                RAISE EXCEPTION 'p_result must be pass or fail';
            END IF;

            -- ``record_result`` is callable directly by the executor login,
            -- so it is the final privacy and result-authority boundary. Keep
            -- the four-argument ABI for the deployed executor, but make every
            -- caller-controlled compatibility input except p_result inert:
            -- direct SQL callers may otherwise persist DSNs, dump content, or
            -- invented table counts through a trusted credential.
            v_detail := 'restore drill diagnostic withheld';

            INSERT INTO restore_drill_executor.restore_drill_results (
                result,
                detail
            )
            VALUES (
                p_result,
                CASE WHEN p_result = 'fail' THEN v_detail ELSE NULL END
            )
            RETURNING id INTO v_result_id;

            -- This audit row remains useful append-only telemetry, but no
            -- scheduler or API reader may infer restore truth from it. The
            -- insert shares the protected ledger transaction and uses only
            -- fixed canonical values.
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
                'restore_drill',
                p_result,
                CASE WHEN p_result = 'fail' THEN v_detail ELSE NULL END,
                jsonb_build_object(
                    'detail', CASE WHEN p_result = 'fail' THEN v_detail ELSE NULL END
                )
            );

            RETURN v_result_id;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION restore_drill_executor.latest_result()
        RETURNS TABLE (
            checked_at TIMESTAMPTZ,
            result TEXT,
            detail TEXT
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
            SELECT recorded_at, result, detail
            FROM restore_drill_executor.restore_drill_results
            ORDER BY recorded_at DESC, id DESC
            LIMIT 1
        $$;
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            v_dashboard_role NAME := COALESCE(
                NULLIF(current_setting('butlers.connecting_user', true), ''),
                'butlers'
            )::name;
        BEGIN
            IF to_regprocedure('restore_drill_executor_admin.finalize_interface()') IS NOT NULL THEN
                PERFORM restore_drill_executor_admin.finalize_interface();
            ELSIF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
                EXECUTE 'GRANT USAGE ON SCHEMA public TO restore_drill_executor_owner';
                EXECUTE 'GRANT SELECT, INSERT ON TABLE public.audit_log '
                    || 'TO restore_drill_executor_owner';
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.audit_log_id_seq '
                    || 'TO restore_drill_executor_owner';
                EXECUTE 'ALTER TABLE restore_drill_executor.restore_drill_results '
                    || 'OWNER TO restore_drill_executor_owner';
                EXECUTE 'ALTER SEQUENCE restore_drill_executor.restore_drill_results_id_seq '
                    || 'OWNER TO restore_drill_executor_owner';
                EXECUTE 'ALTER FUNCTION restore_drill_executor.is_due(INTEGER) '
                    || 'OWNER TO restore_drill_executor_owner';
                EXECUTE 'ALTER FUNCTION restore_drill_executor.latest_result() '
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
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA '
                    || 'restore_drill_executor FROM PUBLIC';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA '
                    || 'restore_drill_executor FROM restore_drill_executor';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA '
                    || 'restore_drill_executor FROM PUBLIC';
                EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA '
                    || 'restore_drill_executor FROM restore_drill_executor';
                EXECUTE 'GRANT USAGE ON SCHEMA restore_drill_executor '
                    || 'TO restore_drill_executor';
                EXECUTE 'GRANT EXECUTE ON FUNCTION restore_drill_executor.is_due(INTEGER) '
                    || 'TO restore_drill_executor';
                EXECUTE 'GRANT EXECUTE ON FUNCTION restore_drill_executor.record_result('
                    || 'TEXT, TEXT, TEXT, INTEGER) TO restore_drill_executor';
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_dashboard_role) THEN
                    EXECUTE 'GRANT USAGE ON SCHEMA restore_drill_executor TO '
                        || quote_ident(v_dashboard_role);
                    EXECUTE 'GRANT EXECUTE ON FUNCTION restore_drill_executor.latest_result() TO '
                        || quote_ident(v_dashboard_role);
                    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
                        || 'restore_drill_executor.restore_drill_results FROM '
                        || quote_ident(v_dashboard_role);
                    EXECUTE 'REVOKE ALL PRIVILEGES ON SEQUENCE '
                        || 'restore_drill_executor.restore_drill_results_id_seq FROM '
                        || quote_ident(v_dashboard_role);
                END IF;
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
            REVOKE ALL ON FUNCTION restore_drill_executor.latest_result()
                FROM PUBLIC;
            DROP FUNCTION IF EXISTS restore_drill_executor.latest_result();
            DROP FUNCTION IF EXISTS restore_drill_executor.record_result(
                TEXT, TEXT, TEXT, INTEGER
            );
            DROP FUNCTION IF EXISTS restore_drill_executor.is_due(INTEGER);
            DROP TABLE IF EXISTS restore_drill_executor.restore_drill_results;
        END;
        $$;
        """
    )
