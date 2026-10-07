"""Private server-admitted dashboard source attribution; legacy remains unknown.

Revision ID: core_261
Revises: core_258

The common API table belongs to core, independently of Relationship startup.
This record is supplied only by the real OwnerAuth-admitted message producer.
It is neither an auth credential nor authority for a later HTTP request.
"""

from alembic import op

revision = "core_261"
down_revision = "core_258"
branch_labels = None
depends_on = None

_SHARED_DDL_LOCK = "butlers:core_261:dashboard-source-attribution"


def _installation_count() -> int:
    """Shared provenance survives while any other schema still needs it."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND namespace.nspname NOT LIKE 'pg_%%'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    return sum(
        int(
            bool(
                bind.exec_driver_sql(
                    f"SELECT EXISTS(SELECT 1 FROM {quote(schema)}.alembic_version "
                    "WHERE CASE WHEN version_num ~ '^core_[0-9]+$' "
                    "THEN substring(version_num FROM 6)::integer >= 261 ELSE false END)"
                ).scalar()
            )
        )
        for schema in schemas
    )


def upgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_SHARED_DDL_LOCK}', 0))")
    op.execute(
        "ALTER TABLE public.dashboard_messages ADD COLUMN IF NOT EXISTS fact_owner_admission JSONB"
    )


def downgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_SHARED_DDL_LOCK}', 0))")
    if _installation_count() > 1:
        return
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='dashboard_messages'
                    AND column_name='fact_owner_admission') THEN
            IF EXISTS(SELECT 1 FROM public.dashboard_messages
                      WHERE fact_owner_admission IS NOT NULL) THEN
              RAISE EXCEPTION
                'source attribution exists; roll forward instead of erasing provenance';
            END IF;
          END IF;
        END $$;
        ALTER TABLE public.dashboard_messages DROP COLUMN IF EXISTS fact_owner_admission;
    """)
