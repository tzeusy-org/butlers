"""Retain rule-creator provenance after terminal-action cleanup.

Revision ID: approvals_009
Revises: approvals_008
Create Date: 2026-07-23 00:00:00.000000

Terminal pending actions expire after 90 days.  A standing approval rule can
outlive the action it was created from, so ``approval_rules.created_from`` is
historical provenance rather than a deletion-blocking foreign key.  New rule
references retain the former integrity guarantee through a deferred,
insert/update-time guard that preserves the original circular-insert contract.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_009"
down_revision = "approvals_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow retained rules to preserve the terminal action that created them."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_rules') IS NOT NULL THEN
                ALTER TABLE approval_rules
                    DROP CONSTRAINT IF EXISTS approval_rules_created_from_fk;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_approval_rule_creator_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.created_from IS NULL THEN
                RETURN NEW;
            END IF;

            PERFORM 1
            FROM pending_actions
            WHERE id = NEW.created_from
            FOR KEY SHARE;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'approval rule created_from % does not reference a live pending action',
                    NEW.created_from
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
            IF to_regclass('approval_rules') IS NOT NULL THEN
                EXECUTE 'DROP TRIGGER IF EXISTS '
                    || quote_ident('trg_approval_rules_created_from_reference')
                    || ' ON approval_rules';
                EXECUTE 'CREATE CONSTRAINT TRIGGER '
                    || quote_ident('trg_approval_rules_created_from_reference')
                    || ' AFTER INSERT OR UPDATE OF created_from ON approval_rules '
                    || 'DEFERRABLE INITIALLY DEFERRED '
                    || 'FOR EACH ROW '
                    || 'EXECUTE FUNCTION validate_approval_rule_creator_reference()';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Remove the guard without invalidating retained historical provenance.

    Recreating the former foreign key is intentionally unsafe once terminal
    action retention has left valid rules whose ``created_from`` action no
    longer exists.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_rules') IS NOT NULL THEN
                EXECUTE 'DROP TRIGGER IF EXISTS '
                    || quote_ident('trg_approval_rules_created_from_reference')
                    || ' ON approval_rules';
            END IF;
        END $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS validate_approval_rule_creator_reference()")
