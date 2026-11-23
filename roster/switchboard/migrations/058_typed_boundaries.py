"""M7 typed boundaries and source containment on the goal ledger (bu-4c7nvp.1.2).

Revision ID: sw_058
Revises: sw_057

Four dark, insert-mostly stores. Each label column set is the sw_057 grammar
(``switchboard.goal_label_valid``); nothing here can narrow or clear a label.

* ``goal_handles``: quarantined content. A committed result whose label would
  expose its reader is held here with that label instead of reaching the
  model; the model holds only the handle id. Reading it (or extracting from
  it) joins the label into the reader. Content and label are immutable.
* ``extraction_cache``: a validated typed extraction keyed by the source
  content digest, the source label, the schema digest and the extractor model
  identity. It holds no caller identity, so a hit never carries another
  caller's principals; its label is the source's and is immutable.
* ``goal_child_results``: the closed result schema the runner fixes for a
  child at launch and, once, the child's validated ``goal.return`` value.
* ``source_containments``: the owner's step-up-confirmed containment of one
  admission source partition, with the goals descended from it at the time.
  History is permanent: no update, delete or truncate.

The guards are statement-level triggers (SQLSTATE 42501) like sw_056/sw_057,
because core_001 and init-db.sql replays re-grant own-schema DML. The ledger
tables' guard is named ``durable_goal_lineage`` like sw_057's. The update check
reruns as a non-deferrable AFTER constraint trigger on the final row, so a
later-sorting BEFORE trigger cannot rewrite a permitted update. The downgrade
refuses while any row exists.
"""

from alembic import op

revision = "sw_058"
down_revision = "sw_057"
branch_labels = None
depends_on = None

_LABEL = """
    label_version SMALLINT NOT NULL,
    label_instr TEXT[] NOT NULL,
    label_data TEXT[] NOT NULL,
    label_sens TEXT[] NOT NULL,
    CHECK (switchboard.goal_label_valid(label_version, label_instr, label_data, label_sens))
"""

_TABLES = f"""
    CREATE TABLE IF NOT EXISTS switchboard.goal_handles (
        id UUID PRIMARY KEY,
        goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
        invocation_id UUID NOT NULL,
        effect_id UUID,
        content JSONB NOT NULL,
        content_digest TEXT NOT NULL CHECK (content_digest ~ '^[0-9a-f]{{64}}$'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        {_LABEL},
        CONSTRAINT fk_handle_invocation FOREIGN KEY (goal_id, invocation_id)
            REFERENCES switchboard.invocations(goal_id, id)
    );
    CREATE INDEX IF NOT EXISTS ix_goal_handles_goal ON switchboard.goal_handles (goal_id);

    CREATE TABLE IF NOT EXISTS switchboard.extraction_cache (
        key TEXT PRIMARY KEY CHECK (key ~ '^[0-9a-f]{{64}}$'),
        schema_digest TEXT NOT NULL CHECK (schema_digest ~ '^[0-9a-f]{{64}}$'),
        extractor TEXT NOT NULL CHECK (extractor ~ '^[a-z0-9][a-z0-9_.:/-]{{0,127}}$'),
        value JSONB NOT NULL CHECK (jsonb_typeof(value) = 'object'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        {_LABEL}
    );

    CREATE TABLE IF NOT EXISTS switchboard.goal_child_results (
        goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
        invocation_id UUID NOT NULL,
        result_schema JSONB NOT NULL CHECK (jsonb_typeof(result_schema) = 'object'),
        schema_digest TEXT NOT NULL CHECK (schema_digest ~ '^[0-9a-f]{{64}}$'),
        result JSONB CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
        returned_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (goal_id, invocation_id),
        CONSTRAINT ck_child_result_returned CHECK ((result IS NULL) = (returned_at IS NULL)),
        CONSTRAINT fk_child_result_invocation FOREIGN KEY (goal_id, invocation_id)
            REFERENCES switchboard.invocations(goal_id, id)
    );

    CREATE TABLE IF NOT EXISTS switchboard.source_containments (
        id UUID PRIMARY KEY,
        source_partition TEXT NOT NULL UNIQUE
            CHECK (length(source_partition) BETWEEN 1 AND 256),
        request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{{64}}$'),
        descendant_goals UUID[] NOT NULL,
        contained_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
"""

