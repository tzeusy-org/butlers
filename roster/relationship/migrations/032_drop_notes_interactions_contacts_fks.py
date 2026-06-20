"""rel_032 — drop the notes/interactions → public.contacts FKs that rel_030 missed.

rel_030 dropped the contact_id → public.contacts(id) foreign keys from the
relationship child tables (addresses, contact_labels, group_members,
important_dates, life_events, relationships, tasks) as part of the contact-schema
retirement. It did **not** include ``notes`` and ``interactions`` — both of which
were (re)created with a ``contact_id UUID NOT NULL REFERENCES contacts(id)`` FK in
migrations 001/003/010 and never had it dropped.

After the contacts cutover (bu-irphu / PR #2551), ``contact_create`` no longer
writes a ``public.contacts`` row — the synthetic ``contact_id`` lives only in
``relationship.contact_entity_map``. Any ``note_add`` / ``interaction_log`` insert
therefore references a ``contact_id`` that is absent from ``contacts`` and would
raise ``ForeignKeyViolationError`` in production. This migration removes those two
dangling FKs so the child rows can carry the bridge-only contact_id, matching the
other child tables post rel_030.

Idempotent (DROP CONSTRAINT IF EXISTS, guarded by to_regclass) and reversible
(downgrade re-adds the FKs best-effort, only when public.contacts still exists and
the constraint is currently absent).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_032"
down_revision = "rel_031"
branch_labels = None
depends_on = None

# (table, column, fk_name, on_delete) — the two FKs rel_030 omitted.
_FK_TABLES = (
    ("notes", "contact_id", "notes_contact_id_fkey", "CASCADE"),
    ("interactions", "contact_id", "interactions_contact_id_fkey", "CASCADE"),
)

_DROP_FK_SQL = r"""
DO $$
BEGIN
    IF to_regclass('notes') IS NOT NULL THEN
        ALTER TABLE notes         DROP CONSTRAINT IF EXISTS notes_contact_id_fkey;
    END IF;
    IF to_regclass('interactions') IS NOT NULL THEN
        ALTER TABLE interactions  DROP CONSTRAINT IF EXISTS interactions_contact_id_fkey;
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(_DROP_FK_SQL)


def downgrade() -> None:
    # Best-effort re-add: only when public.contacts still exists and the
    # constraint is currently absent (skips cleanly once contacts is dropped).
    readd = "\n".join(
        f"""
        IF to_regclass('{tbl}') IS NOT NULL
           AND to_regclass('public.contacts') IS NOT NULL
           AND NOT EXISTS (
               SELECT 1 FROM information_schema.table_constraints
               WHERE table_name = '{tbl}' AND constraint_name = '{fk}'
                 AND constraint_type = 'FOREIGN KEY'
           ) THEN
            ALTER TABLE {tbl}
                ADD CONSTRAINT {fk} FOREIGN KEY ({col})
                REFERENCES public.contacts(id) ON DELETE {ondel};
        END IF;"""
        for tbl, col, fk, ondel in _FK_TABLES
    )
    op.execute(f"DO $$\nBEGIN\n{readd}\nEND\n$$;")
