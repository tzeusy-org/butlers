"""Pending asks may name owner replies that a goal released unread.

Revision ID: sw_071
Revises: sw_070

RFC 0038 S2.2 and S6 (bu-dhloi1.323). sw_070 records, content-blind, each joined owner reply
that a goal's end released unread. A failed or budget-exhausted owner request already has a
pending ask; its cause line now states the count. A goal that ended ``completed`` or ``partial``,
or that failed but can never receive a failure ask (it is not an owner message, or its lineage
already settled an owner delivery), gets its own ask with the new closed cause ``replies_unread``,
so the owner is never left thinking a reply was acted on. A goal gets one ask, never two.

Only the cause CHECK widens. The downgrade refuses while a ``replies_unread`` ask exists,
because it is the only record that the owner was not told.
"""

from alembic import op

revision = "sw_071"
down_revision = "sw_070"
branch_labels = None
depends_on = None

_OLD = "('failed','budget_exhausted','capability_unavailable','admission_refused')"
_NEW = "('failed','budget_exhausted','capability_unavailable','admission_refused','replies_unread')"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE switchboard.pending_asks DROP CONSTRAINT IF EXISTS pending_asks_cause_check;
        ALTER TABLE switchboard.pending_asks
            ADD CONSTRAINT pending_asks_cause_check CHECK (cause IN {_NEW});
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.pending_asks WHERE cause = 'replies_unread') THEN
                RAISE EXCEPTION
                    'sw_071 downgrade refused: replies_unread asks are the only record that unread replies were not reported';
            END IF;
        END $$;
        ALTER TABLE switchboard.pending_asks DROP CONSTRAINT IF EXISTS pending_asks_cause_check;
        ALTER TABLE switchboard.pending_asks
            ADD CONSTRAINT pending_asks_cause_check CHECK (cause IN {_OLD});
    """)
