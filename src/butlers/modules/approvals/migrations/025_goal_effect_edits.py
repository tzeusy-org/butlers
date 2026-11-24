"""Immutable domain-owned snapshots for D13 owner amendments (bu-mmdsl7).

Revision ID: approvals_025
Revises: approvals_024

A staged amendment is inert until the broker commits a matching sw_087 receipt.
The original parked arguments remain here after pending_actions mirrors the edit.
The two chains have no Alembic dependency: both heads are prerequisites for the dark path.
"""

from alembic import op

revision = "approvals_025"
down_revision = "approvals_024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_effect_edits (
            id UUID PRIMARY KEY,
            goal_id UUID NOT NULL,
            action_id UUID NOT NULL REFERENCES pending_actions(id),
            original_digest TEXT NOT NULL CHECK (original_digest ~ '^[0-9a-f]{64}$'),
            approved_digest TEXT NOT NULL CHECK (approved_digest ~ '^[0-9a-f]{64}$'),
            original_args JSONB NOT NULL CHECK (jsonb_typeof(original_args) = 'object'),
            approved_args JSONB NOT NULL CHECK (jsonb_typeof(approved_args) = 'object'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK (original_digest <> approved_digest)
        );
        CREATE INDEX IF NOT EXISTS ix_goal_effect_edits_action ON goal_effect_edits(action_id);
        CREATE FUNCTION goal_effect_edit_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP = 'TRUNCATE' AND NOT EXISTS (SELECT 1 FROM goal_effect_edits) THEN
                RETURN NULL;
            END IF;
            RAISE EXCEPTION 'Goal effect amendments are immutable and durable';
        END $$;
        CREATE TRIGGER goal_effect_edit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
            ON goal_effect_edits FOR EACH STATEMENT EXECUTE FUNCTION goal_effect_edit_immutable();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM goal_effect_edits) THEN
                RAISE EXCEPTION 'Cannot discard goal effect amendments';
            END IF;
        END $$;
        DROP TABLE IF EXISTS goal_effect_edits;
        DROP FUNCTION IF EXISTS goal_effect_edit_immutable();
    """)
