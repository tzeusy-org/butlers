"""Archive Google Health resource-cursor rows from connector liveness.

Revision ID: sw_026
Revises: sw_025
Create Date: 2026-07-13 00:00:00.000000

Google Health persists independent cursors for every account/resource pair in
``connector_registry``. Those storage keys intentionally include an account
UUID and resource suffix, while heartbeats use the canonical per-account
identity. Cursor rows were therefore active registry entries that could never
receive a heartbeat, causing fleet-health and QA to report false offline
connectors.

Archive every row matching the connector's canonical cursor-key shape. The
cursor remains readable because cursor loading does not exclude archived rows;
only liveness, alerting, and active fleet summaries do. Future writes preserve
this classification via ``cursor_store.save_cursor(..., archive=True)``.

This is an irreversible data classification repair. Downgrade intentionally
does not unarchive rows because doing so would restore the false liveness
signals and could override an operator's independent archival decision.
"""

from __future__ import annotations

from alembic import op

revision = "sw_026"
down_revision = "sw_025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        UPDATE connector_registry
           SET archived_at = now()
         WHERE connector_type = 'google_health'
           AND deleted_at IS NULL
           AND archived_at IS NULL
           AND endpoint_identity ~
               '^google_health:user:.+:[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}:(sleep|activity|resting_hr|hrv|spo2|breathing_rate|vo2_max)$'
        """
    )


def downgrade() -> None:
    pass
