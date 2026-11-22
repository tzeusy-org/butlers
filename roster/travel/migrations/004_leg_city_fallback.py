"""leg_city_fallback

Revision ID: travel_004
Revises: travel_003
Create Date: 2026-11-22 00:00:00.000000

bu-swkpoo.3: legs recorded without ``departure_city``/``arrival_city`` showed
null endpoints on the public MCP ``trips`` surface, which publishes only the
city columns. Backfills a blank city from the matching airport/station code.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "travel_004"
down_revision = "travel_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for side in ("departure", "arrival"):
        op.execute(f"""
            UPDATE travel.legs
            SET {side}_city = {side}_airport_station
            WHERE ({side}_city IS NULL OR btrim({side}_city) = '')
              AND {side}_airport_station IS NOT NULL
              AND btrim({side}_airport_station) <> ''
        """)


def downgrade() -> None:
    # No-op: backfilled rows are indistinguishable from legs whose city was
    # genuinely recorded as the station code, so nothing can be safely undone.
    pass
