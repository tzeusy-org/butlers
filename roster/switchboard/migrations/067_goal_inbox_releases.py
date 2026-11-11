"""A goal that ends releases the owner replies it joined but never read.

Revision ID: sw_067
Revises: sw_066

RFC 0038 S2.2 (bu-dhloi1.305, live-flip gate G1). A reply joins a goal as an unread
``owner_reply`` inbox row. When the goal waits on a conditions envelope that names the prompt
the reply stays unread until the goal-event feed wakes the goal. If the goal ends first, at its
deadline, by Stop, by failure or by budget, no lead will ever read it. Such a reply must not be
swallowed silently.

``switchboard.goal_inbox_releases`` records each such row once: the inbox row, its goal, why it
was released and when. One trigger writes it, on the goal's own transition into a terminal
status, so every end path is covered and none can forget. The reason is the goal's terminal
status, except that a failed or partial end with an expired wait is ``deadline``.

The record is content-blind: ids, a fixed reason and a time, never the reply. The runtime role
may read it and nothing else; only the trigger (``SECURITY DEFINER``, pinned search path)
writes it, and nothing updates or deletes it. The owner-failure ask reads the count.

The downgrade refuses while any release exists.
"""

from alembic import op

revision = "sw_067"
down_revision = "sw_066"
branch_labels = None
depends_on = None

_RUNTIME = "butler_switchboard_rw"
_TERMINAL = "('completed','partial','failed','stopped','budget_exhausted')"


def upgrade() -> None:
    op.execute("LOCK TABLE switchboard.goals IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        CREATE TABLE switchboard.goal_inbox_releases (
            inbox_id UUID PRIMARY KEY REFERENCES switchboard.goal_inbox(id),
            goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
            reason TEXT NOT NULL CHECK (reason IN
                ('deadline','stopped','failed','partial','completed','budget_exhausted')),
            released_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ix_goal_inbox_releases_goal ON switchboard.goal_inbox_releases(goal_id);
    """)
    op.execute(f"""
        ALTER TABLE switchboard.goal_inbox_releases ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON switchboard.goal_inbox_releases FROM PUBLIC;
        GRANT SELECT ON switchboard.goal_inbox_releases TO {_RUNTIME};
        CREATE POLICY inbox_release_read ON switchboard.goal_inbox_releases
            FOR SELECT TO {_RUNTIME} USING (current_user = '{_RUNTIME}');

        CREATE FUNCTION switchboard.goal_inbox_releases_durable()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Released replies are durable' USING ERRCODE = 'insufficient_privilege';
        END $$;
        CREATE TRIGGER goal_inbox_releases_durable
            BEFORE UPDATE OR DELETE OR TRUNCATE ON switchboard.goal_inbox_releases
            FOR EACH STATEMENT EXECUTE FUNCTION switchboard.goal_inbox_releases_durable();

        CREATE FUNCTION switchboard.goal_release_unread()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE
            why TEXT := NEW.status;
        BEGIN
            IF NEW.status IN ('failed','partial') AND EXISTS (
                   SELECT 1 FROM switchboard.wait_envelopes
                   WHERE goal_id = NEW.id AND state = 'expired') THEN
                why := 'deadline';
            END IF;
            INSERT INTO switchboard.goal_inbox_releases (inbox_id, goal_id, reason)
                SELECT id, goal_id, why FROM switchboard.goal_inbox
                WHERE goal_id = NEW.id AND input_kind = 'owner_reply' AND consumed_at IS NULL
                ON CONFLICT (inbox_id) DO NOTHING;
            RETURN NULL;
        END $$;
        REVOKE ALL ON FUNCTION switchboard.goal_release_unread() FROM PUBLIC;
        CREATE TRIGGER goal_release_unread AFTER UPDATE OF status ON switchboard.goals
            FOR EACH ROW
            WHEN (NEW.status IN {_TERMINAL} AND OLD.status NOT IN {_TERMINAL})
            EXECUTE FUNCTION switchboard.goal_release_unread();
    """)


def downgrade() -> None:
    op.execute("LOCK TABLE switchboard.goals IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.goal_inbox_releases) THEN
                RAISE EXCEPTION 'Cannot discard released owner replies';
            END IF;
        END $$;
        DROP TRIGGER IF EXISTS goal_release_unread ON switchboard.goals;
        DROP FUNCTION IF EXISTS switchboard.goal_release_unread();
        DROP TABLE IF EXISTS switchboard.goal_inbox_releases;
        DROP FUNCTION IF EXISTS switchboard.goal_inbox_releases_durable();
    """)
