"""priority_contacts: drop the vestigial ``butler`` dimension (make it global).

Issue: bu-gx13h.  Owner decision on bu-v7ek4 (2026-06-18): make
``public.priority_contacts`` BUTLER-AGNOSTIC. The ``butler`` column is vestigial —
``GmailPolicyEvaluator`` (connectors/gmail_policy.py) is the sole runtime consumer
and only ever read ``WHERE butler = 'gmail'``; the dashboard hardcoded the same
value. This migration retires the column and collapses the primary key from
``(contact_id, butler)`` to ``(contact_id)``.

Self-guarding design (mirrors core_115 / core_118 / core_123)
------------------------------------------------------------
``upgrade()`` refuses to lose data silently:

1. **Idempotency** — if the ``butler`` column is already gone (re-run / fresh DB),
   the column-drop steps are skipped, but the butler-less cascade-audit trigger
   is still re-asserted (step 6) so a schema-scoped re-run of core_101 cannot
   leave a butler-referencing trigger behind.
2. **Snapshot** — copies every ``(contact_id, butler, added_at, added_by)`` row to
   ``public.priority_contacts_butler_dropbak_core_129`` so the drop is recoverable.
3. **Dedup** — collapse to one row per ``contact_id`` before the new PK is applied.
   The keeper preference is: the ``butler = 'gmail'`` row first (the canonical
   runtime entry), then the earliest ``added_at`` — so a contact that was a Gmail
   priority sender keeps its original add-metadata.
4. **Parity guard** — every distinct ``contact_id`` that existed before the dedup
   must still be present afterwards. A non-zero gap **raises** and drops nothing
   (the snapshot is still taken). This is the destructive-drop bar.
5. **Drop** — drop the per-butler index, the composite PK, and the ``butler``
   column; add the new single-column PK ``(contact_id)``.
6. **Trigger** — recreate ``public.priority_contacts_cascade_audit()`` so its
   ``target`` no longer references ``OLD.butler`` (which would now be a missing
   column and break every cascade DELETE).

Cross-chain hazard (cross-chain-migration-drop-hazard)
------------------------------------------------------
No sibling chain INSERTs into ``public.priority_contacts`` (verified: only the
core chain references it), so there is no ordering-dependent INSERT to guard. The
whole upgrade is wrapped in a ``to_regclass`` existence check so it no-ops on a
core provision where the table has not been created yet.

Reversibility
-------------
``downgrade()`` re-adds the ``butler`` column (defaulting surviving rows to
``'gmail'``), restores the rows deduped away from the snapshot when present,
recreates the composite PK + per-butler index, and restores the butler-suffixed
cascade-audit trigger. Best-effort: the canonical store after this migration is
the global per-contact row; the snapshot is a recovery aid.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_129"
down_revision = "core_128"
branch_labels = None
depends_on = None

_TABLE = "public.priority_contacts"
_BACKUP_TABLE = "public.priority_contacts_butler_dropbak_core_129"

_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "connector_writer",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def _table_exists(bind) -> bool:
    return bind.execute(sa.text(f"SELECT to_regclass('{_TABLE}')")).scalar() is not None


def _column_exists(bind, schema: str, table: str, column: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = :schema
                  AND table_name   = :table
                  AND column_name  = :column
                """
            ),
            {"schema": schema, "table": table, "column": column},
        ).scalar()
    )


# Dedup: keep exactly one row per contact_id. Prefer the butler='gmail' row (the
# canonical runtime entry), then the earliest added_at. ctid is the physical row
# id used as a stable, unique tie-break and delete handle (no surrogate key here).
_DEDUP_SQL = sa.text(
    f"""
    DELETE FROM {_TABLE} pc
    USING (
        SELECT
            ctid,
            row_number() OVER (
                PARTITION BY contact_id
                ORDER BY (butler = 'gmail') DESC, added_at ASC, ctid
            ) AS rn
        FROM {_TABLE}
    ) ranked
    WHERE pc.ctid = ranked.ctid
      AND ranked.rn > 1
    """
)

# Parity guard: distinct contact_ids in the snapshot that no longer have a row.
_PARITY_SQL = sa.text(
    f"""
    SELECT count(*) AS n
    FROM (SELECT DISTINCT contact_id FROM {_BACKUP_TABLE}) b
    WHERE NOT EXISTS (
        SELECT 1 FROM {_TABLE} pc WHERE pc.contact_id = b.contact_id
    )
    """
)

