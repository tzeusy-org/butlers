"""Decision Desk prompts and intents: the runtime half of the tracker write bridge.

Revision ID: sw_042
Revises: sw_041
Create Date: 2026-10-08 00:00:00.000000

bu-ckkpz.3 (OpenSpec change ``owner-decision-desk-write-bridge``). The runtime
never reaches the Beads tracker. An owner's choice is recorded here as a
``decision_intents`` row, and the one management workload that holds the tracker
credential (the beads CronJob, ``scripts/beads_decision_applier.py``) applies it
with ``bd`` and records the outcome.

``decision_prompts`` holds one row per bead offered over Telegram. It snapshots
the options shown, because a callback's option index refers to that snapshot.
Its ``created_at`` is the HMAC binding of the prompt's ``dsk1`` tokens, and its
``delivery_outcome`` is the at-most-once reservation: only a proven
``not_attempted`` prompt is ever reserved again.

At most one live intent (``pending``/``applying``/``applied``) per bead is a
database invariant, not a convention, so two concurrent taps cannot both win.
"""

from __future__ import annotations

from alembic import op

revision = "sw_042"
down_revision = "sw_041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_prompts (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bead_id           TEXT NOT NULL,
            options           JSONB NOT NULL,
            default_option    TEXT NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            delivery_outcome  TEXT NULL,
            delivered_at      TIMESTAMPTZ NULL,
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_decision_prompts_bead UNIQUE (bead_id),
            CONSTRAINT ck_decision_prompts_options
                CHECK (jsonb_typeof(options) = 'array' AND jsonb_array_length(options) BETWEEN 1 AND 16),
            CONSTRAINT ck_decision_prompts_outcome
                CHECK (delivery_outcome IS NULL OR delivery_outcome IN
                    ('delivered', 'not_attempted', 'rejected', 'uncertain'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS decision_intents (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            bead_id         TEXT NOT NULL,
            option          TEXT NOT NULL,
            source          TEXT NOT NULL,
            actor           TEXT NOT NULL,
            prompt_id       UUID NULL REFERENCES decision_prompts(id) ON DELETE SET NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            attempts        INTEGER NOT NULL DEFAULT 0,
            failure_reason  TEXT NULL,
            last_error      TEXT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            claimed_at      TIMESTAMPTZ NULL,
            finished_at     TIMESTAMPTZ NULL,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_decision_intents_source CHECK (source IN ('dashboard', 'telegram')),
            CONSTRAINT ck_decision_intents_status
                CHECK (status IN ('pending', 'applying', 'applied', 'failed')),
            CONSTRAINT ck_decision_intents_failure
                CHECK ((status = 'failed') = (failure_reason IS NOT NULL)),
            CONSTRAINT ck_decision_intents_option_length CHECK (length(option) BETWEEN 1 AND 512)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_decision_intents_live_bead
        ON decision_intents (bead_id)
        WHERE status IN ('pending', 'applying', 'applied')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_decision_intents_status_created
        ON decision_intents (status, created_at)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_decision_intents_bead_created
        ON decision_intents (bead_id, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decision_intents")
    op.execute("DROP TABLE IF EXISTS decision_prompts")
