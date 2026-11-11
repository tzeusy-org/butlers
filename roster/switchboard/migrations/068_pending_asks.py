"""Pending asks: one identity for an owner request that failed without telling the owner.

Revision ID: sw_068
Revises: sw_067

RFC 0038 S6 (bu-dhloi1.14, package 3). An owner message that became a goal and ended
``failed`` or ``budget_exhausted`` with no owner delivery in its own lineage leaves the owner
with silence. The goal runner's sweep records one ``pending_asks`` row per such goal and
sends one model-free cause line. ``source_key`` is the goal's own ``source_key``, so one
failure has exactly one identity however many sweeps see it.

This revision stores identity and cause only. ``cause`` is a closed word; no ask text, token
or provider detail is kept here. ``state`` already admits the values the recovery package
will write (Run, Drop, ack, the 24 hour stale expiry) so that package needs no CHECK change;
nothing writes them yet. ``goal_id`` is nullable and ``source_key`` is not a goal reference,
for the later refused-admission producer, which has no goal row.

Runtime access is ordinary-owner, non-FORCE RLS: the Switchboard runtime role may read,
insert and update. The downgrade refuses while a row exists, because a pending ask is the
only record that the owner was never told.
"""

from alembic import op

revision = "sw_068"
down_revision = "sw_067"
branch_labels = None
depends_on = None

_CAUSES = "('failed','budget_exhausted','capability_unavailable','admission_refused')"
_STATES = "('open','run','dropped','dropped_stale','acked')"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS switchboard.pending_asks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_key TEXT NOT NULL UNIQUE,
            goal_id UUID REFERENCES switchboard.goals(id),
            cause TEXT NOT NULL CHECK (cause IN {_CAUSES}),
            state TEXT NOT NULL DEFAULT 'open' CHECK (state IN {_STATES}),
            subject_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS ix_pending_asks_open
            ON switchboard.pending_asks (created_at) WHERE state = 'open';
        ALTER TABLE switchboard.pending_asks ENABLE ROW LEVEL SECURITY;
        REVOKE ALL ON switchboard.pending_asks FROM PUBLIC, butler_switchboard_rw;
        GRANT SELECT, INSERT, UPDATE ON switchboard.pending_asks TO butler_switchboard_rw;
        DROP POLICY IF EXISTS pending_asks_runtime ON switchboard.pending_asks;
        CREATE POLICY pending_asks_runtime ON switchboard.pending_asks
            FOR ALL TO butler_switchboard_rw
            USING (current_user = 'butler_switchboard_rw')
            WITH CHECK (current_user = 'butler_switchboard_rw');
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF to_regclass('switchboard.pending_asks') IS NOT NULL
               AND EXISTS (SELECT 1 FROM switchboard.pending_asks) THEN
                RAISE EXCEPTION
                    'sw_068 downgrade refused: pending asks exist and are the only record that the owner was not told';
            END IF;
        END $$;
        DROP TABLE IF EXISTS switchboard.pending_asks;
    """)