_TRIGGER_FN_GLOBAL = """
    CREATE OR REPLACE FUNCTION public.priority_contacts_cascade_audit()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    SECURITY DEFINER
    AS $$
    BEGIN
        INSERT INTO public.audit_log (actor, action, target, note)
        VALUES (
            'system:contact_cascade',
            'ingestion.priority_contact.cascade_remove',
            OLD.contact_id::text,
            'contact removed from public.contacts'
        );
        RETURN OLD;
    END;
    $$
"""

_TRIGGER_FN_BUTLER = """
    CREATE OR REPLACE FUNCTION public.priority_contacts_cascade_audit()
    RETURNS TRIGGER
    LANGUAGE plpgsql
    SECURITY DEFINER
    AS $$
    BEGIN
        INSERT INTO public.audit_log (actor, action, target, note)
        VALUES (
            'system:contact_cascade',
            'ingestion.priority_contact.cascade_remove',
            OLD.contact_id::text || ':' || OLD.butler,
            'contact removed from public.contacts'
        );
        RETURN OLD;
    END;
    $$
"""


def upgrade() -> None:
    bind = op.get_bind()

    # 0. Table absent (fresh core-only provision before the table is created) → no-op.
    if not _table_exists(bind):
        return

    # 1. Drop the butler dimension — only when it is still present. (Idempotent:
    #    a re-run finds the column already gone and skips straight to the trigger
    #    re-assertion below.)
    if _column_exists(bind, "public", "priority_contacts", "butler"):
        # 2. Snapshot every row for recovery.
        op.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_BACKUP_TABLE} AS
            SELECT contact_id, butler, added_at, added_by
            FROM {_TABLE}
            """
        )

        # 3. Dedup to one row per contact_id (folding butler='gmail' rows first).
        bind.execute(_DEDUP_SQL)

        # 4. Parity guard — no contact_id may be lost by the dedup.
        gap = int(bind.execute(_PARITY_SQL).scalar() or 0)
        if gap > 0:
            raise RuntimeError(
                f"core_129 ABORTED: {gap} contact_id(s) from the snapshot have no "
                f"surviving row after dedup; dropping the butler column would lose them. "
                f"A full snapshot was taken at {_BACKUP_TABLE}."
            )

        # 5. Drop the per-butler index, composite PK, and butler column; add the new PK.
        #    (DROP COLUMN would cascade to the index, but be explicit.)
        op.execute("DROP INDEX IF EXISTS public.idx_priority_contacts_butler")
        op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS priority_contacts_pkey")
        op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS butler")
        op.execute(f"ALTER TABLE {_TABLE} ADD PRIMARY KEY (contact_id)")

    # 6. Always (re-)assert the butler-less cascade-audit trigger function. This
    #    runs even on the column-already-gone path: the schema-scoped migration
    #    runner re-executes the whole core chain per schema, and core_101's re-run
    #    reinstalls the butler-referencing function body — which would break every
    #    cascade DELETE against the now butler-less table. ``CREATE OR REPLACE``
    #    is body-late-bound, so re-asserting here is always safe.
    op.execute(_TRIGGER_FN_GLOBAL)

    # Re-grant on the table (privileges survive column drops, but keep parity with
    # core_101's grant block in case the table was recreated on a fresh DB).
    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort(_TABLE, _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind):
        return

    # Idempotency — butler column already present → nothing to do.
    if _column_exists(bind, "public", "priority_contacts", "butler"):
        return

    # Drop the single-column PK so multiple rows per contact_id are permitted again.
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS priority_contacts_pkey")

    # Re-add the butler column; default surviving (global) rows to 'gmail'.
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS butler TEXT")
    op.execute(f"UPDATE {_TABLE} SET butler = 'gmail' WHERE butler IS NULL")

    # Restore rows deduped away during upgrade, if the snapshot survives.
    if bind.execute(sa.text(f"SELECT to_regclass('{_BACKUP_TABLE}')")).scalar() is not None:
        op.execute(
            f"""
            INSERT INTO {_TABLE} (contact_id, butler, added_at, added_by)
            SELECT b.contact_id, b.butler, b.added_at, b.added_by
            FROM {_BACKUP_TABLE} b
            WHERE NOT EXISTS (
                SELECT 1 FROM {_TABLE} pc
                WHERE pc.contact_id = b.contact_id
                  AND pc.butler = b.butler
            )
            """
        )

    op.execute(f"ALTER TABLE {_TABLE} ALTER COLUMN butler SET NOT NULL")
    op.execute(f"ALTER TABLE {_TABLE} ADD PRIMARY KEY (contact_id, butler)")
    op.execute(f"CREATE INDEX IF NOT EXISTS idx_priority_contacts_butler ON {_TABLE} (butler)")

    # Restore the butler-suffixed cascade-audit trigger function.
    op.execute(_TRIGGER_FN_BUTLER)
