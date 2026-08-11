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

_TRUSTED_FINALIZED_INTERFACE_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM pg_namespace AS result_schema
        JOIN pg_roles AS result_owner
            ON result_owner.oid = result_schema.nspowner
        JOIN pg_class AS result_ledger
            ON result_ledger.relnamespace = result_schema.oid
           AND result_ledger.relname = 'restore_drill_results'
           AND result_ledger.relkind = 'r'
        JOIN pg_proc AS is_due
            ON is_due.pronamespace = result_schema.oid
           AND is_due.proname = 'is_due'
           AND is_due.pronargs = 1
           AND is_due.proargtypes = '23'::oidvector
        JOIN pg_proc AS record_result
            ON record_result.pronamespace = result_schema.oid
           AND record_result.proname = 'record_result'
           AND record_result.pronargs = 4
           AND record_result.proargtypes = '25 25 25 23'::oidvector
        JOIN pg_proc AS latest_result
            ON latest_result.pronamespace = result_schema.oid
           AND latest_result.proname = 'latest_result'
           AND latest_result.pronargs = 0
        JOIN pg_namespace AS admin_schema
            ON admin_schema.nspname = 'restore_drill_executor_admin'
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
        WHERE result_schema.nspname = 'restore_drill_executor'
          AND result_owner.rolname = 'restore_drill_executor_owner'
          AND NOT result_owner.rolcanlogin
          AND NOT result_owner.rolsuper
          AND NOT result_owner.rolcreaterole
          AND NOT result_owner.rolcreatedb
          AND NOT result_owner.rolreplication
          AND result_ledger.relowner = result_schema.nspowner
          AND is_due.proowner = result_schema.nspowner
          AND record_result.proowner = result_schema.nspowner
          AND latest_result.proowner = result_schema.nspowner
          AND is_due.prosecdef
          AND record_result.prosecdef
          AND latest_result.prosecdef
          AND bootstrap_owner.rolsuper
          AND installer.prosecdef
          AND finalizer.prosecdef
          AND installer.proowner = admin_schema.nspowner
          AND finalizer.proowner = admin_schema.nspowner
          -- A finalized interface grants the shared migration caller only its
          -- read projection; it must retain neither owner membership nor any
          -- direct ledger, executor-writer, or bootstrap-admin authority.
          AND NOT pg_has_role(current_user, result_owner.oid, 'USAGE')
          AND has_schema_privilege(current_user, result_schema.oid, 'USAGE')
          AND NOT has_schema_privilege(current_user, result_schema.oid, 'CREATE')
          AND NOT has_table_privilege(current_user, result_ledger.oid, 'SELECT')
          AND NOT has_table_privilege(current_user, result_ledger.oid, 'INSERT')
          AND NOT has_function_privilege(current_user, is_due.oid, 'EXECUTE')
          AND NOT has_function_privilege(current_user, record_result.oid, 'EXECUTE')
          AND has_function_privilege(current_user, latest_result.oid, 'EXECUTE')
          AND NOT has_schema_privilege(current_user, admin_schema.oid, 'USAGE')
          AND NOT has_function_privilege(current_user, installer.oid, 'EXECUTE')
          AND NOT has_function_privilege(current_user, finalizer.oid, 'EXECUTE')
    )
"""

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
    # Core migrations execute once per schema. After the first schema installs
    # this database-global authority, finalization correctly revokes this
    # normal migration caller's access to the admin installer. A later
    # schema-scoped invocation may no-op only after catalog proof of the exact
    # trusted finalized interface; an absent or spoofed shape still falls
    # through to the strict bootstrap-installer check below.
    has_trusted_finalized_interface = bool(
        op.get_bind().exec_driver_sql(_TRUSTED_FINALIZED_INTERFACE_SQL).scalar_one()
    )
    if has_trusted_finalized_interface:
        return

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
