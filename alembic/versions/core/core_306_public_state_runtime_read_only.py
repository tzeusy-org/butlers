"""Make ``public.state`` read-only for runtime roles (bu-0zm6zg).

Revision ID: core_306
Revises: core_305
Create Date: 2026-11-02 00:00:00.000000

``public.state`` holds dashboard-owned control facts: ``settings.general``, the
Telegram account-wide consent grant and ``infra.backup_facts``, which the QA
patrol trusts as the backup evidence.  Every writer is the dashboard API, which
connects as the migration login that owns the table and never ``SET ROLE``s.
Butler daemons and connectors only read it (their own ``state_*`` writes go to
the schema-local ``<schema>.state``).

``init-db.sql`` nevertheless grants every runtime role DML on all public tables,
so a butler or connector role could forge those facts.  This revision revokes
INSERT, UPDATE and DELETE from every ``butler_*_rw`` role and
``connector_writer`` while keeping SELECT; the owner login keeps full access.
``init-db.sql`` repeats the revoke after its broad public grant so a bootstrap
replay cannot restore the DML.

The core chain runs once per butler schema over one shared table, so only the
last schema to leave core_306 restores the grants (core_291 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_306"
down_revision = "core_305"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_306:public-state', 0))"


def _set_runtime_dml(*, granted: bool) -> None:
    statement = (
        "GRANT INSERT, UPDATE, DELETE ON TABLE public.state TO %I"
        if granted
        else "REVOKE INSERT, UPDATE, DELETE ON TABLE public.state FROM %I"
    )
    op.execute(f"""
        DO $$
        DECLARE role_name text;
        BEGIN
            IF to_regclass('public.state') IS NULL THEN
                RETURN;
            END IF;
            FOR role_name IN
                SELECT rolname FROM pg_roles
                WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\' OR rolname = 'connector_writer'
            LOOP
                EXECUTE format('{statement}', role_name);
                EXECUTE format('GRANT SELECT ON TABLE public.state TO %I', role_name);
            END LOOP;
        END $$
    """)


def _core_306_installation_count() -> int:
    """Count schema-local core chains that still require the read-only fence."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 306
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    _set_runtime_dml(granted=False)


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared table serves every schema still at core_306; only the last one reopens it.
    if _core_306_installation_count() > 1:
        return
    _set_runtime_dml(granted=True)
