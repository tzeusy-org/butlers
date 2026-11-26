"""core_385 -- calendar_conflict_acknowledgements: "keep both" as durable state.

Revision ID: core_385
Revises: core_378
Create Date: 2026-11-26 00:00:00.000000

An owner who says "keep both" on an overlap has decided it; the radar must stop raising it
until the overlap itself changes. One row per overlap pair (the provider-identity pair id the
radar already uses) holds the digest of the overlap as the owner saw it (``issue_digest``, the
resolve route's 409 binding) and its events-only ``state_digest``. The row suppresses the issue
only while the live state digest still equals the stored one: a change to either event's etag or
time moves it and the overlap is raised again; the clock and unrelated events never do. Public because the radar spans every butler's
calendar schema. Installed once across the per-schema core chains (``IF NOT EXISTS``); the last
schema drops it.
"""

from __future__ import annotations

from alembic import op
from butlers.db_authority.roles import butler_roles

revision = "core_385"
down_revision = "core_378"
branch_labels = None
depends_on = None

_LOCK = (
    "SELECT pg_advisory_xact_lock("
    "hashtextextended('butlers:core_385:calendar-conflict-acknowledgements', 0))"
)
_TABLE = "public.calendar_conflict_acknowledgements"


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            pair_id text PRIMARY KEY,
            issue_digest text NOT NULL,
            state_digest text NOT NULL,
            option_id text NOT NULL,
            acknowledged_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    for role in butler_roles():
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {_TABLE} TO "{role}"';
                END IF;
            END
            $$
            """
        )


def _installation_count() -> int:
    """Count schema-local core chains still at core_385 or later."""
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
                    THEN substring(version_num FROM 6)::integer >= 385
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
