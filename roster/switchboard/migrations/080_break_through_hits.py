"""Break-through hits: one durable row per (owner clause, inbound message) match (bu-s11n0s.8).

Revision ID: sw_080
Revises: sw_079

Slice 1 of the owner break-through feature is shadow-only: the ingest sensor asks the fixed
evaluator whether an inbound message matches a current owner clause and records the match here,
then announces it as a metadata-only ``switchboard.break_through_hit`` event. The primary key
``(clause_id, source_request_id)`` is the replay guard: the sensor inserts with
``ON CONFLICT DO NOTHING`` and only the inserter publishes, so a connector replay emits no second
event and two concurrent replays cannot both win.

The row is content-blind: ids, the channel and a time, never a body, subject or address. It
carries no foreign key to the clause (the clause store is owner-only and a revoked clause leaves
its past hits in place). The runtime role may insert and read; nothing updates, deletes or
truncates a hit, so the record of what the sensor saw cannot be rewritten.

The downgrade refuses while any hit exists.
"""

from alembic import op

revision = "sw_080"
down_revision = "sw_079"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE switchboard.break_through_hits (
            clause_id UUID NOT NULL,
            source_request_id UUID NOT NULL,
            channel TEXT NOT NULL,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (clause_id, source_request_id)
        );
        CREATE INDEX ix_break_through_hits_clause
            ON switchboard.break_through_hits (clause_id, observed_at);

        ALTER TABLE switchboard.break_through_hits ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON switchboard.break_through_hits FROM PUBLIC;
        GRANT SELECT, INSERT ON switchboard.break_through_hits TO {_RUNTIME};
        CREATE POLICY break_through_hits_read ON switchboard.break_through_hits
            FOR SELECT TO {_RUNTIME} USING (current_user = '{_RUNTIME}');
        CREATE POLICY break_through_hits_insert ON switchboard.break_through_hits
            FOR INSERT TO {_RUNTIME} WITH CHECK (current_user = '{_RUNTIME}');

        CREATE FUNCTION switchboard.break_through_hits_durable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Break-through hits are durable'
                USING ERRCODE = 'insufficient_privilege';
        END $$;
        CREATE TRIGGER break_through_hits_durable
            BEFORE UPDATE OR DELETE OR TRUNCATE ON switchboard.break_through_hits
            FOR EACH STATEMENT EXECUTE FUNCTION switchboard.break_through_hits_durable();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.break_through_hits) THEN
                RAISE EXCEPTION 'Cannot discard break-through hits';
            END IF;
        END $$;
        DROP TABLE IF EXISTS switchboard.break_through_hits;
        DROP FUNCTION IF EXISTS switchboard.break_through_hits_durable();
    """)
