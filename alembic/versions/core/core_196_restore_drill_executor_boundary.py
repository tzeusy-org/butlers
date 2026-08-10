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

_ADMIN_INSTALLER = "restore_drill_executor_admin.install_interface"

_TRUSTED_BOOTSTRAP_INSTALLER_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_namespace AS admin_schema
        JOIN pg_roles AS bootstrap_owner
            ON bootstrap_owner.oid = admin_schema.nspowner
        JOIN pg_proc AS installer
            ON installer.pronamespace = admin_schema.oid
           AND installer.proname = 'install_interface'
           AND installer.pronargs = 0
           AND installer.prorettype = 'void'::regtype
        JOIN pg_proc AS finalizer
            ON finalizer.pronamespace = admin_schema.oid
           AND finalizer.proname = 'finalize_interface'
           AND finalizer.pronargs = 0
           AND finalizer.prorettype = 'void'::regtype
        WHERE admin_schema.nspname = 'restore_drill_executor_admin'
          AND installer.prosecdef
          AND finalizer.prosecdef
          AND installer.proowner = admin_schema.nspowner
          AND finalizer.proowner = admin_schema.nspowner
          -- The installer is created only by init-db's trusted cluster
          -- superuser path.  A shared database owner can create matching
          -- objects, but cannot satisfy this provenance condition.
          AND bootstrap_owner.rolsuper
    )
"""


def upgrade() -> None:
    """Call only an exact, bootstrap-owned installer in this transaction.

    The shared migration login must never create the authority objects itself,
    nor accept a matching function signature from an admin schema it can own.
    ``init-db.sql`` validates/rejects that schema before it exposes the fixed
    installer; this catalog check independently fails closed before invoking
    the function.
    """
    has_trusted_bootstrap_installer = bool(
        op.get_bind().exec_driver_sql(_TRUSTED_BOOTSTRAP_INSTALLER_SQL).scalar_one()
    )
    if not has_trusted_bootstrap_installer:
        op.execute(
            """
            DO $$
            BEGIN
                RAISE EXCEPTION
                    'restore-drill bootstrap installer is missing or untrusted; '
                    'run scripts/init-db.sql as the privileged bootstrap first';
            END;
            $$;
            """
        )

    # The fixed no-argument installer creates the exact private authority
    # relation/functions and finalizes their ACLs in the enclosing Alembic
    # transaction. It rejects every pre-existing authority object.
    op.execute(f"SELECT {_ADMIN_INSTALLER}()")


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
