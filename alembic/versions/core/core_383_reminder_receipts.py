"""reminder_receipts: one row per rebase or retirement of an anchored reminder (bu-5ii4gj.10 R-S2).

Revision ID: core_383
Revises: core_380
Create Date: 2026-11-26 15:00:00.000000

When the event an anchored reminder follows moves, the reminder is rebased; when the event is
cancelled or the owner declines it, the reminder is retired. Each change writes one receipt in the
calendar-hosting butler's own schema. A replayed sync changes nothing because the change is
guarded by the reminder's stored anchor (a row lock plus "the event is no longer where the anchor
says"), not by the receipt, so an event that flips 10:00 -> 11:00 -> 10:00 -> 11:00 rebases every
time. ``revision`` records the transition (``<old start>><new start>``) for audit. ``announced_at``
is NULL until the owner-facing candidate was accepted by the broker; unannounced receipts are
retried by the next follow, so a broker outage loses nothing. A receipt holds the reminder's old
and new times and a short reason, never a title or an attendee. The reminder going away cascades.

The core chain runs once per butler schema and every statement is idempotent. Downgrade drops the
table (receipts are an audit trail of derived changes; the reminders themselves are untouched).
"""

from __future__ import annotations

from alembic import op

revision = "core_383"
down_revision = "core_380"
branch_labels = None
depends_on = None

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS reminder_receipts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reminder_event_id UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL CHECK (kind IN ('rebased', 'retired')),
    revision          TEXT NOT NULL,
    old_starts_at     TIMESTAMPTZ NOT NULL,
    new_starts_at     TIMESTAMPTZ,
    reason            TEXT NOT NULL,
    announced_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS reminder_receipts_unannounced_idx
    ON reminder_receipts (created_at) WHERE announced_at IS NULL
"""
_OWNER_SQL = """
DO $$
DECLARE db_owner TEXT;
BEGIN
    SELECT r.rolname INTO db_owner FROM pg_database AS d
        JOIN pg_roles AS r ON r.oid = d.datdba WHERE d.datname = current_database();
    IF db_owner IS NOT NULL AND db_owner <> current_user
       AND pg_has_role(current_user, db_owner, 'MEMBER') THEN
        EXECUTE format('ALTER TABLE reminder_receipts OWNER TO %I', db_owner);
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
    op.execute(_INDEX_DDL)
    op.execute(_OWNER_SQL)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reminder_receipts")
