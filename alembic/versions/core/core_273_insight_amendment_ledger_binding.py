"""Bind premise amendments to the resolved ledger and keep them after candidate cleanup.

Revision ID: core_273
Revises: core_268
Create Date: 2026-10-19 00:00:00.000000

bu-q7vx1q.32 (merged with bu-q7vx1q.33).  Two defects in the core_255 seam:

* ``public.enqueue_premise_amendments`` is SECURITY DEFINER but trusted the caller-supplied
  source, fingerprint and ``resolved_at``, so any role granted EXECUTE could queue a
  correction for any premise without a resolved ledger row.  It now inserts only when
  ``public.owner_conditions`` holds a ``resolved`` row with exactly that source, fingerprint
  and ``resolved_at`` (the real witness), uses that row's ``resolved_at`` for the episode
  key, and otherwise returns 0.  Signature, ``SECURITY DEFINER``, the pinned
  ``search_path`` and the per-role EXECUTE grants are unchanged.
* ``public.insight_amendments.candidate_id`` referenced ``insight_candidates`` with
  ``ON DELETE CASCADE``, so deleting a candidate erased its amendment history.  The foreign
  key is dropped (the column stays NOT NULL and the UNIQUE key stays); the broker's joins to
  ``insight_candidates`` skip an amendment whose candidate no longer exists.

Every statement is idempotent: the core chain runs once per butler schema against these
shared ``public`` objects.  The downgrade of the last schema still at core_273 restores the
core_255 function body and re-adds the cascading foreign key ``NOT VALID``; earlier schemas
leave the shared objects alone, as core_246/266/268 do.  Rows orphaned while the key was
absent are preserved, and the key is enforced for new rows only.
"""

from __future__ import annotations

from alembic import op

revision = "core_273"
down_revision = "core_268"
branch_labels = None
depends_on = None

_FK = "insight_amendments_candidate_id_fkey"

# The core_255 body, restored verbatim by the downgrade.
_OLD_ENQUEUE_FN = """
CREATE OR REPLACE FUNCTION public.enqueue_premise_amendments(
    p_source text,
    p_fingerprint text,
    p_resolved_at timestamptz,
    p_summary text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    inserted integer;
BEGIN
    INSERT INTO public.insight_amendments (candidate_id, episode_key, reason, summary)
    SELECT c.id,
           p_fingerprint || '@' || p_resolved_at::text,
           'premise_resolved',
           p_summary
    FROM public.insight_candidates c
    WHERE c.status = 'delivered'
      AND c.delivered_at <= p_resolved_at
      AND c.premise ->> 'kind' = 'owner_condition'
      AND c.premise ->> 'source' = p_source
      AND c.premise ->> 'fingerprint' = p_fingerprint
    ON CONFLICT (candidate_id, episode_key) DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    RETURN inserted;
END;
$fn$;
"""

# Verifies, never trusts: the resolved owner_conditions row must exist with exactly the
# supplied source, fingerprint and resolved_at, and its own resolved_at is what is used.
_NEW_ENQUEUE_FN = """
CREATE OR REPLACE FUNCTION public.enqueue_premise_amendments(
    p_source text,
    p_fingerprint text,
    p_resolved_at timestamptz,
    p_summary text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    inserted integer;
BEGIN
    INSERT INTO public.insight_amendments (candidate_id, episode_key, reason, summary)
    SELECT c.id,
           oc.fingerprint || '@' || oc.resolved_at::text,
           'premise_resolved',
           p_summary
    FROM public.owner_conditions oc
    JOIN public.insight_candidates c
      ON c.premise ->> 'kind' = 'owner_condition'
     AND c.premise ->> 'source' = oc.source
     AND c.premise ->> 'fingerprint' = oc.fingerprint
    WHERE oc.source = p_source
      AND oc.fingerprint = p_fingerprint
      AND oc.state = 'resolved'
      AND oc.resolved_at = p_resolved_at
      AND c.status = 'delivered'
      AND c.delivered_at <= oc.resolved_at
    ON CONFLICT (candidate_id, episode_key) DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    RETURN inserted;
END;
$fn$;
"""


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _execute_best_effort(statement: str) -> None:
    op.execute(
        f"""
        DO $do$
        BEGIN
            EXECUTE {_quote_literal(statement)};
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $do$;
        """
    )


def upgrade() -> None:
    # CREATE OR REPLACE keeps the owner, the pinned search_path and the EXECUTE grants
    # core_255 set (REVOKE PUBLIC, per-role GRANT); nothing here widens access.
    _execute_best_effort(_NEW_ENQUEUE_FN)
    _execute_best_effort(f"ALTER TABLE public.insight_amendments DROP CONSTRAINT IF EXISTS {_FK}")


_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_273:amendment-binding', 0))"


def _core_273_installation_count() -> int:
    """Count schema-local core chains still at or after core_273 (core_268 pattern)."""
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
                    THEN substring(version_num FROM 6)::integer >= 273
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def downgrade() -> None:
    op.execute(_LOCK)
    # The definer and the foreign key are shared ``public`` objects: every schema still at
    # core_273 runs code that expects the bound body and no cascade, so only the last
    # schema leaving restores the core_255 body (which trusts the caller) and the cascade.
    if _core_273_installation_count() > 1:
        return
    _execute_best_effort(_OLD_ENQUEUE_FN)
    _execute_best_effort(f"ALTER TABLE public.insight_amendments DROP CONSTRAINT IF EXISTS {_FK}")
    _execute_best_effort(
        f"""
        ALTER TABLE public.insight_amendments
            ADD CONSTRAINT {_FK} FOREIGN KEY (candidate_id)
            REFERENCES public.insight_candidates(id) ON DELETE CASCADE NOT VALID
        """
    )
