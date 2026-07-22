"""Retain immutable action-event provenance after terminal-action cleanup.

Revision ID: approvals_008
Revises: approvals_007
Create Date: 2026-07-23 00:00:00.000000

Terminal pending actions expire after 90 days, while immutable approval events
have their own 365-day audit retention window.  An ``approval_events.action_id``
foreign key made those windows incompatible: deleting a terminal action either
failed or would have required mutating/deleting the audit record.  The action
identifier is therefore historical provenance, with an insert-time guard for
new events rather than a deletion-blocking foreign key.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_008"
down_revision = "approvals_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow retained audit events to outlive their terminal action rows."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_action_id_fkey;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_approval_event_action_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.action_id IS NULL THEN
                RETURN NEW;
            END IF;

            PERFORM 1
            FROM pending_actions
            WHERE id = NEW.action_id
            FOR KEY SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'approval event action_id % does not reference a live pending action',
                    NEW.action_id
                    USING ERRCODE = 'foreign_key_violation';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                EXECUTE 'DROP TRIGGER IF EXISTS '
                    || quote_ident('trg_approval_events_action_reference')
                    || ' ON approval_events';
                EXECUTE 'CREATE TRIGGER trg_approval_events_action_reference '
                    || 'BEFORE INSERT ON approval_events '
                    || 'FOR EACH ROW '
                    || 'EXECUTE FUNCTION validate_approval_event_action_reference()';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Remove the insert-time guard without destroying retained audit history.

    Recreating the former foreign key is intentionally unsafe once terminal
    action retention has left valid immutable events with historical action
    identifiers, so this downgrade does not restore that deletion-blocking
    relationship.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                EXECUTE 'DROP TRIGGER IF EXISTS '
                    || quote_ident('trg_approval_events_action_reference')
                    || ' ON approval_events';
            END IF;
        END $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS validate_approval_event_action_reference()")
