"""critical_entities: the owner's registry of entities that always need approval (bu-jv1moa.122).

Revision ID: home_010
Revises: home_009
Create Date: 2026-11-21 00:00:00.000000

REQ-module-home-assistant-001 (RFC 0028 Amendment 1, adopted 2026-11-21).  One row per explicit
Home Assistant entity id the owner marked critical.  A row is written only by the
owner-authenticated dashboard routes; no tool, session, goal, rule or job writes one, and
membership is never inferred from a Home Assistant label, area, device class or name.
``owner_label`` is display text only: nothing reads authority from it, and the audit never
records it.  ``added_by`` and ``added_at`` say who and when.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_010"
down_revision = "home_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS critical_entities (
            entity_id   TEXT        PRIMARY KEY
                        CHECK (entity_id ~ '^[a-z0-9_]+\\.[a-z0-9_]+$'
                               AND char_length(entity_id) <= 255),
            owner_label TEXT        NOT NULL
                        CHECK (char_length(btrim(owner_label)) BETWEEN 1 AND 80),
            added_by    TEXT        NOT NULL CHECK (char_length(added_by) BETWEEN 1 AND 255),
            added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS critical_entities")
