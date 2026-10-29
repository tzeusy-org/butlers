"""safety_watch — the owner's declared life-safety sensors (bu-jv1moa.6).

Revision ID: home_004
Revises: home_003
Create Date: 2026-10-18 00:00:00.000000

One row per sensor the owner wants watched for a hazard.  The safety sentinel
(``butlers.jobs.home_safety``) alerts only on declared rows, never on a device class or name
Home Assistant reports, so an HA-side rename cannot change what is watched.  ``owner_label`` is
the owner's own wording and is the only name an alert uses.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_004"
down_revision = "home_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS safety_watch (
            entity_id   TEXT        NOT NULL,
            role        TEXT        NOT NULL
                        CONSTRAINT ck_safety_watch_role CHECK (role IN ('leak', 'smoke', 'co', 'gas')),
            owner_label TEXT        NOT NULL
                        CONSTRAINT ck_safety_watch_label
                        CHECK (char_length(btrim(owner_label)) BETWEEN 1 AND 80),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (entity_id, role)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS safety_watch")
