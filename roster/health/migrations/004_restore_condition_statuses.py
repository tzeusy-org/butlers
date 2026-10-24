"""restore_condition_statuses

Revision ID: health_004
Revises: health_003
Create Date: 2026-10-18 00:00:00.000000

bu-cgl4na (follow-up to bu-pimdvx).  Before the decay sweep stopped doing it, every
nightly sweep popped ``metadata.status`` from each decaying fact, so Health
``condition`` facts lost their ``active`` / ``managed`` / ``resolved`` status and
``condition_list`` read them all as ``active``.

A condition fact's content is always ``"<name>: <status>"``, stored beside the
metadata, so a live condition row with no ``metadata.status`` can be restored from
that suffix when it is one of ``active``, ``managed`` or ``resolved``.  A row that was
updated after the strip already says ``active`` in its content, so restoring leaves
its meaning unchanged.

Only rows with ``scope = 'health'``, predicate ``condition``, ``validity`` active or
fading, no ``metadata.status`` key and a parseable suffix are touched.  Each restored
row is tagged ``metadata.status_restored_by = 'health_004'`` so the repaired rows can be
found later.  ``condition_update`` copies metadata forward, so the tag also appears on
rows that supersede a restored row; it marks "descended from a repaired row", not "status
restored here".  The downgrade does not touch data (the previous state was the defect, as
in health_003), because stripping by tag would erase a status the owner set after the
repair.

Not recoverable, and left alone: relationship gift statuses (idea, purchased, wrapped,
given, thanked) and goal / deadline facts the LLM wrote from the predicate-registry
examples carry no status in their content.  Finance subscription and bill facts are
recoverable from the finance tables and are handled in bu-4ctaam.

Guarded with ``to_regclass`` (a schema without the memory ``facts`` table is a no-op),
idempotent (a restored row has a status, so a second run matches nothing), and writes
only the schema this chain runs in.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "health_004"
down_revision = "health_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('facts') IS NULL THEN
                RETURN;
            END IF;
            UPDATE facts f
            SET metadata = COALESCE(f.metadata, '{}'::jsonb)
                || jsonb_build_object(
                    'status', substring(f.content from ': (active|managed|resolved)$'),
                    'status_restored_by', 'health_004'
                )
            WHERE f.scope = 'health'
              AND f.predicate = 'condition'
              AND f.validity IN ('active', 'fading')
              AND NOT (COALESCE(f.metadata, '{}'::jsonb) ? 'status')
              AND f.content ~ '^.+: (active|managed|resolved)$';
        END
        $$
    """)


def downgrade() -> None:
    # The data change is not reversed: the previous state was the defect (as in health_003).
    # ``status_restored_by`` is deliberately not removed either, because
    # ``condition_update`` copies a row's metadata forward, so a later row the owner set
    # (a status chosen after the repair) can carry the tag; stripping by tag would erase
    # that real status and recreate the defect.
    pass
