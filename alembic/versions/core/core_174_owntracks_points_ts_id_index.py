"""OwnTracks points: cover timestamp-plus-UUID cursor paging.

Revision ID: core_174
Revises: core_173
Create Date: 2026-07-16 00:00:00.000000

``owntracks.ssid_presence`` pages the indefinitely retained evidence table with
``WHERE (ts, id) > (...) ORDER BY ts, id LIMIT ...``.  The original ``ts``-only
index can constrain the timestamp range, but PostgreSQL must still filter the
UUID boundary and incrementally sort every equal-timestamp group.  The
composite index provides the adapter's total order directly and permits early
LIMIT termination even when one timestamp contains more rows than a page.

Index construction and replacement use PostgreSQL's concurrent forms so the
OwnTracks connector can continue writing evidence while this migration runs.
The guarded DDL tolerates a repeated completed operation; Alembic's autocommit
block is required because PostgreSQL forbids concurrent index DDL inside a
transaction.
"""

from __future__ import annotations

from alembic import op

revision = "core_174"
down_revision = "core_173"
branch_labels = None
depends_on = None

_OLD_INDEX = "connectors.ix_owntracks_points_ts"
_CURSOR_INDEX = "connectors.idx_owntracks_points_ts_id"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_owntracks_points_ts_id
            ON connectors.owntracks_points (ts ASC, id ASC)
            """
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_OLD_INDEX}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_owntracks_points_ts
            ON connectors.owntracks_points (ts DESC)
            """
        )
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_CURSOR_INDEX}")
