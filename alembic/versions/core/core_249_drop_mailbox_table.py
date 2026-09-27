"""Drop the retired mailbox module's per-butler ``mailbox`` table.

Revision ID: core_249
Revises: core_248
Create Date: 2026-09-27 00:00:00.000000

The mailbox module (``src/butlers/modules/mailbox/``) was removed unused: no
roster butler ever enabled ``[modules.mailbox]``, so its ``mailbox_001``/
``mailbox_002`` chain never ran against a deployed schema. The drop is a
defensive ``IF EXISTS`` sweep for any hand-enabled schema; fresh and existing
databases both pass through it as a no-op when the table is absent. The core
chain also runs against ``public``, which never held the table, so that pass is
skipped.

Downgrade is a no-op: the module and its chain no longer exist to own the table.
"""

from __future__ import annotations

from alembic import op

revision = "core_249"
down_revision = "core_248"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF current_schema() <> 'public' THEN
                EXECUTE format('DROP TABLE IF EXISTS %I.mailbox', current_schema());
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    """No-op: the retired mailbox module no longer owns a table to restore."""
