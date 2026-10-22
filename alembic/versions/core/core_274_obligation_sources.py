"""Owning-schema obligation revisions and transactional reference-only outbox.

Revision ID: core_274
Revises: core_273

No Switchboard/control schema dependency or live admission wiring. Definitions
are tombstoned, not erased: immutable revisions, commands and outbox evidence
survive source retirement. Nonempty evidence refuses downgrade.
"""

from alembic import op

revision = "core_274"
down_revision = "core_273"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS obligation_definitions (
            id UUID PRIMARY KEY,
            owner_domain TEXT NOT NULL,
            family TEXT NOT NULL CHECK (family IN
                ('schedule','deadline','chain','subscription','model_job')),
            origin TEXT NOT NULL CHECK (origin IN ('git','runtime')),
            creator JSONB,
            revision TEXT NOT NULL CHECK (revision ~ '^[0-9a-f]{64}$'),
            generation BIGINT NOT NULL CHECK (generation > 0),
            state TEXT NOT NULL CHECK (state IN ('active','disabled','deleted')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((origin='runtime') = (creator IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS obligation_revisions (
            source_id UUID NOT NULL REFERENCES obligation_definitions(id),
            revision TEXT NOT NULL CHECK (revision ~ '^[0-9a-f]{64}$'),
            generation BIGINT NOT NULL CHECK (generation > 0),
            body JSONB NOT NULL CHECK (jsonb_typeof(body)='object'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_id,revision), UNIQUE (source_id,generation)
        )
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid='obligation_definitions'::regclass
                  AND conname='fk_obligation_current_revision'
            ) THEN
                ALTER TABLE obligation_definitions
                ADD CONSTRAINT fk_obligation_current_revision
                FOREIGN KEY (id,revision) REFERENCES obligation_revisions(source_id,revision)
                DEFERRABLE INITIALLY DEFERRED;
            END IF;
        END $$
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS obligation_source_commands (
            id UUID PRIMARY KEY,
            source_id UUID NOT NULL REFERENCES obligation_definitions(id),
            request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
            body JSONB NOT NULL CHECK (jsonb_typeof(body)='object'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS obligation_occurrences (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id UUID NOT NULL,
            revision TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE CHECK (source_key ~ '^[0-9a-f]{64}$'),
            source_kind TEXT NOT NULL CHECK (source_kind IN ('occurrence','domain','feed')),
            source_ref TEXT NOT NULL CHECK (length(source_ref) BETWEEN 1 AND 256),
            occurrence TEXT,
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending','claimed','cancelled')),
            claim_token UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (source_id,revision) REFERENCES obligation_revisions(source_id,revision),
            CHECK ((source_kind='occurrence') = (occurrence IS NOT NULL)),
            CHECK (occurrence IS NULL OR length(occurrence) BETWEEN 1 AND 256),
            CHECK (state <> 'claimed' OR claim_token IS NOT NULL),
            CHECK (state <> 'pending' OR claim_token IS NULL)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_obligation_occurrences_source_state
        ON obligation_occurrences(source_id,state)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS obligation_source_outbox (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_key TEXT NOT NULL UNIQUE CHECK (event_key ~ '^[0-9a-f]{64}$'),
            source_id UUID NOT NULL,
            revision TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN
                ('definition_changed','occurrence_captured','occurrence_cancelled')),
            reference JSONB NOT NULL CHECK (jsonb_typeof(reference)='object'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY (source_id,revision) REFERENCES obligation_revisions(source_id,revision)
        )
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION obligation_evidence_immutable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
            RAISE EXCEPTION 'obligation evidence is immutable';
        END $$
    """)
    for table in ("obligation_revisions", "obligation_source_commands", "obligation_source_outbox"):
        op.execute(f"DROP TRIGGER IF EXISTS obligation_evidence_immutable ON {table}")
        op.execute(f"""
            CREATE TRIGGER obligation_evidence_immutable BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION obligation_evidence_immutable()
        """)
    op.execute("""
        CREATE OR REPLACE FUNCTION obligation_definition_guard()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'obligation source must be tombstoned';
            END IF;
            IF ROW(NEW.id,NEW.owner_domain,NEW.family,NEW.origin,NEW.creator,NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id,OLD.owner_domain,OLD.family,OLD.origin,OLD.creator,OLD.created_at)
               OR OLD.state='deleted' OR NEW.generation <> OLD.generation+1
               OR NEW.revision=OLD.revision THEN
                RAISE EXCEPTION 'obligation creation binding or generation is immutable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("DROP TRIGGER IF EXISTS obligation_definition_guard ON obligation_definitions")
    op.execute("""
        CREATE TRIGGER obligation_definition_guard BEFORE UPDATE OR DELETE
        ON obligation_definitions FOR EACH ROW EXECUTE FUNCTION obligation_definition_guard()
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION obligation_occurrence_guard()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'obligation occurrence is durable evidence';
            END IF;
            IF ROW(NEW.id,NEW.source_id,NEW.revision,NEW.source_key,NEW.source_kind,
                   NEW.source_ref,NEW.occurrence,NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id,OLD.source_id,OLD.revision,OLD.source_key,OLD.source_kind,
                   OLD.source_ref,OLD.occurrence,OLD.created_at)
               OR OLD.state='cancelled'
               OR (OLD.claim_token IS NOT NULL AND NEW.claim_token IS DISTINCT FROM OLD.claim_token)
               OR (OLD.state='claimed' AND NEW.state NOT IN ('claimed','cancelled')) THEN
                RAISE EXCEPTION 'obligation occurrence binding or claim is immutable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("DROP TRIGGER IF EXISTS obligation_occurrence_guard ON obligation_occurrences")
    op.execute("""
        CREATE TRIGGER obligation_occurrence_guard BEFORE UPDATE OR DELETE
        ON obligation_occurrences FOR EACH ROW EXECUTE FUNCTION obligation_occurrence_guard()
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM obligation_definitions)
                OR EXISTS (SELECT 1 FROM obligation_source_outbox) THEN
                RAISE EXCEPTION 'Cannot discard obligation evidence; use forward remediation';
            END IF;
        END $$
    """)
    op.execute(
        "ALTER TABLE obligation_definitions "
        "DROP CONSTRAINT IF EXISTS fk_obligation_current_revision"
    )
    for table in (
        "obligation_source_outbox",
        "obligation_occurrences",
        "obligation_source_commands",
        "obligation_revisions",
        "obligation_definitions",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for function in (
        "obligation_occurrence_guard",
        "obligation_definition_guard",
        "obligation_evidence_immutable",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}()")
