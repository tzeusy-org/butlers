"""The docket job's read grant on public.calendar_rsvp_policies (bu-5ii4gj.9 S3).

Revision ID: core_389
Revises: core_388
Create Date: 2026-11-27 11:00:00.000000

openspec change ``calendar-invitation-docket``, REQ-calendar-invitation-docket-001 and -008. The
docket job prepares answers through the Relationship butler, the butler whose calendar module holds
the response fence, so exactly one runtime role may read the owner's policies: ``butler_relationship_rw``
gets SELECT, behind a row-level-security policy that names it, and nothing else changes. It still
cannot insert, update or delete a policy: only the dashboard definer writes (core_388), and no
other runtime role or the dashboard login can read the table.

The core chain runs once per butler schema over this shared table: every statement is idempotent
under an advisory lock, and only the last schema still at core_389 takes the grant away.
"""

from __future__ import annotations

from alembic import op

revision = "core_389"
down_revision = "core_388"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_389:rsvp_job_read', 0))"
_ROLE = "butler_relationship_rw"
_POLICY = "calendar_rsvp_policies_job_read"


def _installation_count() -> int:
    """Count schema-local core chains still at core_389 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 389
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON public.calendar_rsvp_policies")
    op.execute(
        f"""CREATE POLICY {_POLICY} ON public.calendar_rsvp_policies
            FOR SELECT TO PUBLIC USING (current_user = '{_ROLE}')"""
    )
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ROLE}') THEN
            GRANT SELECT ON TABLE public.calendar_rsvp_policies TO {_ROLE};
        END IF;
    END $$""")


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ROLE}')
           AND to_regclass('public.calendar_rsvp_policies') IS NOT NULL THEN
            REVOKE ALL ON TABLE public.calendar_rsvp_policies FROM {_ROLE};
        END IF;
    END $$""")
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON public.calendar_rsvp_policies")
