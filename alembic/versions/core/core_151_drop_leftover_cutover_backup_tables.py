"""drop_leftover_cutover_backup_tables: drop five leftover snapshot/backup tables.

Context (bead bu-zquce.16 — craft/cruft maintenance pass)
---------------------------------------------------------
The contacts->triples cut-over (2026-06-20) and its sibling dedup migrations each
CREATEd a safety snapshot in ``upgrade()`` but only DROPped it in ``downgrade()``,
so the snapshots persist on the live upgraded DB. This migration removes them,
following the exact precedent of ``core_118_drop_backup_tables.py``: a per-table
``to_regclass`` guard + ``DROP TABLE IF EXISTS`` + a no-op ``downgrade``.

Tables targeted (verified against the live DB 2026-07-03)
--------------------------------------------------------
  1. public.contacts_dropbak (418 rows)
       Durable recovery artifact for public.contacts, dropped by core_134.
  2. public.priority_contacts_dedup_bak_core_133 (0 rows)
       Empty dedup snapshot from core_133 — nothing was deduped.
  3. connectors.home_assistant_persons_dedup_bak_core_133 (0 rows)
       Empty dedup snapshot from core_133.
  4. public.entities_contact_profile_bak_rel_031 (381 rows)
       Relationship-schema cutover snapshot from rel_031.
  5. public.contacts_source_links_dedup_bak_contacts_005 (already absent)
       Snapshot from contacts_005 — already gone on the live DB; guard no-ops.

Recovery window
---------------
core_134's snapshot (contacts_dropbak) and the rel_031 snapshot are the recovery
artifacts for the contacts cut-over. The owner has explicitly approved dropping
all of them now (bu-zquce.16), overriding the remainder of the recovery window —
consistent with how core_118 dropped contact_info_dropbak_core_115 under explicit
owner approval (bu-colrv).

Idempotency / cross-chain safety
--------------------------------
Every drop uses ``IF EXISTS`` behind a ``to_regclass(...)`` guard, so this
migration is a safe no-op when a table is already gone (prior partial run, or a
fresh schema that never created it). These snapshots were created as plain
AS-SELECT copies with no inbound foreign keys, so ``CASCADE`` is not needed.

downgrade()
-----------
These are dead backups of already-mutated/dropped source tables; the row data
cannot be recreated. ``downgrade()`` is an intentional no-op, consistent with
core_118 and the other irreversible drop migrations in this chain.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision = "core_151"
down_revision = "core_150"
branch_labels = None
depends_on = None

_TABLES = (
    "public.contacts_dropbak",
    "public.priority_contacts_dedup_bak_core_133",
    "connectors.home_assistant_persons_dedup_bak_core_133",
    "public.entities_contact_profile_bak_rel_031",
    "public.contacts_source_links_dedup_bak_contacts_005",
)


def upgrade() -> None:
    bind = op.get_bind()
    for qualified in _TABLES:
        if bind.execute(sa.text(f"SELECT to_regclass('{qualified}')")).scalar() is None:
            logger.info("core_151: %s already absent — skipping", qualified)
            continue
        op.execute(f"DROP TABLE IF EXISTS {qualified}")
        logger.info("core_151: dropped %s", qualified)


def downgrade() -> None:
    # These tables are dead backups of already-mutated/dropped source tables.
    # Recreating the row data is not possible. Intentional no-op, consistent
    # with core_118 and the other irreversible drop migrations in this chain.
    logger.info(
        "core_151 downgrade: no-op — leftover cutover backup tables cannot be "
        "recreated without source data that no longer exists."
    )
