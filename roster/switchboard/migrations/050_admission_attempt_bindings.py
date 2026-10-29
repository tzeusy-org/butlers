"""Bind every returned admission attempt to its immutable canonical decision.

Revision ID: sw_050
Revises: sw_047 (048/049 are separately held, undelivered allocations).

Ordinary-owner maintenance remains lossless. Runtime RLS, immutable bindings
and the composite FK preserve source identity after bootstrap DML regrants.
"""

from alembic import op

revision = "sw_050"
down_revision = "sw_047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX uq_admission_decision_source
            ON switchboard.admission_decisions(id,source_key);
        CREATE TABLE switchboard.admission_attempt_bindings (
            attempt_id UUID PRIMARY KEY,
            source_key TEXT NOT NULL,
            decision_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (decision_id,source_key)
                REFERENCES switchboard.admission_decisions(id,source_key)
        );
        INSERT INTO switchboard.admission_attempt_bindings(attempt_id,source_key,decision_id)
            SELECT attempt_id,source_key,id FROM switchboard.admission_decisions;
        CREATE FUNCTION switchboard.guard_admission_attempt_binding()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN RAISE EXCEPTION 'Admission attempt bindings are immutable'; END $$;
        CREATE TRIGGER immutable_admission_attempt_binding BEFORE UPDATE OR DELETE
            ON switchboard.admission_attempt_bindings FOR EACH ROW
            EXECUTE FUNCTION switchboard.guard_admission_attempt_binding();
        ALTER TABLE switchboard.admission_attempt_bindings ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON switchboard.admission_attempt_bindings FROM PUBLIC;
        GRANT SELECT,INSERT ON switchboard.admission_attempt_bindings TO butler_switchboard_rw;
        CREATE POLICY admission_attempt_read ON switchboard.admission_attempt_bindings
            FOR SELECT TO butler_switchboard_rw USING(current_user='butler_switchboard_rw');
        CREATE POLICY admission_attempt_insert ON switchboard.admission_attempt_bindings
            FOR INSERT TO butler_switchboard_rw WITH CHECK(current_user='butler_switchboard_rw');
        CREATE POLICY admission_attempt_update ON switchboard.admission_attempt_bindings
            FOR UPDATE TO butler_switchboard_rw USING(false) WITH CHECK(false);
        CREATE POLICY admission_attempt_delete ON switchboard.admission_attempt_bindings
            FOR DELETE TO butler_switchboard_rw USING(false);
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN (
                'butler_chronicler_rw','butler_concierge_rw','butler_education_rw',
                'butler_finance_rw','butler_general_rw','butler_health_rw','butler_home_rw',
                'butler_lifestyle_rw','butler_messenger_rw','butler_qa_rw',
                'butler_relationship_rw','butler_switchboard_rw','butler_travel_rw',
                'butler_calendar_rw','connector_writer')
                AND (rolsuper OR rolbypassrls OR pg_has_role(oid,
                    (SELECT relowner FROM pg_class WHERE
                     oid='switchboard.admission_attempt_bindings'::regclass),'MEMBER'))) THEN
                RAISE EXCEPTION 'Runtime may not bypass or own admission attempts';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        LOCK TABLE switchboard.admission_attempt_bindings IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.admission_attempt_bindings) THEN
                RAISE EXCEPTION 'Cannot discard durable admission attempts';
            END IF;
        END $$;
        DROP TABLE switchboard.admission_attempt_bindings;
        DROP FUNCTION switchboard.guard_admission_attempt_binding();
        DROP INDEX switchboard.uq_admission_decision_source;
    """)
