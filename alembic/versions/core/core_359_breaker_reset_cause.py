"""breaker_resets: record the cause each manual reset claims to address (bu-ejow5p.8).

Revision ID: core_359
Revises: core_358

REQ-qa-remediation-liveness-003.  A manual QA breaker reset buys five more attempts. When the
failure streak was caused by a missing capability (for example no publisher), a reset that does not
name what changed only buys five more identical failures. Two nullable columns record the cause
code the reset addresses and the operator's reference to what changed (a PR, a bead, a deploy).
Historic rows keep NULL: they were reset before a cause was required, and are never rewritten.

A refused reset is an owner-visible decision, so it is recorded too: ``outcome`` is ``reset``
(default, so every historic row and every legacy writer is a real reset) or ``refused``, and
``refusal_reason`` carries the machine reason. A refusal row must never clear the breaker, so the
shared QA admission filter (``QA_BREAKER_WHERE_SQL``) counts only ``outcome = 'reset'`` rows.

All columns are additive. Table-level grants from core_164 already cover new columns.
"""

from __future__ import annotations

from alembic import op

revision = "core_359"
down_revision = "core_358"
branch_labels = None
depends_on = None


_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_359:breaker-reset-cause', 0))"

_ALREADY_APPLIED = """
SELECT (
    SELECT count(*) FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'breaker_resets'
      AND column_name IN ('cause_code', 'cause_ref', 'outcome', 'refusal_reason')
) = 4 AND EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.breaker_resets'::regclass
      AND conname = 'breaker_resets_outcome_check'
)
"""


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the shared columns."""
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
                    THEN substring(version_num FROM 6)::integer >= 359
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    # The chain runs once per butler schema over this one shared table. Serialize the schemas, and
    # let a later one that finds the work done read the catalog only: ADD COLUMN IF NOT EXISTS
    # takes ACCESS EXCLUSIVE even as a no-op, held to the end of the transaction (bu-opskk2).
    op.execute(_LOCK)
    done = op.get_bind().exec_driver_sql(_ALREADY_APPLIED)
    if done.scalar():
        return
    op.execute("ALTER TABLE public.breaker_resets ADD COLUMN IF NOT EXISTS cause_code TEXT")
    op.execute("ALTER TABLE public.breaker_resets ADD COLUMN IF NOT EXISTS cause_ref TEXT")
    op.execute(
        "ALTER TABLE public.breaker_resets "
        "ADD COLUMN IF NOT EXISTS outcome TEXT NOT NULL DEFAULT 'reset'"
    )
    op.execute("ALTER TABLE public.breaker_resets ADD COLUMN IF NOT EXISTS refusal_reason TEXT")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'public.breaker_resets'::regclass
                  AND conname = 'breaker_resets_outcome_check'
            ) THEN
                ALTER TABLE public.breaker_resets
                    ADD CONSTRAINT breaker_resets_outcome_check
                    CHECK (outcome IN ('reset', 'refused'));
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    # Only the last schema still on this revision undoes the shared change (core_246 pattern);
    # earlier schemas touch no table, so the DELETE below never holds a row lock while another
    # worker waits for the ALTERs' ACCESS EXCLUSIVE lock.
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    # Refusal rows are not resets; drop them so the downgraded filter-less readers stay correct.
    # The core chain runs once per butler schema over this one shared table, so an earlier
    # schema's downgrade has already dropped the column: guard the delete on its existence.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'breaker_resets'
                  AND column_name = 'outcome'
            ) THEN
                DELETE FROM public.breaker_resets WHERE outcome = 'refused';
            END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE public.breaker_resets DROP CONSTRAINT IF EXISTS breaker_resets_outcome_check"
    )
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS refusal_reason")
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS outcome")
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS cause_ref")
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS cause_code")
