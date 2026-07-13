"""Exclude cursor-only storage rows from the QA connector-state view.

Revision ID: sw_026
Revises: sw_025
Create Date: 2026-07-13 00:00:00.000000

``cursor_store.save_cursor`` persists checkpoints in ``connector_registry``.
When a connector needs several independent cursors, those storage keys need
not be heartbeat identities.  Such a row has a checkpoint but has never
received a process instance or heartbeat.  Treating it as a registered
connector makes the infra-state patrol report a false ``ConnectorOffline``
finding after its first-seen grace period expires.

Keep genuinely registered, never-heartbeated rows visible: only the precise
cursor-only shape (checkpoint present, no instance, no heartbeat) is excluded.
Rows become visible automatically if a heartbeat later upserts the same key.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_026"
down_revision = "sw_025"
branch_labels = None
depends_on = None

_CONNECTOR_VIEW_FQN = "public.v_qa_connector_state"

_CONNECTOR_VIEW_SQL = """
    SELECT
        connector_type,
        endpoint_identity,
        state,
        error_message,
        last_heartbeat_at,
        first_seen_at
    FROM connector_registry
    WHERE deleted_at IS NULL
      AND archived_at IS NULL
      AND NOT (
          instance_id IS NULL
          AND last_heartbeat_at IS NULL
          AND checkpoint_cursor IS NOT NULL
      )
"""

_PREVIOUS_CONNECTOR_VIEW_SQL = """
    SELECT
        connector_type,
        endpoint_identity,
        state,
        error_message,
        last_heartbeat_at,
        first_seen_at
    FROM connector_registry
    WHERE deleted_at IS NULL
      AND archived_at IS NULL
"""


def upgrade() -> None:
    op.execute(f"CREATE OR REPLACE VIEW {_CONNECTOR_VIEW_FQN} AS{_CONNECTOR_VIEW_SQL}")


def downgrade() -> None:
    op.execute(f"CREATE OR REPLACE VIEW {_CONNECTOR_VIEW_FQN} AS{_PREVIOUS_CONNECTOR_VIEW_SQL}")
