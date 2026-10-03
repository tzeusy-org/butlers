"""public.entities.posture: owner-asserted person posture.

Revision ID: core_256
Revises: core_255
Create Date: 2026-10-03 00:00:00.000000

bu-q7vx1q.8.  The owner can tell Relationship once that someone has died, is
estranged or must not be contacted, and every producer that nudges about that
person (birthday highlight, gift ask, reconnection insight, outbound
``notify``) respects it. ``entities.listed`` (core_103) stays the separate
"hide from search" axis; archiving was the only lever before and it hides the
person's memories and chronicles too.

Schema delta on ``public.entities``:

- ``posture TEXT NOT NULL DEFAULT 'active'`` constrained to
  ``active | memorial | quiet | no_contact``. NOT NULL so a reader never has to
  guess what an absent value means.
- ``posture_since DATE`` and ``posture_set_by TEXT``: when and by whom (the
  asserting butler) the current posture was set.
- ``public.posture_writer_allowed(role)`` plus ``public.guard_entity_posture_writer()``
  and a ``BEFORE UPDATE`` trigger:
  every butler runtime role holds a table-level ``UPDATE`` on
  ``public.entities`` (core_065), which a column-level ``REVOKE`` cannot narrow,
  so the trigger refuses a posture change from any butler runtime role or
  ``connector_writer`` other than ``butler_relationship_rw``. Sessions that are
  not one of those roles (migration owner, dev stacks without the roles) are not
  restricted, matching the best-effort role handling in core_065 and core_110.

``ADD COLUMN IF NOT EXISTS`` and guarded constraint creation keep the migration
idempotent: the core chain runs once per butler schema against this shared table.
Downgrade drops the trigger, function and columns; the posture is lost, which
returns every person to ``active``.
"""

from __future__ import annotations

from alembic import op

revision = "core_256"
down_revision = "core_255"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.entities
            ADD COLUMN IF NOT EXISTS posture TEXT NOT NULL DEFAULT 'active',
            ADD COLUMN IF NOT EXISTS posture_since DATE,
            ADD COLUMN IF NOT EXISTS posture_set_by TEXT
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_entities_posture'
                  AND conrelid = 'public.entities'::regclass
            ) THEN
                ALTER TABLE public.entities
                    ADD CONSTRAINT ck_entities_posture
                    CHECK (posture IN ('active', 'memorial', 'quiet', 'no_contact'));
            END IF;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.posture_writer_allowed(role_name text)
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        AS $$
            SELECT NOT (
                (role_name ~ '^butler_.+_rw$' OR role_name = 'connector_writer')
                AND role_name <> 'butler_relationship_rw'
            )
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.guard_entity_posture_writer()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF (NEW.posture IS DISTINCT FROM OLD.posture
                OR NEW.posture_since IS DISTINCT FROM OLD.posture_since
                OR NEW.posture_set_by IS DISTINCT FROM OLD.posture_set_by)
               AND NOT public.posture_writer_allowed(current_user::text) THEN
                RAISE EXCEPTION 'entity posture may only be changed by the relationship butler'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_entities_posture_writer ON public.entities")
    op.execute(
        """
        CREATE TRIGGER trg_entities_posture_writer
        BEFORE UPDATE OF posture, posture_since, posture_set_by ON public.entities
        FOR EACH ROW EXECUTE FUNCTION public.guard_entity_posture_writer()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_entities_posture_writer ON public.entities")
    op.execute("DROP FUNCTION IF EXISTS public.guard_entity_posture_writer()")
    op.execute("DROP FUNCTION IF EXISTS public.posture_writer_allowed(text)")
    op.execute("ALTER TABLE public.entities DROP CONSTRAINT IF EXISTS ck_entities_posture")
    op.execute(
        """
        ALTER TABLE public.entities
            DROP COLUMN IF EXISTS posture_set_by,
            DROP COLUMN IF EXISTS posture_since,
            DROP COLUMN IF EXISTS posture
        """
    )
