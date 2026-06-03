"""drop_contact_info: final drop of public.contact_info (migration bead 10, bu-e2ja9).

Spec anchor: Brief §6b Amendment 1.1.A.6 (drop is a separate dated decision after
the triple-store-only soak; backups retained ≥ 90 days) + Amendment 1.1.C bead 10.
Parent epic: bu-uhjxr.

This is the **final, irreversible-in-spirit** step of the contacts→triples
migration.  By the time this migration runs:

- Non-secured channel identifiers have been backfilled into
  ``relationship.entity_facts`` (``has-email`` / ``has-phone`` / ``has-website`` /
  ``has-handle``) — see migration beads 3/7/8 and the backfill scripts.
- ``secured = true`` rows have been carved out to ``public.entity_info`` (#2042).
- All live request-path readers have been re-pointed to ``relationship.entity_facts``
  (bu-l5w8a; contacts-sync matcher migrated alongside this bead).

Self-guarding design
--------------------
``upgrade()`` will **refuse to drop** the table if doing so would silently lose
data.  Concretely it:

1. **Snapshots** ``public.contact_info`` → ``public.contact_info_dropbak_core_115``
   (a full row-for-row copy) so the drop is recoverable for the 90-day retention
   window mandated by Amendment 1.1.A.6.
2. **Parity-guards**: counts non-secured, entity-linked rows whose
   ``contact_info.type`` maps to a channel predicate but which have **no matching
   active triple** in ``relationship.entity_facts`` (mirrors the proven
   ``run_contact_info_reconciler`` sweep, including the ``telegram:`` object
   prefix).  Owner-accepted unmapped types (default ``google_health``) are
   excluded.  If the count is non-zero the migration **raises** and drops nothing.
3. **Drops** ``public.contact_info`` only once parity is clean.

The parity guard is the in-migration enforcement of the bead-9 (bu-hpv4u)
sign-off precondition: *backfill applied + gap re-verified = 0*.  An operator who
has explicit owner sign-off to accept residual loss may set
``CONTACT_INFO_DROP_FORCE=1`` to bypass the raise (the snapshot is still taken).

Reversibility
-------------
``downgrade()`` recreates the table DDL (core_002 + core_083 ``context`` column)
and restores rows from the ``contact_info_dropbak_core_115`` snapshot when present.
This is best-effort: the canonical store after this migration is
``relationship.entity_facts`` and the snapshot is pruned after 90 days.
"""

from __future__ import annotations

import logging
import os

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger("alembic.runtime.migration")

# revision identifiers, used by Alembic.
revision = "core_115"
down_revision = "core_114"
branch_labels = None
depends_on = None

# Cross-chain note: the relationship migration rel_019 JOINs public.contact_info
# to prefix telegram has-handle objects. Multiple alembic version_locations have
# no guaranteed ordering, so on a fresh provision core_115 (the drop) may run
# before or after rel_019. rel_019 carries a to_regclass guard so it no-ops when
# the table is already gone; the two migrations are therefore order-independent.


_BACKUP_TABLE = "public.contact_info_dropbak_core_115"

# Channel-type → predicate map.  Mirrors the proven sweep in
# ``roster/relationship/jobs/relationship_jobs.run_contact_info_reconciler`` and
# ``src/butlers/identity._CHANNEL_TYPE_TO_PREDICATE``.  Telegram-family types are
# stored in ``entity_facts`` under ``has-handle`` with a ``telegram:`` object
# prefix (bead bu-wni4z); legacy rows may carry the verbatim value, so the parity
# check accepts both encodings.
_PARITY_SWEEP_SQL = sa.text(
    """
    SELECT ci.type AS ci_type, count(*) AS n
    FROM public.contact_info ci
    JOIN public.contacts c ON c.id = ci.contact_id
    JOIN public.entities e ON e.id = c.entity_id
    WHERE ci.secured = false
      AND c.entity_id IS NOT NULL
      AND (e.metadata->>'merged_into') IS NULL
      AND NOT (ci.type = ANY(:accepted_unmapped))
      AND NOT EXISTS (
          SELECT 1
          FROM relationship.entity_facts ef
          WHERE ef.subject   = c.entity_id
            AND ef.predicate = CASE ci.type
                  WHEN 'email'             THEN 'has-email'
                  WHEN 'phone'             THEN 'has-phone'
                  WHEN 'website'           THEN 'has-website'
                  WHEN 'telegram'          THEN 'has-handle'
                  WHEN 'telegram_user_id'  THEN 'has-handle'
                  WHEN 'telegram_username' THEN 'has-handle'
                  WHEN 'linkedin'          THEN 'has-handle'
                  WHEN 'twitter'           THEN 'has-handle'
                  WHEN 'other'             THEN 'has-handle'
                  ELSE 'has-' || ci.type
              END
            AND ef.object IN (
                  ci.value,
                  CASE ci.type
                      WHEN 'telegram'          THEN 'telegram:' || ci.value
                      WHEN 'telegram_user_id'  THEN 'telegram:' || ci.value
                      WHEN 'telegram_username' THEN 'telegram:' || ci.value
                      ELSE ci.value
                  END
              )
            AND ef.validity  = 'active'
      )
    GROUP BY ci.type
    ORDER BY n DESC
    """
)


