"""Permanent, content-blind ordinary ingestion target acceptance ledger.

Revision ID: core_268
Revises: core_265

Reserved after the 2026-10-14 full remote census (foreign 266/267 are unmerged).
The receipt is the first route_inbox id, without a pruning-sensitive FK.
Downgrade deliberately preserves the ledger, guards, grants and FORCE RLS.
"""

from alembic import op

revision = "core_268"
down_revision = "core_265"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_bind().exec_driver_sql("SELECT current_schema()").scalar_one()
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS route_acceptance_ledger (
            ingestion_event_id UUID NOT NULL,
            target_butler TEXT NOT NULL CHECK (target_butler ~ '^[a-z][a-z0-9_-]{0,63}$'),
            segment_id TEXT NOT NULL CHECK (segment_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
            payload_digest TEXT NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
            receipt_id UUID NOT NULL UNIQUE,
            accepted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (ingestion_event_id, target_butler, segment_id),
            CHECK (substring(ingestion_event_id::text FROM 15 FOR 1) = '7')
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_route_acceptance_ledger()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'route acceptance evidence is append-only'
                USING ERRCODE = '42501';
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION stamp_route_acceptance_time()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            NEW.accepted_at := now();
            RETURN NEW;
        END $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS route_acceptance_server_time ON route_acceptance_ledger")
    op.execute(
        "CREATE TRIGGER route_acceptance_server_time BEFORE INSERT ON route_acceptance_ledger "
        "FOR EACH ROW EXECUTE FUNCTION stamp_route_acceptance_time()"
    )
    op.execute("DROP TRIGGER IF EXISTS route_acceptance_append_only ON route_acceptance_ledger")
    op.execute(
        """
        CREATE TRIGGER route_acceptance_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON route_acceptance_ledger
        FOR EACH STATEMENT EXECUTE FUNCTION guard_route_acceptance_ledger()
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION guard_ingestion_route_inbox_acceptance()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE binding jsonb := NEW.route_envelope -> 'delivery';
        BEGIN
            IF binding IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM {quoted_schema}.route_acceptance_ledger ledger
                WHERE ledger.ingestion_event_id = (binding ->> 'ingestion_event_id')::uuid
                  AND ledger.target_butler = binding ->> 'target_butler'
                  AND ledger.segment_id = binding ->> 'segment_id'
                  AND ledger.payload_digest = binding ->> 'payload_digest'
                  AND ledger.receipt_id = NEW.id
                  AND coalesce(binding ->> 'operation', 'accept') = 'accept'
            ) THEN
                RAISE EXCEPTION 'route inbox requires matching permanent acceptance'
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS ingestion_route_acceptance ON route_inbox")
    op.execute(
        "CREATE TRIGGER ingestion_route_acceptance BEFORE INSERT ON route_inbox "
        "FOR EACH ROW EXECUTE FUNCTION guard_ingestion_route_inbox_acceptance()"
    )
    op.execute("ALTER TABLE route_acceptance_ledger ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE route_acceptance_ledger FORCE ROW LEVEL SECURITY")
    # Bind to the installed schema, not a mutable runtime search_path. Public
    # supports existing core-only fixtures and legacy standalone databases.
    schema_literal = "'" + schema.replace("'", "''") + "'"
    predicate = (
        "current_user = 'butler_' || target_butler || '_rw' "
        f"AND ({schema_literal} = 'public' OR target_butler = {schema_literal})"
    )
    for command in ("SELECT", "INSERT"):
        policy = "route_acceptance_" + command.lower()
        op.execute(f"DROP POLICY IF EXISTS {policy} ON route_acceptance_ledger")
        clause = "USING" if command == "SELECT" else "WITH CHECK"
        op.execute(
            f"CREATE POLICY {policy} ON route_acceptance_ledger "
            f"FOR {command} {clause} ({predicate})"
        )
    op.execute("REVOKE ALL ON route_acceptance_ledger FROM PUBLIC")
    # Existing bootstrap owns role creation. Grant only the two runtime
    # operations; replayed bootstrap widening cannot override RLS/immutability.
    op.execute(
        """
        DO $$ DECLARE runtime_role text;
        BEGIN
            FOR runtime_role IN SELECT rolname FROM pg_roles
                WHERE rolname ~ '^butler_[a-z][a-z0-9_-]*_rw$'
                  AND (current_schema() = 'public'
                       OR rolname = 'butler_' || current_schema() || '_rw')
            LOOP
                EXECUTE format('REVOKE ALL ON route_acceptance_ledger FROM %I', runtime_role);
                EXECUTE format('GRANT SELECT, INSERT ON route_acceptance_ledger TO %I',
                               runtime_role);
            END LOOP;
        END $$
        """
    )


def downgrade() -> None:
    """Keep accepted evidence and its enforcement; older code cannot admit this envelope."""
    pass
