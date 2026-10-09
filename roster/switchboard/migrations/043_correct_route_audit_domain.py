"""Admit the existing misroute correction audit vocabulary.

Revision ID: sw_043
Revises: sw_042
Create Date: 2026-10-10 00:00:00.000000

The correct_route producer already writes action_type=correct_route and
outcome=failure. Keep sw_002 immutable and extend only its two finite CHECK
vocabularies. Downgrade refuses to discard or reinterpret these audit rows.
"""

from __future__ import annotations

from alembic import op

revision = "sw_043"
down_revision = "sw_042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Follow Alembic's target-schema search_path, including flat-public fixtures.
    # One ALTER replaces both CHECKs atomically without touching audit rows.
    op.execute(
        """
        ALTER TABLE operator_audit_log
            DROP CONSTRAINT valid_action_type,
            ADD CONSTRAINT valid_action_type CHECK (
                action_type IN (
                    'manual_reroute', 'cancel_request', 'abort_request',
                    'controlled_replay', 'controlled_retry', 'force_complete',
                    'correct_route'
                )
            ),
            DROP CONSTRAINT valid_outcome,
            ADD CONSTRAINT valid_outcome CHECK (
                outcome IN ('success', 'failed', 'rejected', 'partial', 'failure')
            )
        """
    )


def downgrade() -> None:
    # Acquire the DDL lock before checking, so a concurrent writer cannot add a
    # new-domain row between the guard and CHECK replacement. Alembic owns the
    # transaction: refusal rolls back both the revision and constraint changes.
    op.execute("LOCK TABLE operator_audit_log IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM operator_audit_log
                WHERE action_type = 'correct_route' OR outcome = 'failure'
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = '23514',
                    MESSAGE = 'correct_route audit history prevents domain downgrade';
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        ALTER TABLE operator_audit_log
            DROP CONSTRAINT valid_action_type,
            ADD CONSTRAINT valid_action_type CHECK (
                action_type IN (
                    'manual_reroute', 'cancel_request', 'abort_request',
                    'controlled_replay', 'controlled_retry', 'force_complete'
                )
            ),
            DROP CONSTRAINT valid_outcome,
            ADD CONSTRAINT valid_outcome CHECK (
                outcome IN ('success', 'failed', 'rejected', 'partial')
            )
        """
    )
