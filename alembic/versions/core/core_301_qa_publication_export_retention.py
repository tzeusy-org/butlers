"""Prune retained QA export bytes after retention, keeping the binding (bu-mc01y5.25).

Revision ID: core_301
Revises: core_300
Create Date: 2026-11-02 00:00:00.000000

core_294 made ``public.qa_publication_exports`` immutable with no runtime removal path:
up to 21 MiB per attempt, excluded from backups, growing without bound.  Spec
REQ-qa-publication-authority-006 still requires the sealed artifact and binding to be
retained until exact reconciliation resolves the hold, so pruning is narrow:

* ``pruned_at`` (nullable ``timestamptz``).  A prune empties ``objects`` and stamps it;
  ``ck_qa_publication_exports_pruned`` keeps a pruned row empty.  The row stays as a
  tombstone: base, head, commits, digest and ``retained_at`` keep binding the proposal for
  the case API (``export_retained``) and the reconciliation sweep (``head_sha``).  No row
  is ever deleted, so the core_294 rollback refusal still holds.
* ``qa_publication_exports_immutable`` now admits exactly one rewrite, for every role
  (a privileged login that bypasses row security included): unpruned to pruned, every
  bound column unchanged, ``objects`` emptied, and only while the attempt is terminal,
  closed at least 30 days ago by database time, and holds no publication lineage (no
  operation in ``egress_started`` or ``ambiguous``).  The guard stamps ``pruned_at`` with
  database time.  Any other UPDATE still refuses, and a pruned row is final.
* ``qa_publication_exports_unpruned_insert`` (BEFORE INSERT, every role) refuses a row
  inserted with ``pruned_at`` set, so the guarded UPDATE is the only way to a tombstone.
* Only ``butler_qa_rw`` prunes: forced row security gains an UPDATE policy for that role
  alone (``scripts/init-db.sql`` re-grants DML to every runtime role, so the policy, not
  the grant, is the fence).

The retention window lives here and in ``retention.EXPORT_RETENTION``; change both.  It is
measured from ``healing_attempts.closed_at`` as the QA role writes it.  That table has no
row-security fence (runtime roles hold public DML), so the window is only as trustworthy as
its writers; the lineage-hold check reads the fenced ledger.

The core chain runs once per butler schema over one shared table, so only the last
schema to leave core_301 removes the prune path, and only while no export is pruned:
an emptied row would otherwise read back as tampered (core_246 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_301"
down_revision = "core_300"
branch_labels = None
depends_on = None

_TABLE = "public.qa_publication_exports"
_GUARD = "public.qa_publication_exports_immutable"
_INSERT_GUARD = "public.qa_publication_exports_unpruned_insert"
_INSERT_TRIGGER = "qa_publication_exports_unpruned_insert"
_QA_ROLE = "butler_qa_rw"
_CONSTRAINT = "ck_qa_publication_exports_pruned"
_POLICY = "qa_publication_exports_prune"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_301:qa-exports', 0))"

_ROLLBACK_REFUSAL = (
    "core_301 cannot remove QA export pruning while a pruned export exists: its emptied "
    "bytes would read back as tampered. Keep core_301 installed."
)

# The core_294 guard body, verbatim.
_CORE_294_GUARD = f"""
        CREATE OR REPLACE FUNCTION {_GUARD}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'retained QA export is immutable'
                USING ERRCODE = 'check_violation';
        END
        $$
        """

_PRUNE_GUARD = f"""
        CREATE OR REPLACE FUNCTION {_GUARD}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.pruned_at IS NOT NULL THEN
                RAISE EXCEPTION 'retained QA export: a pruned export is final'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
                OR NEW.base_sha IS DISTINCT FROM OLD.base_sha
                OR NEW.head_sha IS DISTINCT FROM OLD.head_sha
                OR NEW.commits IS DISTINCT FROM OLD.commits
                OR NEW.digest IS DISTINCT FROM OLD.digest
                OR NEW.retained_at IS DISTINCT FROM OLD.retained_at
                OR NEW.pruned_at IS NULL
                OR octet_length(NEW.objects) <> 0 THEN
                RAISE EXCEPTION 'retained QA export is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.healing_attempts AS a
                WHERE a.id = OLD.attempt_id
                  AND a.status IN ('pr_merged', 'failed', 'unfixable', 'anonymization_failed',
                                   'timeout')
                  AND a.closed_at <= now() - interval '30 days'
            ) THEN
                RAISE EXCEPTION 'retained QA export: attempt is not past retention'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.qa_publication_operations AS op
                WHERE op.attempt_id = OLD.attempt_id
                  AND op.stage IN ('egress_started', 'ambiguous')
            ) THEN
                RAISE EXCEPTION 'retained QA export: publication lineage is held'
                    USING ERRCODE = 'check_violation';
            END IF;
            NEW.pruned_at := now();
            RETURN NEW;
        END
        $$
        """

# Pruning is reachable only through the guarded UPDATE: an INSERT that arrives already pruned
# would skip the eligibility checks and leave a tombstone no verified export ever backed.
_INSERT_GUARD_FUNCTION = f"""
        CREATE OR REPLACE FUNCTION {_INSERT_GUARD}() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.pruned_at IS NOT NULL THEN
                RAISE EXCEPTION 'retained QA export cannot be inserted pruned'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END
        $$
        """


def _for_existing_role(role: str, statement: str) -> None:
    escaped = statement.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE '{escaped}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
        END
        $$
        """
    )


