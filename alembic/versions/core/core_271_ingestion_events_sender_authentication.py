"""Add sender_authentication to public.ingestion_events.

Revision ID: core_271
Revises: core_268
Create Date: 2026-10-19 00:00:00.000000

Numbering note: chain head on main at authoring time was core_268. Open branches
already claim core_269 (bu-svqpzo.13) and core_270 (bu-q7vx1q.30), both revising
core_268. If either lands first, rechain ``down_revision`` onto the new head (and
renumber with git mv) before this one lands, per the repo's duplicate-revision
collision lore (see core_160's / core_168's renumbering sagas).

bu-s11n0s.20.

Motivation
----------
Several readers need to know whether an accepted email really came from the domain
it claims: the payment-redirect guard (bu-s11n0s.9), vendor-door confirmation
(bu-jv1moa.3) and RFC 0038 admission. Each parsing ``Authentication-Results`` itself
would drift, so ingest computes one deterministic verdict from the topmost header and
stores it on the canonical event row.

The column is ``authenticated``, ``unverified`` or ``failed``. It is NULL for every
non-email channel and for rows ingested before this revision (the header is not
re-derivable for them). ``public.ingestion_events`` is a cross-butler public table;
the column is nullable and additive, so every existing writer and reader stays valid.

Reversibility
-------------
The downgrade drops the column and its constraint. No data migration is needed.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_271"
down_revision = "core_268"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Autocommit so each statement releases its ACCESS EXCLUSIVE lock when it finishes: the
    # NOT VALID add is metadata-only, and VALIDATE then scans under a weaker lock that does
    # not block ingest writes (core_242 precedent). lock_timeout bounds the wait for each lock.
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        previous_timeout = connection.exec_driver_sql("SHOW lock_timeout").scalar_one()
        op.execute("SET lock_timeout = '5s'")
        try:
            op.execute(
                """
                ALTER TABLE public.ingestion_events
                ADD COLUMN IF NOT EXISTS sender_authentication TEXT
                """
            )
            op.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'ck_ingestion_events_sender_authentication'
                          AND conrelid = 'public.ingestion_events'::regclass
                    ) THEN
                        ALTER TABLE public.ingestion_events
                        ADD CONSTRAINT ck_ingestion_events_sender_authentication
                        CHECK (sender_authentication IN ('authenticated', 'unverified', 'failed'))
                        NOT VALID;
                    END IF;
                END
                $$
                """
            )
            op.execute(
                """
                ALTER TABLE public.ingestion_events
                VALIDATE CONSTRAINT ck_ingestion_events_sender_authentication
                """
            )
        finally:
            connection.exec_driver_sql(
                "SELECT set_config('lock_timeout', %s, false)", (previous_timeout,)
            )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        DROP CONSTRAINT IF EXISTS ck_ingestion_events_sender_authentication
        """
    )
    op.execute(
        """
        ALTER TABLE public.ingestion_events
        DROP COLUMN IF EXISTS sender_authentication
        """
    )
