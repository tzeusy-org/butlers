"""Create triage_rules table with indexes, constraints, and seed data.

Revision ID: sw_017
Revises: sw_016
Create Date: 2026-02-23 00:00:00.000000

Migration notes:
- Creates triage_rules table per pre_classification_triage.md §4.1.
- rule_type constrained to: sender_domain, sender_address, header_condition, mime_type.
- action constrained to skip/metadata_only/low_priority_queue/pass_through or route_to:<target>.
- created_by constrained to: dashboard, api, seed.
- Soft-delete support via deleted_at column (NULL = active, non-NULL = deleted).
- Three indexes: active+priority composite, rule_type filtered, GIN on condition JSONB.
- Seed rows from §7 are inserted idempotently (ON CONFLICT DO NOTHING) with created_by='seed'.
- Downgrade drops all indexes and the table.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_017"
down_revision = "sw_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the triage_rules table per spec §4.1
    op.execute(
        """
        CREATE TABLE triage_rules (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          rule_type TEXT NOT NULL,
          condition JSONB NOT NULL,
          action TEXT NOT NULL,
          priority INTEGER NOT NULL,
          enabled BOOLEAN NOT NULL DEFAULT TRUE,
          created_by TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          deleted_at TIMESTAMPTZ NULL,

          CONSTRAINT triage_rules_rule_type_check
            CHECK (rule_type IN ('sender_domain', 'sender_address', 'header_condition', 'mime_type')),

          CONSTRAINT triage_rules_action_check
            CHECK (
              action IN ('skip', 'metadata_only', 'low_priority_queue', 'pass_through')
              OR action LIKE 'route_to:%'
            ),

          CONSTRAINT triage_rules_created_by_check
            CHECK (created_by IN ('dashboard', 'api', 'seed')),

          CONSTRAINT triage_rules_priority_check
            CHECK (priority >= 0)
        )
        """
    )

    # Index: active rules ordered by evaluation priority (primary query path)
    op.execute(
        """
        CREATE INDEX triage_rules_active_priority_idx
          ON triage_rules (enabled, priority, created_at, id)
          WHERE deleted_at IS NULL
        """
    )

    # Index: filter by rule_type (for CRUD list queries)
    op.execute(
        """
        CREATE INDEX triage_rules_rule_type_idx
          ON triage_rules (rule_type)
          WHERE deleted_at IS NULL
        """
    )

    # GIN index: condition JSONB for flexible querying
    op.execute(
        """
        CREATE INDEX triage_rules_condition_gin_idx
          ON triage_rules
          USING GIN (condition)
        """
    )

    # Seed rules from spec §7 — idempotent via ON CONFLICT DO NOTHING on id.
    # We use a fixed-UUID approach with ON CONFLICT to guarantee idempotency
    # across repeated imports. created_by='seed' marks all seed rows.
    op.execute(
        """
        INSERT INTO triage_rules
          (id, rule_type, condition, action, priority, enabled, created_by)
        VALUES
          (
            '00000000-0000-0000-0001-000000000010',
            'sender_domain',
            '{"domain": "chase.com", "match": "suffix"}',
            'route_to:finance',
            10,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000011',
            'sender_domain',
            '{"domain": "americanexpress.com", "match": "suffix"}',
            'route_to:finance',
            11,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000020',
            'sender_domain',
            '{"domain": "delta.com", "match": "suffix"}',
            'route_to:travel',
            20,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000021',
            'sender_domain',
            '{"domain": "united.com", "match": "suffix"}',
            'route_to:travel',
            21,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000030',
            'sender_domain',
            '{"domain": "paypal.com", "match": "suffix"}',
            'route_to:finance',
            30,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000040',
            'header_condition',
            '{"header": "List-Unsubscribe", "op": "present"}',
            'metadata_only',
            40,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000041',
            'header_condition',
            '{"header": "Precedence", "op": "equals", "value": "bulk"}',
            'low_priority_queue',
            41,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000042',
            'header_condition',
            '{"header": "Auto-Submitted", "op": "equals", "value": "auto-generated"}',
            'skip',
            42,
            TRUE,
            'seed'
          ),
          (
            '00000000-0000-0000-0001-000000000050',
            'mime_type',
            '{"type": "text/calendar"}',
            'route_to:relationship',
            50,
            TRUE,
            'seed'
          )
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS triage_rules_condition_gin_idx")
    op.execute("DROP INDEX IF EXISTS triage_rules_rule_type_idx")
    op.execute("DROP INDEX IF EXISTS triage_rules_active_priority_idx")
    op.execute("DROP TABLE IF EXISTS triage_rules")
