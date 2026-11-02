"""OwnTracks points: cover arrival-order cursor paging.

Revision ID: core_309
Revises: core_308
Create Date: 2026-11-03 00:00:00.000000

``owntracks.points`` pages the indefinitely retained evidence table by arrival
order, ``WHERE (recorded_at, id) > (...) ORDER BY recorded_at, id LIMIT ...``
(bu-e6lrho), so an offline-queued fix with an older device ``ts`` is still
projected when it arrives late. Without this index every run would scan and
sort the whole table. The ``(ts, id)`` index stays: ``owntracks.ssid_presence``
and the legacy-watermark cutover still read by device time.

Concurrent DDL in Alembic's autocommit block lets the OwnTracks connector keep
writing while the index builds. Because that block commits, the downgrade first
runs the protected core_198 rollback preflight: a deep downgrade that core_198
will refuse must fail before this revision drops the index or its stamp lands.
"""

from __future__ import annotations

from alembic import context, op
from butlers.migration_preflight import preflight_runtime_attention_downgrade

revision = "core_309"
down_revision = "core_308"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_owntracks_points_recorded_at_id
            ON connectors.owntracks_points (recorded_at ASC, id ASC)
            """
        )


def downgrade() -> None:
    preflight_runtime_attention_downgrade(op, context)
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS connectors.idx_owntracks_points_recorded_at_id"
        )
