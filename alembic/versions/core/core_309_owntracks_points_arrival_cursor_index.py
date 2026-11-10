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

The core chain runs once per butler schema over one shared table, and those runs can overlap.
Two ``CREATE INDEX CONCURRENTLY`` builds on one table wait for each other's virtual
transactions, so racing builders deadlock (bu-dhloi1.285). The build therefore takes a session
advisory lock first, polled with ``pg_try_advisory_lock`` in autocommit: a blocking
``pg_advisory_lock`` would keep a snapshot the lock holder's build must wait for (the same
cycle, one step removed), and each failed poll ends its own transaction before the next sleep.
The loser then finds the index built and ``IF NOT EXISTS`` makes its build a no-op. This is the
pattern core_231, core_242 and core_245 use.
"""

from __future__ import annotations

from time import monotonic, sleep

from alembic import context, op
from butlers.migration_preflight import preflight_runtime_attention_downgrade

revision = "core_309"
down_revision = "core_308"
branch_labels = None
depends_on = None


_LOCK = 309_20261103
# A build on a large evidence table can run for minutes; a peer waits for it rather than failing.
_LOCK_WAIT_SECONDS = 1800


def _acquire_lock(connection) -> None:
    deadline = monotonic() + _LOCK_WAIT_SECONDS
    while not connection.exec_driver_sql(f"SELECT pg_try_advisory_lock({_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("owntracks arrival index migration is busy; retry this upgrade")
        sleep(0.1)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        _acquire_lock(connection)
        try:
            op.execute(
                """
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_owntracks_points_recorded_at_id
                ON connectors.owntracks_points (recorded_at ASC, id ASC)
                """
            )
        finally:
            op.execute(f"SELECT pg_advisory_unlock({_LOCK})")


def downgrade() -> None:
    preflight_runtime_attention_downgrade(op, context)
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS connectors.idx_owntracks_points_recorded_at_id"
        )
