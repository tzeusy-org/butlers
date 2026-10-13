"""Ordinary ingestion classified plans and immutable per-segment target intents.

Revision ID: sw_045
Revises: sw_044

Representation only: classification admission and fenced transitions belong to
subsequent delivery beads. No retry loop, historical backfill, or approval tables.
Payloads may be nulled for retention; identity/digest cannot be rewritten.
Downgrade retains representations so rollback cannot forget accepted evidence.
"""

from alembic import op

revision = "sw_045"
down_revision = "sw_044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_target_plans (
            ingestion_event_id UUID PRIMARY KEY,
            plan_digest TEXT NOT NULL CHECK (plan_digest ~ '^[0-9a-f]{64}$'),
            classified_plan JSONB,
            committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (substring(ingestion_event_id::text FROM 15 FOR 1) = '7')
        );
        CREATE TABLE IF NOT EXISTS ingestion_target_intents (
            ingestion_event_id UUID NOT NULL REFERENCES ingestion_target_plans(ingestion_event_id),
            target_butler TEXT NOT NULL CHECK (target_butler ~ '^[a-z][a-z0-9_-]{0,63}$'),
            segment_id TEXT NOT NULL CHECK (segment_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
            subrequest_id TEXT NOT NULL CHECK (length(subrequest_id) BETWEEN 1 AND 256),
            payload_digest TEXT NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
            route_payload JSONB,
            state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN
                ('pending','attempting','accepted','retry_wait','ambiguous','terminal_failed')),
            transport_outcome TEXT CHECK (transport_outcome IN
                ('confirmed','rejected','uncertain','not_attempted')),
            reason TEXT CHECK (reason IN
                ('target_unavailable','transport_timeout','overload_rejected','policy_denied',
                 'provider_rejected','binding_conflict','receipt_unavailable','worker_lost',
                 'attempt_budget_exhausted','age_budget_exhausted','payload_pruned')),
            receipt_id UUID,
            claim_id UUID,
            claimed_at TIMESTAMPTZ,
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deadline_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '1 day',
            next_attempt_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (ingestion_event_id, target_butler, segment_id),
            CHECK ((state = 'accepted') = (receipt_id IS NOT NULL)),
            CHECK ((claim_id IS NULL) = (claimed_at IS NULL)),
            CHECK ((state = 'attempting') = (claim_id IS NOT NULL)),
            CHECK (state <> 'terminal_failed' OR reason IS NOT NULL),
            CHECK (attempt_count <= max_attempts),
            CHECK (deadline_at > created_at AND deadline_at <= created_at + interval '7 days')
        );
        CREATE INDEX IF NOT EXISTS ix_ingestion_target_intents_waiting
            ON ingestion_target_intents (state, next_attempt_at, created_at)
            WHERE state IN ('pending','attempting','retry_wait','ambiguous');
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_ingestion_target_identity()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION 'ingestion target identity is retained' USING ERRCODE = '42501';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF TG_TABLE_NAME = 'ingestion_target_plans' THEN
                    NEW.committed_at := now();
                ELSE
                    NEW.created_at := now();
                    NEW.updated_at := now();
                    IF NEW.claim_id IS NOT NULL THEN NEW.claimed_at := now(); END IF;
                END IF;
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'ingestion_target_plans' THEN
                IF (to_jsonb(NEW) - 'classified_plan') IS DISTINCT FROM
                   (to_jsonb(OLD) - 'classified_plan')
                   OR (NEW.classified_plan IS NOT NULL
                       AND NEW.classified_plan IS DISTINCT FROM OLD.classified_plan) THEN
                    RAISE EXCEPTION 'ingestion target plan is immutable' USING ERRCODE = '42501';
                END IF;
            ELSE
                IF (NEW.ingestion_event_id, NEW.target_butler, NEW.segment_id,
                    NEW.subrequest_id, NEW.payload_digest, NEW.created_at,
                    NEW.max_attempts, NEW.deadline_at) IS DISTINCT FROM
                   (OLD.ingestion_event_id, OLD.target_butler, OLD.segment_id,
                    OLD.subrequest_id, OLD.payload_digest, OLD.created_at,
                    OLD.max_attempts, OLD.deadline_at)
                   OR (NEW.route_payload IS NOT NULL
                       AND NEW.route_payload IS DISTINCT FROM OLD.route_payload)
                   OR (OLD.state = 'accepted' AND
                       (to_jsonb(NEW) - 'route_payload') IS DISTINCT FROM
                       (to_jsonb(OLD) - 'route_payload')) THEN
                    RAISE EXCEPTION 'ingestion target intent is immutable' USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;
        END $$
        """
    )
    for table in ("ingestion_target_plans", "ingestion_target_intents"):
        op.execute(f"DROP TRIGGER IF EXISTS ingestion_target_identity ON {table}")
        op.execute(
            f"CREATE TRIGGER ingestion_target_identity BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_ingestion_target_identity()"
        )
        op.execute(f"DROP TRIGGER IF EXISTS ingestion_target_no_truncate ON {table}")
        op.execute(
            f"CREATE TRIGGER ingestion_target_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION guard_ingestion_target_identity()"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS ingestion_target_switchboard ON {table}")
        op.execute(
            f"CREATE POLICY ingestion_target_switchboard ON {table} "
            "USING (current_user = 'butler_switchboard_rw') "
            "WITH CHECK (current_user = 'butler_switchboard_rw')"
        )
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        op.execute(
            f"""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_switchboard_rw') THEN
                    REVOKE ALL ON {table} FROM butler_switchboard_rw;
                    GRANT SELECT, INSERT, UPDATE ON {table} TO butler_switchboard_rw;
                END IF;
            END $$
            """
        )


def downgrade() -> None:
    """Retain first decisions, target identities, state and receipts across rollback."""
    pass
