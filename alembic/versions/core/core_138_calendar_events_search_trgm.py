"""calendar: pg_trgm GIN index over calendar_events(title, description, location)

Revision ID: core_138
Revises: core_137
Create Date: 2026-06-21 00:00:00.000000

Motivation
----------
The calendar workspace gains a full-text search endpoint
(``GET /api/calendar/workspace/search``) that matches a free-text query against
the human-readable event text in the ``calendar_events`` projection — ``title``,
``description``, and ``location``.  Without an index those substring/``ILIKE``
lookups degrade to a sequential scan of the whole projection per butler schema.

This migration ensures the ``pg_trgm`` extension and creates a GIN trigram index
covering all three searchable columns so substring (``ILIKE '%q%'``) and
similarity lookups are index-eligible rather than seq-scans.

Per-schema layout
-----------------
``calendar_events`` lives in every butler's own schema (not ``public``).  Alembic
runs each butler-schema migration with ``search_path`` set to that schema, so the
unqualified table name targets the correct per-butler table and the index is
created once per schema — consistent with the projection's per-butler layout.

Extension scope
---------------
``pg_trgm`` is a shared, database-wide object.  It is normally pre-installed by
the DB provisioning step (``scripts/init-db.sql``), so ``CREATE EXTENSION IF NOT
EXISTS`` here is a no-op in practice; it is included to make the search contract
self-sufficient.  ``downgrade()`` deliberately leaves the extension installed
(it may be used by other features) and only drops the index.

Idempotency / reversibility
---------------------------
``CREATE EXTENSION IF NOT EXISTS`` + ``CREATE INDEX IF NOT EXISTS`` make the
upgrade safely re-runnable as a no-op.  ``downgrade()`` uses ``DROP INDEX IF
EXISTS`` and keeps the extension.
"""

from __future__ import annotations

from alembic import op

revision = "core_138"
down_revision = "core_137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Shared, database-wide extension. Normally pre-installed by the DB
    # provisioning step; IF NOT EXISTS makes this a no-op when already present.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # GIN trigram index covering every searchable projection column so a
    # substring / similarity query against title, description, or location is
    # index-eligible. description/location are nullable — gin_trgm_ops simply
    # stores no trigrams for NULL values.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_calendar_events_search_trgm
        ON calendar_events
        USING gin (
            title gin_trgm_ops,
            description gin_trgm_ops,
            location gin_trgm_ops
        )
        """
    )


def downgrade() -> None:
    # Drop only the index; leave the shared pg_trgm extension installed.
    op.execute("DROP INDEX IF EXISTS ix_calendar_events_search_trgm")
