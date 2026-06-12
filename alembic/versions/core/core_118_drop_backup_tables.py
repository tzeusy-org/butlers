"""drop_backup_tables: drop two verified-dead public backup/snapshot tables.

Tables targeted:
  1. public.contacts_pre_migration_20260531 (1 row)
       Pre-migration snapshot created by
       src/butlers/scripts/contact_migration_snapshot.py before the
       contacts→triples cut-over. The live contacts table has long been the
       canonical source; this table is a dead snapshot.
  2. public.contact_info_dropbak_core_115 (872 rows)
       Safety snapshot created by core_115_drop_contact_info.py when it
       dropped public.contact_info.  Retained for the 90-day recovery window
       mandated by Amendment 1.1.A.6 (bead bu-e2ja9).  Owner has explicitly
       approved dropping it (bead bu-colrv).

Both tables are confirmed to have zero runtime code references and no
inbound foreign keys.

Idempotency / cross-chain safety
---------------------------------
Both drops use ``IF EXISTS`` and a prior ``to_regclass(...)`` guard so the
migration is a safe no-op when the tables are already gone (e.g. after a
prior partial run, or when applied against a fresh schema that never
created them). No external chain can hold an FK referencing these backup
tables (they were created as plain AS-SELECT copies), so DROP CASCADE is
not needed.

downgrade()
-----------
Recreating the row data is not possible — these tables are dead backups
of tables that have already been dropped. downgrade() is a documented
no-op, consistent with how other drop migrations in this chain handle
irreversible backup removals.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision = "core_118"
down_revision = "core_117"
branch_labels = None
depends_on = None

_TABLES = (
    "public.contacts_pre_migration_20260531",
    "public.contact_info_dropbak_core_115",
)


def upgrade() -> None:
    bind = op.get_bind()
    for qualified in _TABLES:
        if bind.execute(sa.text(f"SELECT to_regclass('{qualified}')")).scalar() is None:
            logger.info("core_118: %s already absent — skipping", qualified)
            continue
        op.execute(f"DROP TABLE IF EXISTS {qualified}")
        logger.info("core_118: dropped %s", qualified)


def downgrade() -> None:
    # These tables are dead backups of already-dropped source tables.
    # Recreating the row data is not possible.
    # This is an intentional no-op, consistent with other drop migrations
    # in this chain (e.g. core_115 acknowledges the same irreversibility
    # for data that cannot be recovered once the source is gone).
    logger.info(
        "core_118 downgrade: no-op — backup tables cannot be recreated "
        "without source data that no longer exists."
    )
