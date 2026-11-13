"""departure_watch and departure_cards — the read-only departure card (bu-jv1moa.8).

Revision ID: home_006
Revises: home_005
Create Date: 2026-11-14 00:00:00.000000

``departure_watch`` holds the things the owner wants checked when they leave home: one row per
Home Assistant entity, the owner's own ``owner_label`` (the only name a card uses) and the
states that count as secure (``off``, ``closed``, ``locked``...).  Anything else, including an
unreadable entity, is listed on the card.

``departure_cards`` is the evidence for one confirmed leave edge, rechecked every minute by
``butlers.jobs.home_departure``: ``open`` while the card still says what it said when it was
queued, ``closed`` with a reason once it must not be sent.  The Switchboard reads it only
through ``public.resolve_home_departure`` (core_335), which answers open, closed or unknown.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_006"
down_revision = "home_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS departure_watch (
            entity_id     TEXT        PRIMARY KEY,
            owner_label   TEXT        NOT NULL
                          CONSTRAINT ck_departure_watch_label
                          CHECK (char_length(btrim(owner_label)) BETWEEN 1 AND 80),
            secure_states TEXT[]      NOT NULL
                          CONSTRAINT ck_departure_watch_secure_states
                          CHECK (cardinality(secure_states) BETWEEN 1 AND 8),
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS departure_cards (
            edge_event_id        UUID        PRIMARY KEY,
            confirmed_at         TIMESTAMPTZ NOT NULL,
            state                TEXT        NOT NULL
                                 CONSTRAINT ck_departure_cards_state
                                 CHECK (state IN ('open', 'closed')),
            closed_reason        TEXT
                                 CONSTRAINT ck_departure_cards_reason
                                 CHECK (closed_reason IN
                                        ('retracted', 'late', 'reentered', 'all_secure',
                                         'not_queued')),
            proposed_fingerprint TEXT,
            fingerprint          TEXT,
            checked_at           TIMESTAMPTZ NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_departure_cards_reason_iff_closed
                CHECK ((state = 'closed') = (closed_reason IS NOT NULL))
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_departure_cards_confirmed_at "
        "ON departure_cards (confirmed_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS departure_cards")
    op.execute("DROP TABLE IF EXISTS departure_watch")
