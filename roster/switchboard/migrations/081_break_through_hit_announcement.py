"""Break-through hits carry their announcement state, so a lost announcement is retried (bu-s11n0s.8 S2).

Revision ID: sw_081
Revises: sw_080

sw_080 recorded each hit and left the announcement to the inserting call. A timeout or cancel
between the insert and the publish would lose the announcement for good, because a replay finds
the row and stays silent. A hit now carries its own announcement state:

* ``claimed_at`` is the lease of the call that is announcing it (the insert's own time at first);
* ``published_at`` is NULL until the announcement, the event and the candidate that drives
  delivery, completed.

A hit is announced by whoever holds a live lease. An unpublished hit whose lease has lapsed is
claimed again, by the hourly sweep or by a replay, and announced; a published hit is never
announced twice by a retry. The event is at-least-once across a crash between the publish and
the stamp, and consumers key on the clause and source request ids.

The hit also keeps what the announcement needs to be rebuilt without the message: the private
origin projection the evaluator matched (ids, the authenticated sender domain, the channel and
security kind, never text), the resolved sender's display name, the thread and the receive time
the "still unanswered" probe reads. Rows from sw_080 predate the announcement and are stamped
published, because that slice announced inline and sent nothing.

The record stays append-only apart from the two lease columns: a trigger refuses any update that
changes another column or clears ``published_at``, and refuses every delete and truncate.

The downgrade refuses while any hit exists, as sw_080's does.
"""

from alembic import op

revision = "sw_081"
down_revision = "sw_080"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE switchboard.break_through_hits
            ADD COLUMN origin JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ADD COLUMN sender_name TEXT,
            ADD COLUMN thread TEXT,
            ADD COLUMN received_at TIMESTAMPTZ,
            ADD COLUMN claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            ADD COLUMN published_at TIMESTAMPTZ;
        ALTER TABLE switchboard.break_through_hits DISABLE TRIGGER break_through_hits_durable;
        UPDATE switchboard.break_through_hits SET published_at = observed_at;
        ALTER TABLE switchboard.break_through_hits ENABLE TRIGGER break_through_hits_durable;
        DROP TRIGGER break_through_hits_durable ON switchboard.break_through_hits;
        DROP FUNCTION switchboard.break_through_hits_durable();

        CREATE FUNCTION switchboard.break_through_hits_no_discard()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Break-through hits are durable'
                USING ERRCODE = 'insufficient_privilege';
        END $$;
        CREATE TRIGGER break_through_hits_no_discard
            BEFORE DELETE OR TRUNCATE ON switchboard.break_through_hits
            FOR EACH STATEMENT EXECUTE FUNCTION switchboard.break_through_hits_no_discard();

        CREATE FUNCTION switchboard.break_through_hits_lease_only()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            IF (to_jsonb(NEW) - 'claimed_at' - 'published_at')
                   IS DISTINCT FROM (to_jsonb(OLD) - 'claimed_at' - 'published_at')
               OR (OLD.published_at IS NOT NULL
                   AND NEW.published_at IS DISTINCT FROM OLD.published_at) THEN
                RAISE EXCEPTION 'Break-through hits are durable'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER break_through_hits_lease_only
            BEFORE UPDATE ON switchboard.break_through_hits
            FOR EACH ROW EXECUTE FUNCTION switchboard.break_through_hits_lease_only();

        GRANT UPDATE (claimed_at, published_at) ON switchboard.break_through_hits TO {_RUNTIME};
        CREATE POLICY break_through_hits_lease ON switchboard.break_through_hits
            FOR UPDATE TO {_RUNTIME}
            USING (current_user = '{_RUNTIME}') WITH CHECK (current_user = '{_RUNTIME}');
        CREATE INDEX ix_break_through_hits_unpublished
            ON switchboard.break_through_hits (claimed_at) WHERE published_at IS NULL;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.break_through_hits) THEN
                RAISE EXCEPTION 'Cannot discard break-through hits';
            END IF;
        END $$;
        DROP TRIGGER IF EXISTS break_through_hits_lease_only ON switchboard.break_through_hits;
        DROP FUNCTION IF EXISTS switchboard.break_through_hits_lease_only();
        DROP TRIGGER IF EXISTS break_through_hits_no_discard ON switchboard.break_through_hits;
        DROP FUNCTION IF EXISTS switchboard.break_through_hits_no_discard();
        DROP POLICY IF EXISTS break_through_hits_lease ON switchboard.break_through_hits;
        DROP INDEX IF EXISTS switchboard.ix_break_through_hits_unpublished;
        REVOKE UPDATE ON switchboard.break_through_hits FROM butler_switchboard_rw;
        ALTER TABLE switchboard.break_through_hits
            DROP COLUMN origin, DROP COLUMN sender_name, DROP COLUMN thread,
            DROP COLUMN received_at, DROP COLUMN claimed_at, DROP COLUMN published_at;
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
