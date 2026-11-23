"""Read indexes for the goal ledger dashboard routes (RFC 0038 M3, bu-85bqeq.4).

Revision ID: sw_085
Revises: sw_084

``GET /api/goals`` orders on ``goals.created_at DESC, id DESC``, keysets on the same pair, and
filters ``created_at`` by ``since``/``until``. Nothing indexed ``created_at``, so every page
sorted the whole table. ``ix_goals_created_id`` matches that order exactly: the page is an
index scan that stops at the limit, and the other filters (origin, state, exposure, outcome)
are applied to the rows it walks.

Its ``region`` filter and ``GET /api/goals/regions`` both derive a goal's region from
``split_part(effects.capability, '__', 1)``. ``ix_effects_region_goal`` indexes that expression
with ``goal_id``: the ``region`` filter becomes an index probe per goal, and the
``DISTINCT (goal_id, region)`` scan of the regions summary reads the narrow index only, not the
effect rows. The summary still reads every effect once; reducing that needs a query change, not
an index.

Both are plain additive indexes: no grant, table or constraint changes. The tables are small
and the migration runs before the daemons serve, so ``CREATE INDEX`` is not ``CONCURRENTLY``.
"""

from alembic import op

revision = "sw_085"
down_revision = "sw_084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_goals_created_id
        ON switchboard.goals (created_at DESC, id DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_effects_region_goal
        ON switchboard.effects ((split_part(capability, '__', 1)), goal_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS switchboard.ix_effects_region_goal")
    op.execute("DROP INDEX IF EXISTS switchboard.ix_goals_created_id")