_GUARDS = """
    -- A handle, a cache entry and a containment are written once. A child
    -- result row may only gain its one result.
    CREATE OR REPLACE FUNCTION switchboard.guard_typed_boundary()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        -- Nested, not AND-ed: PL/pgSQL resolves OLD.result even on tables
        -- without that column, so only goal_child_results may read it.
        IF TG_OP = 'UPDATE' AND TG_TABLE_NAME = 'goal_child_results' THEN
            IF (to_jsonb(OLD) ->> 'result') IS NULL
               AND (to_jsonb(NEW) ->> 'result') IS NOT NULL
               AND (to_jsonb(NEW) - ARRAY['result', 'returned_at'])
                   = (to_jsonb(OLD) - ARRAY['result', 'returned_at']) THEN
                RETURN NEW;
            END IF;
        END IF;
        RAISE EXCEPTION 'Typed boundary records are immutable'
            USING ERRCODE = 'insufficient_privilege';
    END $$;
    CREATE OR REPLACE FUNCTION switchboard.guard_typed_boundary_history()
    RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
    BEGIN
        RAISE EXCEPTION 'Typed boundary history is durable'
            USING ERRCODE = 'insufficient_privilege';
    END $$;
    DO $$
    DECLARE
        t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY['goal_handles', 'extraction_cache', 'goal_child_results',
                                 'source_containments'] LOOP
            EXECUTE format('DROP TRIGGER IF EXISTS immutable_typed_boundary ON switchboard.%I', t);
            EXECUTE format('CREATE TRIGGER immutable_typed_boundary BEFORE UPDATE '
                'ON switchboard.%I FOR EACH ROW '
                'EXECUTE FUNCTION switchboard.guard_typed_boundary()', t);
            -- The runtime role holds TRIGGER (core_001 re-grants it), so a later
            -- BEFORE trigger could rewrite the one permitted update. The same
            -- check reruns on the final row, as sw_057's settled triggers do.
            EXECUTE format('DROP TRIGGER IF EXISTS typed_boundary_settled ON switchboard.%I', t);
            EXECUTE format('CREATE CONSTRAINT TRIGGER typed_boundary_settled AFTER UPDATE '
                'ON switchboard.%I FOR EACH ROW '
                'EXECUTE FUNCTION switchboard.guard_typed_boundary()', t);
            EXECUTE format('DROP TRIGGER IF EXISTS durable_goal_lineage ON switchboard.%I', t);
            EXECUTE format('CREATE TRIGGER durable_goal_lineage BEFORE DELETE OR TRUNCATE '
                'ON switchboard.%I FOR EACH STATEMENT '
                'EXECUTE FUNCTION switchboard.guard_typed_boundary_history()', t);
            EXECUTE format('ALTER TABLE switchboard.%I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('REVOKE ALL ON switchboard.%I FROM PUBLIC, butler_switchboard_rw', t);
            EXECUTE format('GRANT SELECT, INSERT ON switchboard.%I TO butler_switchboard_rw', t);
            EXECUTE format('DROP POLICY IF EXISTS typed_boundary_read ON switchboard.%I', t);
            EXECUTE format('CREATE POLICY typed_boundary_read ON switchboard.%I FOR SELECT '
                'TO butler_switchboard_rw USING (current_user = ''butler_switchboard_rw'')', t);
            EXECUTE format('DROP POLICY IF EXISTS typed_boundary_insert ON switchboard.%I', t);
            EXECUTE format('CREATE POLICY typed_boundary_insert ON switchboard.%I FOR INSERT '
                'TO butler_switchboard_rw WITH CHECK (current_user = ''butler_switchboard_rw'')',
                t);
        END LOOP;
    END $$;
    GRANT UPDATE (result, returned_at) ON switchboard.goal_child_results
        TO butler_switchboard_rw;
    DROP POLICY IF EXISTS typed_boundary_return ON switchboard.goal_child_results;
    CREATE POLICY typed_boundary_return ON switchboard.goal_child_results FOR UPDATE
        TO butler_switchboard_rw USING (current_user = 'butler_switchboard_rw')
        WITH CHECK (current_user = 'butler_switchboard_rw');
"""


def upgrade() -> None:
    op.execute(_TABLES)
    op.execute(_GUARDS)


def downgrade() -> None:
    op.execute("""
        LOCK TABLE switchboard.goal_handles, switchboard.extraction_cache,
            switchboard.goal_child_results, switchboard.source_containments
            IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.goal_handles)
               OR EXISTS (SELECT 1 FROM switchboard.extraction_cache)
               OR EXISTS (SELECT 1 FROM switchboard.goal_child_results)
               OR EXISTS (SELECT 1 FROM switchboard.source_containments) THEN
                RAISE EXCEPTION 'Cannot discard durable typed boundary records';
            END IF;
        END $$;
        DROP TABLE switchboard.goal_handles;
        DROP TABLE switchboard.extraction_cache;
        DROP TABLE switchboard.goal_child_results;
        DROP TABLE switchboard.source_containments;
        DROP FUNCTION switchboard.guard_typed_boundary();
        DROP FUNCTION switchboard.guard_typed_boundary_history();
    """)
