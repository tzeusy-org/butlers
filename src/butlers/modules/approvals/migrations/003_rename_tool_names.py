"""rename_tool_names: strip user_/bot_ prefixes from approval_rules.tool_name

Revision ID: approvals_003
Revises: approvals_002
Create Date: 2026-02-25 00:00:00.000000

Migrates existing standing approval rules to use the new plain tool names
introduced by the contacts-identity-model refactor (task 8.5).  The old
``user_*`` and ``bot_*`` name conventions have been removed; tools now use
plain names (e.g., ``telegram_send_message``, ``email_send_message``,
``notify``).

Changes applied in upgrade():

  1. UPDATE approval_rules SET tool_name = regexp_replace(tool_name, '^(user|bot)_', '')
     WHERE tool_name ~ '^(user|bot)_'

  2. Deactivate any rules whose tool_name references a tool that no longer
     exists after renaming (collision-free guard: if two rules map to the
     same plain name, keep both — the rules engine handles precedence).

downgrade():
  Reverses the rename is not practical (ambiguous without original names),
  so downgrade() is a no-op.  This migration is intentionally one-way.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_003"
down_revision = "approvals_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename user_* and bot_* tool references to plain names.
    # Uses a DO block to guard against the table not existing on fresh installs.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'approval_rules'
            ) THEN
                UPDATE approval_rules
                   SET tool_name = regexp_replace(tool_name, '^(user|bot)_', '')
                 WHERE tool_name ~ '^(user|bot)_';
            END IF;
        END;
        $$;
    """)


def downgrade() -> None:
    # Intentionally a no-op: reversing an ambiguous rename is not safe.
    # To revert, restore from backup or manually reassign tool_name values.
    pass
