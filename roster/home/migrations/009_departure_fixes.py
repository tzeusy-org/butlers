"""departure_fixes: the owner's declared per-item fix (bu-jv1moa.115).

Revision ID: home_009
Revises: home_008
Create Date: 2026-11-14 00:00:00.000000

One row declares the single exact Home Assistant call the owner wants offered, for approval, when a
watched departure item is not secure: its ``(domain, service)``, one explicit target entity and the
allowed ``data`` payload. A row is written only by the owner-authenticated dashboard route; no job,
tool or model writes one. ``entity_id`` references ``departure_watch`` so a fix cannot outlive the
item it fixes, and the unique key means one entity has at most one fix.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_009"
down_revision = "home_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS departure_fixes (
            entity_id        TEXT        PRIMARY KEY
                             REFERENCES departure_watch (entity_id) ON DELETE CASCADE,
            domain           TEXT        NOT NULL CHECK (domain ~ '^[a-z0-9_]{1,64}$'),
            service          TEXT        NOT NULL CHECK (service ~ '^[a-z0-9_]{1,64}$'),
            target_entity_id TEXT        NOT NULL
                             CHECK (target_entity_id ~ '^[a-z0-9_]+\\.[a-z0-9_]+$'),
            data             JSONB       NOT NULL DEFAULT '{}'::jsonb
                             CHECK (jsonb_typeof(data) = 'object'),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS departure_fixes")
