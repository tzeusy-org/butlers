"""group_labels: add group_labels join table to assign labels to groups.

Revision ID: rel_023
Revises: rel_022
Create Date: 2026-06-15 00:00:00.000000

Creates a ``group_labels`` join table that records which labels are
assigned to which groups.  Labels themselves already exist in the
``labels`` table (created by ``rel_001``); this migration adds the
many-to-many join so groups can be tagged/organised with labels.

Schema
------
``group_labels (group_id, label_id)``

- ``group_id``  FK → ``groups(id)`` ON DELETE CASCADE
- ``label_id``  FK → ``labels(id)`` ON DELETE CASCADE
- Primary key: ``(group_id, label_id)``

Index
-----
A reverse index on ``label_id`` supports the query "which groups
carry this label?" (e.g. the assign/remove endpoint and label-based
group filtering).

Idempotency
-----------
All DDL uses ``IF NOT EXISTS / IF EXISTS`` guards so the migration is
safe to replay on a partially-applied database.

Downgrade
---------
Drops ``group_labels`` (and its index, which PostgreSQL drops
automatically with the table) and the reverse index explicitly.
"""

from __future__ import annotations

from alembic import op

revision = "rel_023"
down_revision = "rel_022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS group_labels (
            group_id  UUID NOT NULL REFERENCES groups(id)  ON DELETE CASCADE,
            label_id  UUID NOT NULL REFERENCES labels(id)  ON DELETE CASCADE,
            PRIMARY KEY (group_id, label_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_group_labels_label_id
            ON group_labels (label_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_group_labels_label_id")
    op.execute("DROP TABLE IF EXISTS group_labels")
