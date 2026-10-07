"""Server-stamped fact authority, unavailable reporters and candidate review.

Revision ID: rel_037
Revises: rel_036

Existing rows remain NULL/unclassified and resolvable. Reporter deletion clears
only the live pointer; the original UUID is inert provenance, never a lookup.
Subject/object deletion and the rel_035 transition indexes remain unchanged.
"""

from __future__ import annotations

from alembic import op

revision = "rel_037"
down_revision = "rel_036"
branch_labels = None
depends_on = None

AUTHORITY_COLUMNS = """
    ADD COLUMN content_authority TEXT,
    ADD COLUMN authority_entity_id UUID REFERENCES public.entities(id) ON DELETE SET NULL,
    ADD COLUMN authority_original_entity_id UUID,
    ADD COLUMN authority_entity_created_at TIMESTAMPTZ,
    ADD COLUMN confirmed_by_entity_id UUID REFERENCES public.entities(id) ON DELETE SET NULL,
    ADD COLUMN confirmed_by_original_entity_id UUID,
    ADD COLUMN confirmed_at TIMESTAMPTZ,
    ADD COLUMN confirmation_source TEXT
"""


def upgrade_statements() -> tuple[str, ...]:
    statements: list[str] = []
    execute = statements.append
    execute(f"ALTER TABLE relationship.entity_facts {AUTHORITY_COLUMNS}")
    execute("""
        ALTER TABLE relationship.entity_facts
        ADD CONSTRAINT ck_ef_content_authority CHECK (
            content_authority IS NULL OR content_authority IN
            ('owner','owner_device','third_party','system','mixed')),
        ADD CONSTRAINT ck_ef_live_reporter CHECK (authority_entity_id IS NULL OR
            (authority_original_entity_id IS NOT NULL AND
             authority_entity_id = authority_original_entity_id AND
             authority_entity_created_at IS NOT NULL)),
        ADD CONSTRAINT ck_ef_confirmation CHECK (
            (confirmed_at IS NULL AND confirmed_by_entity_id IS NULL AND
             confirmed_by_original_entity_id IS NULL AND confirmation_source IS NULL)
            OR (confirmed_at IS NOT NULL AND confirmed_by_original_entity_id IS NOT NULL
                AND confirmation_source IS NOT NULL)),
        ADD CONSTRAINT ck_ef_verified_authority CHECK (content_authority IS NULL OR
            verified = (content_authority IN ('owner','owner_device') OR confirmed_at IS NOT NULL))
    """)
    # Replace only the original validity vocabulary check, not temporal checks.
    execute("""
        DO $$ DECLARE constraint_name TEXT; BEGIN
            FOR constraint_name IN SELECT conname FROM pg_constraint
            WHERE conrelid='relationship.entity_facts'::regclass AND contype='c'
              AND conname='entity_facts_validity_check'
            LOOP EXECUTE format('ALTER TABLE relationship.entity_facts DROP CONSTRAINT %I',
                                constraint_name); END LOOP;
        END $$;
        ALTER TABLE relationship.entity_facts ADD CONSTRAINT ck_ef_validity
            CHECK (validity IN ('active','candidate','retracted','superseded'));
        CREATE UNIQUE INDEX uq_ef_spo_occurrence_candidate
        ON relationship.entity_facts
          (subject,predicate,object,
           COALESCE(effective_period_id,'00000000-0000-0000-0000-000000000000'::uuid))
        WHERE validity='candidate';
        CREATE FUNCTION relationship.guard_fact_report_authority() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.content_authority IS DISTINCT FROM OLD.content_authority OR
               NEW.authority_original_entity_id IS DISTINCT FROM OLD.authority_original_entity_id OR
               NEW.authority_entity_created_at IS DISTINCT FROM OLD.authority_entity_created_at OR
               (NEW.authority_entity_id IS DISTINCT FROM OLD.authority_entity_id AND
                NEW.authority_entity_id IS NOT NULL) THEN
                RAISE EXCEPTION 'fact report attribution is immutable within a version';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER guard_fact_report_authority BEFORE UPDATE ON relationship.entity_facts
            FOR EACH ROW EXECUTE FUNCTION relationship.guard_fact_report_authority();
        ALTER TABLE relationship.fact_approval_context
          ADD COLUMN frozen_report JSONB,
          ADD COLUMN decision_report JSONB;
        CREATE TABLE relationship.fact_approval_rule_context (
            rule_id UUID PRIMARY KEY,
            rule_created_at TIMESTAMPTZ NOT NULL,
            rule_args_digest TEXT NOT NULL,
            owner_report JSONB NOT NULL,
            creation_event_id TEXT NOT NULL
        );
        CREATE TABLE relationship.fact_identity_decisions (
            fact_id UUID PRIMARY KEY REFERENCES relationship.entity_facts(id) ON DELETE CASCADE,
            decision TEXT NOT NULL CHECK(decision IN ('adopt','reject','confirm')),
            decided_at TIMESTAMPTZ NOT NULL,
            owner_entity_id UUID REFERENCES public.entities(id) ON DELETE SET NULL,
            owner_original_entity_id UUID NOT NULL,
            fact_result_id UUID REFERENCES relationship.entity_facts(id) ON DELETE SET NULL
        );
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='butler_relationship_rw') THEN
                GRANT SELECT,INSERT,UPDATE,DELETE ON relationship.fact_identity_decisions,
                    relationship.fact_approval_rule_context
                    TO butler_relationship_rw;
            END IF;
        END $$;
    """)

    return tuple(statements)


def upgrade() -> None:
    for statement in upgrade_statements():
        op.execute(statement)


def downgrade() -> None:
    # No destructive rollback of classified provenance, confirmations or review.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM relationship.entity_facts WHERE
                content_authority IS NOT NULL OR authority_original_entity_id IS NOT NULL OR
                confirmed_at IS NOT NULL OR validity='candidate') OR
               EXISTS(SELECT 1 FROM relationship.fact_approval_context WHERE
                    frozen_report IS NOT NULL OR decision_report IS NOT NULL) OR
               EXISTS(SELECT 1 FROM relationship.fact_identity_decisions) OR
               EXISTS(SELECT 1 FROM relationship.fact_approval_rule_context) THEN
                RAISE EXCEPTION 'authority data exists; roll forward instead of erasing provenance';
            END IF;
        END $$;
        DROP TABLE relationship.fact_identity_decisions;
        DROP TABLE relationship.fact_approval_rule_context;
        ALTER TABLE relationship.fact_approval_context DROP COLUMN frozen_report,
            DROP COLUMN decision_report;
        DROP TRIGGER guard_fact_report_authority ON relationship.entity_facts;
        DROP FUNCTION relationship.guard_fact_report_authority();
        DROP INDEX relationship.uq_ef_spo_occurrence_candidate;
        ALTER TABLE relationship.entity_facts DROP CONSTRAINT ck_ef_validity,
            ADD CONSTRAINT entity_facts_validity_check
            CHECK (validity IN ('active','retracted','superseded')),
            DROP CONSTRAINT ck_ef_content_authority,
            DROP CONSTRAINT ck_ef_live_reporter,
            DROP CONSTRAINT ck_ef_confirmation,
            DROP CONSTRAINT ck_ef_verified_authority,
            DROP COLUMN content_authority, DROP COLUMN authority_entity_id,
            DROP COLUMN authority_original_entity_id, DROP COLUMN authority_entity_created_at,
            DROP COLUMN confirmed_by_entity_id, DROP COLUMN confirmed_by_original_entity_id,
            DROP COLUMN confirmed_at, DROP COLUMN confirmation_source;
    """)
