"""calendar_owner_agreements: what the owner accepted (bu-5ii4gj.10 slice A-S1).

Revision ID: core_378
Revises: core_377
Create Date: 2026-11-26 12:00:00.000000

One row per accepted or tentative occurrence of a provider event the owner did not organise, in the
calendar-hosting butler's own schema: the terms as agreed (start, end) and digests of the location
and of the other attendees. It holds digests, never a location, a join link or an address. A later
change to the event does not rewrite the agreed terms, but the ``current_*`` columns follow the
event on every write, so a reader can compare the two without parsing provider
metadata. The owner declining or clearing their answer deletes the row,
and the event going away cascades.
``join_digest`` is reserved and stays NULL until the provider mapping carries conference data.

The core chain runs once per butler schema and every statement is idempotent. Downgrade drops the
table (it is derived and rebuilt from the next sync).
"""

from __future__ import annotations

from alembic import op

revision = "core_378"
down_revision = "core_377"
branch_labels = None
depends_on = None

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS calendar_owner_agreements (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id           UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    occurrence_start   TIMESTAMPTZ NOT NULL,
    agreed_starts_at   TIMESTAMPTZ NOT NULL,
    agreed_ends_at     TIMESTAMPTZ NOT NULL,
    location_digest    TEXT,
    join_digest        TEXT,
    attendees_digest   TEXT NOT NULL,
    response_status    TEXT NOT NULL CHECK (response_status IN ('accepted', 'tentative')),
    observed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    current_starts_at         TIMESTAMPTZ NOT NULL,
    current_ends_at           TIMESTAMPTZ NOT NULL,
    current_location_digest   TEXT,
    current_attendees_digest  TEXT NOT NULL,
    current_observed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, occurrence_start)
)
"""
_OWNER_SQL = """
DO $$
DECLARE db_owner TEXT;
BEGIN
    SELECT r.rolname INTO db_owner FROM pg_database AS d
        JOIN pg_roles AS r ON r.oid = d.datdba WHERE d.datname = current_database();
    IF db_owner IS NOT NULL AND db_owner <> current_user
       AND pg_has_role(current_user, db_owner, 'MEMBER') THEN
        EXECUTE format('ALTER TABLE calendar_owner_agreements OWNER TO %I', db_owner);
    END IF;
END $$
"""


def upgrade() -> None:
    if (
        not op.get_bind()
        .exec_driver_sql(
            "SELECT to_regclass(quote_ident(current_schema()) || '.calendar_events') IS NOT NULL"
        )
        .scalar()
    ):
        return
    op.execute(_TABLE_DDL)
    op.execute(_OWNER_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS calendar_owner_agreements")
