"""drop_contacts_roles: drop the roles column from shared.contacts

Revision ID: core_016
Revises: core_015
Create Date: 2026-03-05 00:00:00.000000

Drops the legacy `roles` column from `shared.contacts` and the associated
`ix_contacts_owner_singleton` partial unique index.

Background:
  core_007 added `roles TEXT[] NOT NULL DEFAULT '{}'` to `shared.contacts` and
  created the owner singleton index `ix_contacts_owner_singleton`.  Identity
  roles were the source of truth on `contacts.roles` until core_014 moved them
  to `shared.entities.roles` with a full data migration.  core_014 kept the
  column for backward compatibility and noted that a follow-up migration would
  drop it.  core_015 granted missing butler roles to shared.entities.  This
  migration (core_016) completes the cleanup by dropping the now-unused roles
  column from shared.contacts.

Changes applied in upgrade():

  1. Drop partial unique index `ix_contacts_owner_singleton` on shared.contacts
     (was guarding 'owner' = ANY(roles)).
  2. Drop the `roles` column from `shared.contacts`.

downgrade() reverses in reverse order.

Design notes:
  - All DDL is guarded with IF (NOT) EXISTS / DO blocks for idempotency.
  - The column drop is NOT VALID-safe: there are no FK constraints on roles.
  - After this migration, `shared.entities.roles` is the sole source of truth
    for identity roles; `shared.contacts` carries no role information.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_016"
down_revision = "core_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Drop owner singleton partial unique index from shared.contacts.
    #    This index predicated on contacts.roles and is no longer meaningful
    #    once the roles column is removed.
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS shared.ix_contacts_owner_singleton")

    # -------------------------------------------------------------------------
    # 2. Drop the roles column from shared.contacts.
    #    Guard with an existence check so the migration is idempotent.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'contacts'
                     AND column_name = 'roles'
               )
            THEN
                ALTER TABLE shared.contacts DROP COLUMN roles;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # 2. Re-add roles column to shared.contacts.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'contacts'
                     AND column_name = 'roles'
               )
            THEN
                ALTER TABLE shared.contacts
                    ADD COLUMN roles TEXT[] NOT NULL DEFAULT '{}';
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 1. Re-create owner singleton partial unique index on shared.contacts.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_class idx
                   JOIN pg_namespace n ON n.oid = idx.relnamespace
                   WHERE idx.relname = 'ix_contacts_owner_singleton'
               )
            THEN
                EXECUTE
                    'CREATE UNIQUE INDEX ix_contacts_owner_singleton '
                    'ON shared.contacts ((true)) '
                    'WHERE ''owner'' = ANY(roles)';
            END IF;
        END
        $$;
    """)
