"""Expose receiver-observed butler liveness to QA without policy write authority.

Revision ID: sw_037
Revises: sw_036
Create Date: 2026-09-23 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "sw_037"
down_revision = "sw_036"
branch_labels = None
depends_on = None

_VIEW = "public.v_qa_butler_receiver_state"


def upgrade() -> None:
    bind = op.get_bind()
    schema = str(bind.exec_driver_sql("SELECT current_schema()").scalar_one())
    quoted_schema = '"' + schema.replace('"', '""') + '"'
    op.execute(f"""
        CREATE OR REPLACE VIEW {_VIEW} AS
        SELECT r.name, r.registered_at, r.liveness_ttl_seconds,
               c.observed_state, c.healthy_observed_at,
               COALESCE(
                   c.boot_epoch > 0 AND c.observed_boot_epoch = c.boot_epoch,
                   false
               ) AS current_boot_observed
        FROM {quoted_schema}.butler_registry AS r
        LEFT JOIN {quoted_schema}.butler_registry_control_plane AS c USING (name)
    """)
    op.execute(f"REVOKE ALL ON {_VIEW} FROM PUBLIC")
    op.execute(f"GRANT SELECT ON {_VIEW} TO butler_qa_rw")


def downgrade() -> None:
    op.execute(f"DROP VIEW IF EXISTS {_VIEW}")
