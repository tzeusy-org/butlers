"""Owner-chat commitment candidates and their sink receipts (bu-jv1moa.11).

Revision ID: rel_042
Revises: rel_041

REQ-commitment-lifecycle-011, -012 and -015. A candidate is not a commitment: it
is a proposal the owner answers on a signed confirm card. ``source_key`` (a
one-way hash of the source message and direction) is unique, so a replayed
message never yields a second candidate, and a non-terminal candidate's
fingerprint is unique, so a restated promise does not open a second one.

The content columns hold only the detected action, never the message text.
A CHECK fixes the tombstone shape: once a candidate is terminal every content
column is NULL, so no code path can leave content on a disposed row. Only a
tracked tombstone keeps the commitment fingerprint.

``commitment_candidate_receipts`` records the sink's answer per ``confirm_id``
(``public.pending_confirms.id``) so a retried application returns the same
receipt. It holds outcomes only, no content.

Downgrade drops both tables.
"""

from __future__ import annotations

from alembic import op

revision = "rel_042"
down_revision = "rel_041"
branch_labels = None
depends_on = None

_CONTENT_COLUMNS = (
    "kind",
    "direction",
    "counterparty_entity_id",
    "action_description",
    "deadline",
    "confidence",
    "marker",
    "fingerprint",
    "channel",
    "evidence_ref",
    "taint",
    "card_snapshot",
    "card_expires_at",
)
_LIVE_REQUIRED = (
    "kind",
    "direction",
    "counterparty_entity_id",
    "action_description",
    "confidence",
    "marker",
    "fingerprint",
    "channel",
    "evidence_ref",
    "taint",
)


def upgrade_statements() -> tuple[str, ...]:
    content_null = " AND ".join(f"{column} IS NULL" for column in _CONTENT_COLUMNS)
    live_present = " AND ".join(f"{column} IS NOT NULL" for column in _LIVE_REQUIRED)
    return (
        f"""
        CREATE TABLE relationship.commitment_candidates (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_key TEXT NOT NULL UNIQUE CHECK (source_key ~ '^[0-9a-f]{{64}}$'),
            state TEXT NOT NULL CHECK (state IN
                ('pending','carded','tracked','done','ignored','superseded','lapsed')),
            kind TEXT,
            direction TEXT CHECK (direction = 'owner_to_other'),
            counterparty_entity_id UUID,
            action_description TEXT
                CHECK (char_length(action_description) BETWEEN 1 AND 120),
            deadline TIMESTAMPTZ,
            confidence DOUBLE PRECISION CHECK (confidence > 0 AND confidence <= 1),
            marker TEXT CHECK (marker IN ('relay_told','relay_promised','first_person_will',
                'first_person_going_to','first_person_obligation')),
            fingerprint TEXT,
            channel TEXT CHECK (channel IN ('whatsapp_user_client','telegram_user_client')),
            evidence_ref JSONB,
            taint TEXT CHECK (taint = 'untrusted_origin'),
            event_time TIMESTAMPTZ NOT NULL,
            lapse_at TIMESTAMPTZ NOT NULL CHECK (lapse_at > event_time),
            card_snapshot JSONB,
            confirm_id UUID UNIQUE,
            card_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            terminal_at TIMESTAMPTZ,
            commitment_fingerprint TEXT,
            CONSTRAINT ck_cc_live_shape CHECK (
                state NOT IN ('pending','carded') OR
                ({live_present} AND terminal_at IS NULL AND commitment_fingerprint IS NULL)),
            CONSTRAINT ck_cc_carded CHECK (
                state <> 'carded' OR (confirm_id IS NOT NULL AND card_snapshot IS NOT NULL)),
            CONSTRAINT ck_cc_tombstone CHECK (
                state IN ('pending','carded') OR ({content_null} AND terminal_at IS NOT NULL)),
            CONSTRAINT ck_cc_tracked_fingerprint CHECK (
                (state = 'tracked') = (commitment_fingerprint IS NOT NULL))
        )
        """,
        """
        CREATE UNIQUE INDEX uq_cc_live_fingerprint ON relationship.commitment_candidates
            (fingerprint) WHERE state IN ('pending','carded')
        """,
        """
        CREATE INDEX ix_cc_live_lapse ON relationship.commitment_candidates (lapse_at)
            WHERE state IN ('pending','carded')
        """,
        "CREATE INDEX ix_cc_event_time ON relationship.commitment_candidates (event_time)",
        """
        CREATE TABLE relationship.commitment_candidate_receipts (
            confirm_id UUID PRIMARY KEY,
            candidate_id UUID NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN (
                'tracked','done','ignored','already_tracked','already_done',
                'already_ignored','already_superseded','already_lapsed')),
            commitment_fingerprint TEXT,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        DO $$ BEGIN
            IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='butler_relationship_rw') THEN
                GRANT SELECT,INSERT,UPDATE,DELETE ON relationship.commitment_candidates,
                    relationship.commitment_candidate_receipts TO butler_relationship_rw;
            END IF;
        END $$
        """,
    )


def upgrade() -> None:
    for statement in upgrade_statements():
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS relationship.commitment_candidate_receipts")
    op.execute("DROP TABLE IF EXISTS relationship.commitment_candidates")
