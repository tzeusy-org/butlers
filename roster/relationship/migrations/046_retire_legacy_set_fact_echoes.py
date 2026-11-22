"""Retire legacy ``Set fact '<key>' = '<value>'`` activity echoes (bu-swkpoo.5).

Revision ID: rel_046
Revises: rel_045

A retired tool wrote an ``activity`` fact for every fact it set, with the tool's own
confirmation text as the content. Each echo duplicates the real fact it names, and together they
crowded the public ``entity_profile`` of an entity out of its 200-fact cap (RFC 0039 D10).

The rows are marked ``validity = 'retracted'`` rather than deleted: they were never a statement
about the owner's life, so ``superseded`` (which says a newer fact replaced them) would be wrong.
``relationship.facts`` is published only for ``validity`` in ``active`` and ``fading``, so the
rows leave the projection on its next sync. The match is the exact predicate plus the echo
prefix, so a real activity fact is never touched.

Idempotent: a second run matches no ``active``/``fading`` row. Downgrade is a no-op, because the
rows cannot be told apart from an owner-retracted activity once retired.
"""

from __future__ import annotations

from alembic import op

revision = "rel_046"
down_revision = "rel_045"
branch_labels = None
depends_on = None

RETIRE_STATEMENT = """
    UPDATE relationship.facts
    SET validity = 'retracted'
    WHERE predicate = 'activity'
      AND content LIKE 'Set fact %'
      AND validity IN ('active', 'fading')
"""


def upgrade() -> None:
    op.execute(RETIRE_STATEMENT)


def downgrade() -> None:
    pass
