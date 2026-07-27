"""Retain immutable rule-event provenance after inactive-rule cleanup.

Revision ID: approvals_011
Revises: approvals_010
Create Date: 2026-07-28 00:00:00.000000

Inactive approval rules clean up after 180 days, while immutable approval
events retain their separate 365-day audit window. An
``approval_events.rule_id`` foreign key made those windows incompatible:
deleting an eligible rule either failed or would have required mutating or
deleting its audit record. The rule identifier is therefore historical
provenance, with an insert-time guard that keeps new event references valid.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_011"
down_revision = "approvals_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow retained audit events to outlive their inactive rule rows."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_rule_id_fkey;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_approval_event_rule_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.rule_id IS NULL THEN
                RETURN NEW;
            END IF;

            PERFORM 1
            FROM approval_rules
            WHERE id = NEW.rule_id
            FOR KEY SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'approval event rule_id % does not reference a live approval rule',
                    NEW.rule_id
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
                    || quote_ident('trg_approval_events_rule_reference')
                    || ' ON approval_events';
                EXECUTE 'CREATE TRIGGER trg_approval_events_rule_reference '
                    || 'BEFORE INSERT ON approval_events '
                    || 'FOR EACH ROW '
                    || 'EXECUTE FUNCTION validate_approval_event_rule_reference()';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Remove the insert-time guard without destroying retained audit history.

    Recreating the former foreign key is intentionally unsafe once inactive-rule
    retention has left valid immutable events with historical rule identifiers.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                EXECUTE 'DROP TRIGGER IF EXISTS '
                    || quote_ident('trg_approval_events_rule_reference')
                    || ' ON approval_events';
            END IF;
        END $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS validate_approval_event_rule_reference()")
