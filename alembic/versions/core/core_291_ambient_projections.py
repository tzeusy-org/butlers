"""Owner-minted, route-scoped ambient projection grants (REQ-ambient-projections-001).

Revision ID: core_291
Revises: core_290

``public.ambient_projections`` holds one row per glance surface (wall display, e-ink
panel, Home Assistant sensor). Only the SHA-256 digest of the bearer token is stored.

Write authority (owner decision bu-xfhsrz, option B): only the dashboard API login,
which owns the table, mints, revokes and stamps grants. Row-level security is
ENABLEd (not FORCEd, so the owning login keeps its owner bypass) with a SELECT-only
policy, so a runtime role that still holds a bootstrap-granted INSERT/UPDATE/DELETE
privilege (``init-db.sql`` re-grants on every rerun) matches no write policy and is
refused. The direct privileges are also revoked here. Residual risk, stated in the
spec: every runtime role can read the digests (never the tokens).
"""

from alembic import op

revision = "core_291"
down_revision = "core_290"
branch_labels = None
depends_on = None
_LOCK = "butlers:core_291:ambient-projections"


def upgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_LOCK}', 0))")
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.ambient_projections (
            id uuid PRIMARY KEY,
            label text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
            surface_kind text NOT NULL
                CHECK (surface_kind IN ('wall_display', 'eink', 'home_assistant', 'other')),
            audience_class text NOT NULL
                CHECK (audience_class IN ('household', 'owner_private')),
            route_scope text NOT NULL CHECK (route_scope IN ('glance')),
            token_digest text NOT NULL UNIQUE CHECK (token_digest ~ '^[0-9a-f]{64}$'),
            expected_fetch_interval interval NOT NULL DEFAULT interval '5 minutes'
                CHECK (expected_fetch_interval BETWEEN interval '1 minute' AND interval '1 day'),
            created_at timestamptz NOT NULL DEFAULT now(),
            revoked_at timestamptz,
            last_fetch_at timestamptz,
            last_fetch_status text
                CHECK (last_fetch_status IS NULL OR last_fetch_status IN ('ok', 'degraded'))
        )
    """)
    op.execute("ALTER TABLE public.ambient_projections ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS ambient_projections_read ON public.ambient_projections")
    op.execute(
        "CREATE POLICY ambient_projections_read ON public.ambient_projections "
        "FOR SELECT USING (true)"
    )
    op.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.ambient_projections FROM PUBLIC")
    op.execute("""
        DO $$
        DECLARE role_name text;
        BEGIN
            FOR role_name IN
                SELECT rolname FROM pg_roles
                WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\' OR rolname = 'connector_writer'
            LOOP
                EXECUTE format(
                    'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON public.ambient_projections FROM %I',
                    role_name
                );
                EXECUTE format('GRANT SELECT ON public.ambient_projections TO %I', role_name);
            END LOOP;
        END $$
    """)


def downgrade() -> None:
    op.execute(f"SELECT pg_advisory_xact_lock(hashtextextended('{_LOCK}', 0))")
    # Core is applied independently per schema over this one public store. The
    # advisory lock also serializes the version census with other 291 replays.
    bind = op.get_bind()
    schemas = bind.exec_driver_sql("""
        SELECT namespace.nspname FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid=version_table.relnamespace
        WHERE version_table.relname='alembic_version'
          AND version_table.relkind IN ('r','p') AND namespace.nspname NOT LIKE 'pg_%%'
    """).scalars()
    quote = bind.dialect.identifier_preparer.quote
    consumers = 0
    for schema in schemas:
        versions = bind.exec_driver_sql(
            f"SELECT version_num FROM {quote(schema)}.alembic_version"
        ).scalars()
        core = [version[5:] for version in versions if version.startswith("core_")]
        if any(not version.isascii() or not version.isdecimal() for version in core):
            raise RuntimeError(
                "ambient projection consumer revision unknown; roll forward and repair "
                "the schema migration history before retrying rollback"
            )
        consumers += any(int(version) >= 291 for version in core)
    if consumers > 1:
        return
    if consumers != 1:
        raise RuntimeError(
            "ambient projection consumer could not be established; roll forward and "
            "repair the schema migration history before retrying rollback"
        )
    op.execute("""
        DO $$ BEGIN
          IF to_regclass('public.ambient_projections') IS NULL THEN
            RETURN;
          END IF;
          LOCK TABLE public.ambient_projections IN ACCESS EXCLUSIVE MODE;
          IF EXISTS(SELECT 1 FROM public.ambient_projections) THEN
            RAISE EXCEPTION 'ambient projection grants exist; roll forward to retain history'
              USING HINT = 'Revocation preserves history; it does not authorize deletion.';
          END IF;
          DROP TABLE public.ambient_projections;
        END $$;
    """)
