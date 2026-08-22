"""Durable accepted-to-outcome receipts for dashboard curriculum requests.

Revision ID: education_004
Revises: education_003
Create Date: 2026-08-22 00:00:00.000000

``POST /api/education/curriculum-requests`` used to persist nothing but a
short-lived ``pending_curriculum_request`` KV lock, fire a *detached* trigger
task, and return 202. Everything after the 202 was invisible: a trigger failure
was logged and swallowed, a crashed API process stranded the lock behind a
permanent 409, and the dashboard's success toast claimed setup-and-contact from
an acceptance rather than from evidence.

This migration installs the durable spine those claims need:

``education.curriculum_requests``
    One immutable row per accepted request. ``id`` is the receipt the owner (and
    the UI) can follow from acceptance to a terminal outcome. Evidence columns
    (``session_id``, ``mind_map_id``, ``calibration_ready_at``) and
    ``failure_reason`` settle onto the row as the detached work reports back.

``uq_curriculum_requests_one_open``
    The one-pending-at-a-time guard, moved out of the KV store and into the
    database as a partial unique index over the non-terminal statuses. A single
    guard, enforced by the backend rather than by an LLM remembering to call
    ``state_delete``.

Two CHECK constraints keep the receipt from lying: a terminal status must carry
``settled_at``, and ``failed`` must carry a ``failure_reason``.

See openspec/specs/dashboard-education-api/spec.md, "Curriculum request
receipt lifecycle".
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "education_004"
down_revision = "education_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS education.curriculum_requests (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            topic                TEXT NOT NULL,
            goal                 TEXT,
            status               TEXT NOT NULL DEFAULT 'accepted'
                                     CHECK (status IN ('accepted', 'running',
                                                       'completed', 'failed')),
            session_id           TEXT,
            mind_map_id          UUID REFERENCES education.mind_maps(id) ON DELETE SET NULL,
            calibration_ready_at TIMESTAMPTZ,
            failure_reason       TEXT,
            requested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            triggered_at         TIMESTAMPTZ,
            settled_at           TIMESTAMPTZ,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- A terminal receipt SHALL carry the timestamp that made it terminal;
            -- a live one SHALL NOT. Without this a row can read "completed" while
            -- claiming it never settled.
            CONSTRAINT curriculum_requests_terminal_settled CHECK (
                (status IN ('accepted', 'running') AND settled_at IS NULL)
                OR (status IN ('completed', 'failed') AND settled_at IS NOT NULL)
            ),

            -- A failure the owner cannot read is not a receipt.
            CONSTRAINT curriculum_requests_failure_reason CHECK (
                status <> 'failed' OR failure_reason IS NOT NULL
            )
        )
    """)

    # The single pending guard. Partial unique index over a constant expression:
    # at most one row may sit in a non-terminal status at any time, so a second
    # submit races into a unique violation (-> 409) instead of a second detached
    # curriculum start.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_curriculum_requests_one_open
            ON education.curriculum_requests ((true))
            WHERE status IN ('accepted', 'running')
    """)

    # Newest-first receipt lookup for the status read.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_curriculum_requests_requested_at
            ON education.curriculum_requests (requested_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS education.curriculum_requests")
