"""Drop orphaned triage_rules table — superseded by ingestion_policy.

Revision ID: sw_017
Revises: sw_016
Create Date: 2026-07-02 00:00:00.000000

Background
----------
``triage_rules`` was created in 003_switchboard_routing.py (sw_003) as an early
routing classification table.  It was superseded by ``ingestion_policy`` (see
comment at src/butlers/ingestion_policy.py:9: "replaces triage_rules evaluation")
and its data was already migrated into ``ingestion_rules`` (scope='global') within
the same sw_003 collapse migration.

Orphan audit
------------
Zero DML found across the entire codebase (src/, roster/, tests/).  The only
references are:
  - src/butlers/ingestion_policy.py:9  — comment only ("replaces triage_rules")
  - tests/contracts/test_connector_as_transport.py — references ``triage_rules``
    as a callable attribute name on a connector interface, NOT as a DB table
  - roster/switchboard/migrations/003_switchboard_routing.py — the migration
    itself (CREATE + data migration + its own downgrade DROP)

``014_drop_dead_feature_tables.py`` culled sibling dead tables but missed this
one; this migration finishes the cleanup.

Guards
------
``DROP TABLE IF EXISTS`` is used for idempotency — safe to run against
environments where the table is already absent (e.g. fresh installs after
sw_003 was updated to exclude it).  Dropping the table automatically drops
the three dependent indexes created in sw_003:
  - triage_rules_active_priority_idx
  - triage_rules_rule_type_idx
  - triage_rules_condition_gin_idx

Downgrade recreates the original table schema (columns, constraints, indexes)
from sw_003 for rollback fidelity.  No data is restored — the table was
empty at time of drop.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_017"
down_revision = "sw_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dropping the table automatically drops the three dependent indexes
    # (triage_rules_active_priority_idx, triage_rules_rule_type_idx,
    # triage_rules_condition_gin_idx).  No explicit index drops needed.
    op.execute("DROP TABLE IF EXISTS triage_rules")


def downgrade() -> None:
    # Recreate the original schema from sw_003 for rollback fidelity.
    # No data to restore — the table was empty at time of drop.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS triage_rules (
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
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS triage_rules_active_priority_idx
          ON triage_rules (enabled, priority, created_at, id)
          WHERE deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS triage_rules_rule_type_idx
          ON triage_rules (rule_type)
          WHERE deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS triage_rules_condition_gin_idx
          ON triage_rules
          USING GIN (condition)
        """
    )