def _core_301_installation_count() -> int:
    """Count schema-local core chains that still depend on the prune path (core_246)."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND namespace.nspname NOT LIKE 'pg_%%'
          AND namespace.nspname <> 'information_schema'
        """
    ).scalars()
    quote = bind.dialect.identifier_preparer.quote
    count = 0
    for schema in schemas:
        installed = bind.exec_driver_sql(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM {quote(schema)}.alembic_version
                WHERE CASE WHEN version_num ~ '^core_[0-9]+$'
                    THEN substring(version_num FROM 6)::integer >= 301
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS pruned_at TIMESTAMPTZ")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{_TABLE}'::regclass AND conname = '{_CONSTRAINT}'
            ) THEN
                ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT}
                    CHECK (pruned_at IS NULL OR octet_length(objects) = 0);
            END IF;
        END
        $$
        """
    )
    op.execute(_PRUNE_GUARD)
    op.execute(_INSERT_GUARD_FUNCTION)
    op.execute(f"DROP TRIGGER IF EXISTS {_INSERT_TRIGGER} ON {_TABLE}")
    op.execute(
        f"""
        CREATE TRIGGER {_INSERT_TRIGGER}
            BEFORE INSERT ON {_TABLE}
            FOR EACH ROW EXECUTE FUNCTION {_INSERT_GUARD}()
        """
    )
    _for_existing_role(
        _QA_ROLE, f"GRANT UPDATE (objects, pruned_at) ON TABLE {_TABLE} TO {_QA_ROLE}"
    )
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    op.execute(
        f"""
        CREATE POLICY {_POLICY} ON {_TABLE}
            FOR UPDATE
            USING (current_user = '{_QA_ROLE}')
            WITH CHECK (current_user = '{_QA_ROLE}')
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared table serves every schema still at core_301; only the last one removes it.
    if _core_301_installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE} WHERE pruned_at IS NOT NULL) THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP POLICY IF EXISTS {_POLICY} ON {_TABLE}")
    _for_existing_role(
        _QA_ROLE, f"REVOKE UPDATE (objects, pruned_at) ON TABLE {_TABLE} FROM {_QA_ROLE}"
    )
    op.execute(f"DROP TRIGGER IF EXISTS {_INSERT_TRIGGER} ON {_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_INSERT_GUARD}()")
    op.execute(_CORE_294_GUARD)
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS pruned_at")
