"""metadata_backfill

Revision ID: travel_002
Revises: travel_001
Create Date: 2026-09-06 00:00:00.000000

Backfills ``travel.{trips,legs,accommodations,reservations,documents}.metadata``
rows that were double-JSON-encoded by a bug in ``tools/bookings.py`` (calling
``json.dumps()`` before binding a ``$n::jsonb`` parameter, which the asyncpg
JSONB codec then encodes *again* -- see ``src/butlers/db.py::register_jsonb_codec``
and AGENTS.md's "Notes to self" entry on this trap).

Only rows where ``metadata`` is a JSON *string* (``jsonb_typeof = 'string'``)
are touched, which makes re-running this migration a no-op. The original
string value is preserved in ``raw_pre_migration`` on each backfilled row for
exact rollback.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "travel_002"
down_revision = "travel_001"
branch_labels = None
depends_on = None

_TABLES = ("trips", "legs", "accommodations", "reservations", "documents")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"""
            ALTER TABLE travel.{table}
                ADD COLUMN IF NOT EXISTS raw_pre_migration JSONB
        """)
        # `metadata #>> '{{}}'` unwraps a jsonb *string* scalar to its raw text
        # content (removing the JSON quoting), which is then re-parsed as the
        # real jsonb object it always was underneath the double-encoding.
        op.execute(f"""
            UPDATE travel.{table}
            SET raw_pre_migration = metadata,
                metadata = (metadata #>> '{{}}')::jsonb
            WHERE jsonb_typeof(metadata) = 'string'
        """)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"""
            UPDATE travel.{table}
            SET metadata = raw_pre_migration
            WHERE raw_pre_migration IS NOT NULL
        """)
        op.execute(f"""
            ALTER TABLE travel.{table}
                DROP COLUMN IF EXISTS raw_pre_migration
        """)
