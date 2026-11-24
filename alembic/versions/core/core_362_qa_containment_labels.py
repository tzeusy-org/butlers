"""Label how a QA attempt was contained, and flag exports sealed before isolation (bu-ejow5p.11).

Two additive columns:

``public.healing_attempts.containment`` (nullable ``TEXT``)
    ``'none'`` for an investigation run in the daemon's own process tree (today's worktree
    path) and ``'job'`` for one run in a per-attempt credential-free Job. ``NULL`` is a row
    written before the label existed and reads as ``'none'``: nothing about it was isolated.

``public.qa_publication_exports.sealed_before_isolation`` (``BOOLEAN NOT NULL DEFAULT true``)
    Set at seal time, true for any export whose investigation ran without Job isolation. The
    default is true so an unlabelled or legacy export is flagged, never trusted. A publisher
    MUST require a per-pull-request owner approval record for a flagged export before it
    pushes (the precondition is recorded in ``docs/operations/qa-publication-boundary.md``;
    no publisher is configured today, so nothing enforces it yet).

The retained-export guard (core_301) now also refuses any UPDATE that changes the flag, for
every role (a privileged login included), so the flag cannot be cleared after sealing.

The core chain runs once per butler schema over these shared tables, so every statement is
idempotent and only the last schema to leave core_362 removes them (core_246 pattern).
"""

from __future__ import annotations

from alembic import op

revision = "core_362"
down_revision = "core_361"
branch_labels = None
depends_on = None

_EXPORTS = "public.qa_publication_exports"
_ATTEMPTS = "public.healing_attempts"
_GUARD = "public.qa_publication_exports_immutable"
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_362:qa-containment', 0))"
_CHECK = "ck_healing_attempts_containment"


def _guard(*, with_flag: bool) -> str:
    """The core_301 guard body; ``with_flag`` adds the sealed-before-isolation comparison."""
    flag = (
        "OR NEW.sealed_before_isolation IS DISTINCT FROM OLD.sealed_before_isolation\n"
        if (with_flag)
        else ""
    )
    return f"""
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
                {flag}OR NEW.pruned_at IS NULL
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


def _installation_count() -> int:
    """Count schema-local core chains that still depend on this revision (core_246)."""
    bind = op.get_bind()
    schemas = bind.exec_driver_sql(
        """
        SELECT namespace.nspname
        FROM pg_class version_table
        JOIN pg_namespace namespace ON namespace.oid = version_table.relnamespace
        WHERE version_table.relname = 'alembic_version'
          AND version_table.relkind IN ('r', 'p')
          AND left(namespace.nspname, 3) <> 'pg_'
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
                    THEN substring(version_num FROM 6)::integer >= 362
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(f"ALTER TABLE {_ATTEMPTS} ADD COLUMN IF NOT EXISTS containment TEXT")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{_ATTEMPTS}'::regclass AND conname = '{_CHECK}'
            ) THEN
                ALTER TABLE {_ATTEMPTS} ADD CONSTRAINT {_CHECK}
                    CHECK (containment IS NULL OR containment IN ('none', 'job'));
            END IF;
        END
        $$
        """
    )
    op.execute(
        f"ALTER TABLE {_EXPORTS} "
        "ADD COLUMN IF NOT EXISTS sealed_before_isolation BOOLEAN NOT NULL DEFAULT true"
    )
    op.execute(_guard(with_flag=True))


def downgrade() -> None:
    op.execute(_LOCK)
    # One shared table serves every schema still at core_362; only the last one removes it.
    if _installation_count() > 1:
        return
    op.execute(_guard(with_flag=False))
    op.execute(f"ALTER TABLE {_EXPORTS} DROP COLUMN IF EXISTS sealed_before_isolation")
    op.execute(f"ALTER TABLE {_ATTEMPTS} DROP CONSTRAINT IF EXISTS {_CHECK}")
    op.execute(f"ALTER TABLE {_ATTEMPTS} DROP COLUMN IF EXISTS containment")
