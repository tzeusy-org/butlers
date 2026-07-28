"""Exclude checkpoint-only registry rows from QA connector liveness.

Revision ID: sw_028
Revises: sw_027
Create Date: 2026-07-28 00:00:00.000000

``cursor_store.save_cursor`` persists checkpoints in ``connector_registry``.
Most connectors use their canonical heartbeat identity as the cursor key, but
connectors with independently advancing streams may encode an extra dimension
in that key.  Google Health, for example, uses one key per account and resource.
Those rows carry a checkpoint but never receive a heartbeat, so the original
``v_qa_connector_state`` view exposed them as connectors that had never checked
in.  ``InfraStateSource`` consequently emitted a permanent ``ConnectorOffline``
finding once the row passed its startup grace window.

A row that has a checkpoint but no process instance and no heartbeat is storage
state, not a connector liveness identity. Keep such rows in
``connector_registry`` for restart-safe cursor persistence, but exclude them
from the QA liveness view. A process identity remains visible even before its
first heartbeat so the existing registration grace-window semantics still
apply.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_028"
down_revision = "sw_027"
branch_labels = None
depends_on = None

_CONNECTOR_VIEW_FQN = "public.v_qa_connector_state"

_BASE_CONNECTOR_VIEW_SQL = """
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

_CHECKPOINT_ONLY_PREDICATE = """
      AND NOT (
          instance_id IS NULL
          AND last_heartbeat_at IS NULL
          AND checkpoint_cursor IS NOT NULL
      )
"""


def upgrade() -> None:
    op.execute(
        f"CREATE OR REPLACE VIEW {_CONNECTOR_VIEW_FQN} AS "
        f"{_BASE_CONNECTOR_VIEW_SQL}{_CHECKPOINT_ONLY_PREDICATE}"
    )


def downgrade() -> None:
    op.execute(f"CREATE OR REPLACE VIEW {_CONNECTOR_VIEW_FQN} AS {_BASE_CONNECTOR_VIEW_SQL}")