def _accepted_unmapped_types() -> list[str]:
    """Channel types the owner has accepted as having no triple home (no loss)."""
    raw = os.environ.get("CONTACT_INFO_DROP_ACCEPTED_UNMAPPED_TYPES", "google_health")
    return [t.strip() for t in raw.split(",") if t.strip()]


def _forced() -> bool:
    return os.environ.get("CONTACT_INFO_DROP_FORCE") in ("1", "true", "yes")


def upgrade() -> None:
    bind = op.get_bind()

    # Idempotency: already dropped (e.g. migration re-run) → nothing to do.
    if bind.execute(sa.text("SELECT to_regclass('public.contact_info')")).scalar() is None:
        return

    # 1. Snapshot (full copy) for the 90-day recovery window.
    op.execute(f"CREATE TABLE IF NOT EXISTS {_BACKUP_TABLE} AS TABLE public.contact_info WITH DATA")

    # 2. Parity guard — refuse to drop if non-secured mapped rows lack a triple.
    if bind.execute(sa.text("SELECT to_regclass('relationship.entity_facts')")).scalar() is None:
        # The triple store is absent — e.g. the core chain provisioned in
        # isolation, or core_115 scheduled before the relationship chain on a
        # fresh DB. We cannot verify zero-loss against it, so only proceed when
        # public.contact_info is empty (a fresh DB has nothing to lose). On a real
        # upgrade of an existing deployment the relationship chain ran long ago, so
        # this branch is reached only with an empty, freshly-created table.
        remaining = bind.execute(sa.text("SELECT count(*) FROM public.contact_info")).scalar()
        if remaining and not _forced():
            raise RuntimeError(
                f"core_115 ABORTED: relationship.entity_facts is absent so zero-loss "
                f"cannot be verified, but public.contact_info still holds {remaining} "
                f"row(s). Apply the relationship chain + backfill first, or set "
                f"CONTACT_INFO_DROP_FORCE=1 with explicit owner sign-off. A full "
                f"snapshot was taken at {_BACKUP_TABLE}."
            )
    else:
        accepted = _accepted_unmapped_types()
        gap_rows = bind.execute(_PARITY_SWEEP_SQL, {"accepted_unmapped": accepted}).fetchall()
        gap_total = sum(int(r.n) for r in gap_rows)
        if gap_total > 0:
            breakdown = ", ".join(f"{r.ci_type}={int(r.n)}" for r in gap_rows)
            msg = (
                f"core_115 ABORTED: {gap_total} non-secured, entity-linked "
                f"public.contact_info rows have no matching active triple in "
                f"relationship.entity_facts and would be silently lost by the drop "
                f"(by type: {breakdown}). Run the backfill to apply these rows, or "
                f"set CONTACT_INFO_DROP_FORCE=1 only with explicit owner sign-off to "
                f"accept the loss. Accepted-unmapped types this run: {accepted}. "
                f"A full snapshot was taken at {_BACKUP_TABLE}."
            )
            if not _forced():
                raise RuntimeError(msg)
            # Forced: proceed but leave a loud trail in the migration log.
            logger.warning("CONTACT_INFO_DROP_FORCE override: %s", msg)

    # 3. Drop (CASCADE clears the parent_id self-FK; no external FK references it).
    op.execute("DROP TABLE IF EXISTS public.contact_info CASCADE")


def downgrade() -> None:
    # Recreate the table DDL (core_002 + core_083 context column).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.contact_info (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id  UUID NOT NULL REFERENCES public.contacts(id) ON DELETE CASCADE,
            type        VARCHAR NOT NULL,
            value       TEXT NOT NULL,
            label       VARCHAR,
            is_primary  BOOLEAN DEFAULT false,
            secured     BOOLEAN NOT NULL DEFAULT false,
            parent_id   UUID REFERENCES public.contact_info(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ DEFAULT now(),
            context     VARCHAR CHECK (context IN ('personal', 'work', 'other')),
            CONSTRAINT uq_shared_contact_info_type_value UNIQUE (type, value)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_contact_info_contact_id "
        "ON public.contact_info (contact_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shared_contact_info_parent_id "
        "ON public.contact_info (parent_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_contact_info_context ON public.contact_info (context)"
    )

    # Restore rows from the snapshot if it is still present.
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT to_regclass('{_BACKUP_TABLE}')")).scalar() is not None:
        op.execute(
            f"INSERT INTO public.contact_info SELECT * FROM {_BACKUP_TABLE} ON CONFLICT DO NOTHING"
        )
