"""arrival_cards: evidence for the read-only arrival card (bu-jv1moa.116).

Revision ID: home_008
Revises: home_007
Create Date: 2026-11-14 00:00:00.000000

One row per confirmed owner.place enter edge that ``butlers.jobs.home_arrival`` has decided about:
``open`` while the card still says what it said when it was queued, ``closed`` with a content-blind
reason once it must not be sent (``withdrawn_changed`` when its lines changed after queueing; the
row never stores the lines themselves). The Switchboard reads it only through
``public.resolve_home_arrival`` (core_338), which answers open, closed or unknown.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_008"
down_revision = "home_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS arrival_cards (
            edge_event_id        UUID        PRIMARY KEY,
            leave_event_id       UUID,
            confirmed_at         TIMESTAMPTZ NOT NULL,
            state                TEXT        NOT NULL
                                 CONSTRAINT ck_arrival_cards_state
                                 CHECK (state IN ('open', 'closed')),
            closed_reason        TEXT
                                 CONSTRAINT ck_arrival_cards_reason
                                 CHECK (closed_reason IN
                                        ('retracted', 'late', 'left_again', 'no_leave',
                                         'short_absence', 'nothing_to_report', 'not_queued',
                                         'withdrawn_changed')),
            proposed_fingerprint TEXT,
            fingerprint          TEXT,
            checked_at           TIMESTAMPTZ NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_arrival_cards_reason_iff_closed
                CHECK ((state = 'closed') = (closed_reason IS NOT NULL))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_arrival_cards_confirmed_at ON arrival_cards (confirmed_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS arrival_cards")
