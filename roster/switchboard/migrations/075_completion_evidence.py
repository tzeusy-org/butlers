"""Completion evidence: plan requirements and the receipts a settled goal was judged by.

Revision ID: sw_075
Revises: sw_074

RFC 0038 D11 and D13 (bu-4c7nvp.1.3, PR-C). A goal counts as ``completed`` only when receipts
prove what its plan requires (``butlers.goal_runner.evidence``). Dark: until
``EVIDENCE_COMPLETION_LIVE`` flips, nothing writes either structure below.

- ``goal_plans.requirements``: the plan version's requirements, each ``{ref, evidence, anchor}``
  (at most 64), folded into the version's digest when present. A version's owner-anchored
  requirements (server facts, never model text) are kept by every later version: an insert that
  drops or changes one is refused. A plan version stays append-only (sw_063).
- ``goal_outcome_evidence``: one row per goal, written once when the settle resolution judges it:
  the final status and the requirements it found proved (``satisfied``) and unproved
  (``missing``, each with the status it found). Append-only and durable. Its schema-default
  table privileges are revoked first, then the runtime role is granted read and insert only, and
  an UPDATE is refused per statement, so a refused edit is never a silent zero-row no-op under
  row-level security.

The downgrade refuses while any evidence row or plan requirement exists.
"""

from alembic import op

revision = "sw_075"
down_revision = "sw_074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE switchboard.goal_plans
            ADD COLUMN requirements JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (
                jsonb_typeof(requirements) = 'array' AND jsonb_array_length(requirements) <= 64);

        -- Every requirement is a closed shape, and a later version keeps each owner anchor of
        -- its predecessor exactly.
        CREATE FUNCTION switchboard.goal_plan_requirements()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        DECLARE
            item JSONB;
            previous JSONB;
        BEGIN
            FOR item IN SELECT value FROM jsonb_array_elements(NEW.requirements) LOOP
                IF jsonb_typeof(item) <> 'object'
                   OR (SELECT count(*) FROM jsonb_object_keys(item)) <> 3
                   OR NOT (item ? 'ref' AND item ? 'evidence' AND item ? 'anchor')
                   OR item->>'anchor' NOT IN ('owner','lead')
                   OR NOT (
                       (item->>'ref' ~ '^effect:[0-9a-f-]{36}$'
                            AND item->>'evidence' IN ('committed','owner_delivered'))
                       OR (item->>'ref' ~ '^child:[0-9a-f-]{36}$'
                            AND item->>'evidence' = 'returned')
                       OR (item->>'ref' ~ '^inbox:[0-9a-f-]{36}$'
                            AND item->>'evidence' = 'consumed')
                       OR (item->>'ref' = 'reply' AND item->>'evidence' = 'owner_delivered')) THEN
                    RAISE EXCEPTION 'A plan requirement is {ref, evidence, anchor} of a known kind';
                END IF;
            END LOOP;
            IF NEW.predecessor_version IS NOT NULL THEN
                SELECT requirements INTO previous FROM switchboard.goal_plans
                 WHERE goal_id = NEW.goal_id AND version = NEW.predecessor_version;
                FOR item IN SELECT value FROM jsonb_array_elements(coalesce(previous, '[]'))
                    LOOP
                    IF item->>'anchor' = 'owner'
                       AND NOT NEW.requirements @> jsonb_build_array(item) THEN
                        RAISE EXCEPTION 'A plan version keeps every owner-anchored requirement';
                    END IF;
                END LOOP;
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER goal_plan_requirements BEFORE INSERT ON switchboard.goal_plans
            FOR EACH ROW EXECUTE FUNCTION switchboard.goal_plan_requirements();

        CREATE TABLE switchboard.goal_outcome_evidence (
            goal_id UUID PRIMARY KEY REFERENCES switchboard.goals(id),
            final_status TEXT NOT NULL CHECK (final_status IN ('completed','partial','failed')),
            satisfied JSONB NOT NULL CHECK (
                jsonb_typeof(satisfied) = 'array' AND jsonb_array_length(satisfied) <= 128),
            missing JSONB NOT NULL CHECK (
                jsonb_typeof(missing) = 'array' AND jsonb_array_length(missing) <= 128),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT ck_evidence_completed CHECK (
                final_status <> 'completed' OR jsonb_array_length(missing) = 0),
            CONSTRAINT fk_evidence_outcome FOREIGN KEY (goal_id)
                REFERENCES switchboard.goal_outcomes(goal_id)
        );
        CREATE FUNCTION switchboard.goal_outcome_evidence_append_only()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Completion evidence is append-only';
        END $$;
        CREATE FUNCTION switchboard.goal_outcome_evidence_durable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Completion evidence is durable' USING ERRCODE = 'insufficient_privilege';
        END $$;
        ALTER TABLE switchboard.goal_outcome_evidence ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON switchboard.goal_outcome_evidence FROM PUBLIC;
        -- The schema's default privileges grant the runtime role every table privilege.
        REVOKE ALL ON switchboard.goal_outcome_evidence FROM butler_switchboard_rw;
        GRANT SELECT, INSERT ON switchboard.goal_outcome_evidence TO butler_switchboard_rw;
        CREATE POLICY goal_outcome_evidence_read ON switchboard.goal_outcome_evidence FOR SELECT TO butler_switchboard_rw
            USING (current_user = 'butler_switchboard_rw');
        CREATE POLICY goal_outcome_evidence_insert ON switchboard.goal_outcome_evidence FOR INSERT TO butler_switchboard_rw
            WITH CHECK (current_user = 'butler_switchboard_rw');
        -- Per statement: refused even when row-level security narrows it to no rows.
        CREATE TRIGGER goal_outcome_evidence_append_only BEFORE UPDATE ON switchboard.goal_outcome_evidence
            FOR EACH STATEMENT EXECUTE FUNCTION switchboard.goal_outcome_evidence_append_only();
        CREATE TRIGGER goal_outcome_evidence_durable BEFORE DELETE OR TRUNCATE ON switchboard.goal_outcome_evidence
            FOR EACH STATEMENT EXECUTE FUNCTION switchboard.goal_outcome_evidence_durable();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF (to_regclass('switchboard.goal_outcome_evidence') IS NOT NULL
                AND EXISTS (SELECT 1 FROM switchboard.goal_outcome_evidence))
               OR EXISTS (SELECT 1 FROM switchboard.goal_plans WHERE requirements <> '[]') THEN
                RAISE EXCEPTION 'Cannot discard completion evidence';
            END IF;
        END $$;
        DROP TABLE IF EXISTS switchboard.goal_outcome_evidence;
        DROP FUNCTION IF EXISTS switchboard.goal_outcome_evidence_append_only();
        DROP FUNCTION IF EXISTS switchboard.goal_outcome_evidence_durable();
        DROP TRIGGER IF EXISTS goal_plan_requirements ON switchboard.goal_plans;
        DROP FUNCTION IF EXISTS switchboard.goal_plan_requirements();
        ALTER TABLE switchboard.goal_plans DROP COLUMN requirements;
    """)
