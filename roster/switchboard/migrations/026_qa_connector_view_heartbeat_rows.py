"""Restrict the QA connector-state view to heartbeat-established rows.

Revision ID: sw_026
Revises: sw_025
Create Date: 2026-07-13 00:00:00.000000

``connector_registry`` serves two distinct persistence roles: connector
heartbeat state and connector checkpoint/settings storage.  The latter can
upsert a row without ever representing a running connector process.  Such a
row has no ``instance_id`` because that required heartbeat field is populated
only by ``connector.heartbeat.v1`` ingestion.

The original ``public.v_qa_connector_state`` definition admitted both kinds
of row.  Once a checkpoint-only row aged past the liveness threshold,
``InfraStateSource`` therefore reported a false ``ConnectorOffline`` finding.
Google Health's per-account, per-resource cursor identities made the bug
especially visible, but the distinction is connector-independent.

Filter on ``instance_id IS NOT NULL`` so QA monitors connector processes that
have actually established liveness through a heartbeat.  The view remains a
read-only, migration-tracked cross-schema surface under RFC 0010's guardrails.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_026"
down_revision = "sw_025"
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

_HEARTBEAT_ONLY_CONNECTOR_VIEW_SQL = (
    _BASE_CONNECTOR_VIEW_SQL + "      AND instance_id IS NOT NULL\n"
)


def upgrade() -> None:
    op.execute(
        f"CREATE OR REPLACE VIEW {_CONNECTOR_VIEW_FQN} AS"
        f"{_HEARTBEAT_ONLY_CONNECTOR_VIEW_SQL}"
    )


def downgrade() -> None:
    op.execute(
        f"CREATE OR REPLACE VIEW {_CONNECTOR_VIEW_FQN} AS{_BASE_CONNECTOR_VIEW_SQL}"
    )
