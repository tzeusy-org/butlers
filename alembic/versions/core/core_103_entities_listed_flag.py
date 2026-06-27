"""add entities.listed flag

Revision ID: core_103
Revises: core_102
Create Date: 2026-05-19 00:00:00.000000

Decision: Option A (docs/archive/decisions/2026-05-19-contacts-listed-flag-migration.md, PR #1794).

Adds ``listed BOOLEAN NOT NULL DEFAULT true`` to ``public.entities`` — the
canonical entity registry — so that entity-level read paths can gate on the
same include/exclude semantics already used by ``public.contacts.listed``.

``listed = true``  → entity is visible on working surfaces (default)
``listed = false`` → entity is archived / retired (suppress from search, scores,
                     exports, relationship jobs, etc.)

This column is the entity-graph equivalent of ``public.contacts.listed``.
The upcoming bead 5 backfill will copy ``contacts.listed`` → ``entities.listed``
for every entity that has a linked contact.

Schema delta
------------
public.entities:
    listed  BOOLEAN  NOT NULL DEFAULT true

Indexes
-------
ix_entities_listed_active  — partial btree on (listed) WHERE listed = true.
    Accelerates the common query pattern ``WHERE e.listed = true`` used by all
    13 read-path sites identified in the decision doc.  The false-side is rare
    and does not warrant its own index.
"""

from __future__ import annotations

from alembic import op

revision = "core_103"
down_revision = "core_102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add listed column with server-side default so existing rows are backfilled
    # without a table rewrite on large deployments.
    op.execute("""
        ALTER TABLE public.entities
            ADD COLUMN IF NOT EXISTS listed BOOLEAN NOT NULL DEFAULT true
    """)

    # Partial btree index for the common filter: WHERE e.listed = true.
    # The false side (archived entities) is rare; a full index would waste space.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_entities_listed_active
        ON public.entities (listed)
        WHERE listed = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_entities_listed_active")
    op.execute("""
        ALTER TABLE public.entities
            DROP COLUMN IF EXISTS listed
    """)
