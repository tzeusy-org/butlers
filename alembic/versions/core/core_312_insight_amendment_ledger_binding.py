"""Bind premise amendments to the resolved ledger and keep them after candidate cleanup.

Revision ID: core_312
Revises: core_311
Create Date: 2026-11-03 00:00:00.000000

bu-q7vx1q.32 (merged with bu-q7vx1q.33).  Two defects in the core_255 seam:

* ``public.enqueue_premise_amendments`` is SECURITY DEFINER but trusted the caller-supplied
  source, fingerprint and ``resolved_at``, so any role granted EXECUTE could queue a
  correction for any premise without a resolved ledger row.  It now inserts only when
  ``public.owner_conditions`` holds a ``resolved`` row with exactly that source, fingerprint
  and ``resolved_at`` (the real witness), keys the episode on that row's values, and
  otherwise returns 0.  It also pins TimeZone/DateStyle so the ``resolved_at::text`` dedup
  key is session-independent, and stores a NULL summary whatever the caller passes (the
  broker shows the summary to the owner; every legitimate caller passes NULL).  Signature,
  ``SECURITY DEFINER``, the pinned ``search_path`` and the per-role EXECUTE grants are
  unchanged (CREATE OR REPLACE keeps them).
* Upgrade fails loudly unless the replacement, the dropped key and the widened CHECK all
  took effect: a database whose definer is owned by another role (bootstrap, bu-jjv2du)
  fails this migration until ownership converges, rather than recording the fix as applied
  while the forgeable body stays live.
* ``public.insight_amendments.candidate_id`` referenced ``insight_candidates`` with
  ``ON DELETE CASCADE``, so deleting a candidate erased its amendment history.  The foreign
  key is dropped (the column stays NOT NULL and the UNIQUE key stays).  Amendment evidence
  is kept indefinitely (owner decision 2026-10-27, docs/operations/data-retention.md): the
  state vocabulary gains the terminal ``orphaned``, which the broker's cleanup sets on a
  ``pending`` or ``fold`` row whose candidate is gone.

Every statement is idempotent: the core chain runs once per butler schema against these
shared ``public`` objects.  Only the last schema still at core_312 downgrades them
(core_308 pattern): it restores the core_255 body, folds ``orphaned`` to ``folded`` (both
terminal; documented lossy fold), restores the core_255 CHECK and re-adds the cascading
key ``NOT VALID``, so rows orphaned while it was absent are preserved and only new rows are
checked.
"""

from __future__ import annotations

from alembic import op

revision = "core_312"
down_revision = "core_311"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_312:amendment-binding', 0))"
_FK = "insight_amendments_candidate_id_fkey"
_CHECK = "chk_insight_amendments_state"
_CORE_255_STATES = "'pending', 'applied', 'fold', 'folded'"
_STATES = "'pending', 'applied', 'fold', 'folded', 'orphaned'"

# The core_255 body, restored verbatim by the downgrade.
CORE_255_ENQUEUE_FN = """
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
# supplied source, fingerprint and resolved_at, and its own values key the episode.
# TimeZone/DateStyle are pinned so ``resolved_at::text`` -- the dedup key -- renders the
# same from every session (byte-identical to keys core_255 wrote from UTC sessions).
# ``p_summary`` is ignored: the broker shows the summary to the owner, and no legitimate
# caller supplies one, so a caller with EXECUTE cannot plant owner-visible text.
ENQUEUE_FN = """
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
SET TimeZone = 'UTC'
SET DateStyle = 'ISO, MDY'
AS $fn$
DECLARE
    inserted integer;
BEGIN
    INSERT INTO public.insight_amendments (candidate_id, episode_key, reason, summary)
    SELECT c.id,
           oc.fingerprint || '@' || oc.resolved_at::text,
           'premise_resolved',
           NULL
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
    """core_255's tolerance: a schema replay that cannot own the shared object skips it."""
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


def _set_states(states: str) -> None:
    _execute_best_effort(
        f"ALTER TABLE public.insight_amendments DROP CONSTRAINT IF EXISTS {_CHECK}, "
        f"ADD CONSTRAINT {_CHECK} CHECK (state IN ({states}))"
    )


def _core_312_installation_count() -> int:
    """Count schema-local core chains still at or after core_312 (core_308 pattern)."""
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
                    THEN substring(version_num FROM 6)::integer >= 312
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


# The steps above are best-effort (a schema replay that cannot own a shared object skips
# it), but a skipped security fix must not be recorded as applied: a definer still owned
# by another role (bootstrap, bu-jjv2du) would keep the forgeable core_255 body live.
_ASSERT_UPGRADED = f"""
DO $do$
DECLARE
    fn regprocedure := to_regprocedure(
        'public.enqueue_premise_amendments(text, text, timestamptz, text)');
BEGIN
    IF fn IS NULL OR NOT EXISTS (
        SELECT 1 FROM pg_proc p
        WHERE p.oid = fn
          AND p.prosrc LIKE '%public.owner_conditions%'
          AND 'TimeZone=UTC' = ANY (p.proconfig)
    ) THEN
        RAISE EXCEPTION 'core_312: public.enqueue_premise_amendments was not replaced '
            '(is it owned by another role? see bu-jjv2du)';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.insight_amendments'::regclass AND conname = '{_FK}'
    ) THEN
        RAISE EXCEPTION 'core_312: the cascading candidate foreign key is still present';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.insight_amendments'::regclass AND conname = '{_CHECK}'
          AND pg_get_constraintdef(oid) LIKE '%orphaned%'
    ) THEN
        RAISE EXCEPTION 'core_312: the amendment state CHECK does not admit orphaned';
    END IF;
END
$do$;
"""


def upgrade() -> None:
    op.execute(_LOCK)
    _execute_best_effort(ENQUEUE_FN)
    _execute_best_effort(f"ALTER TABLE public.insight_amendments DROP CONSTRAINT IF EXISTS {_FK}")
    _set_states(_STATES)
    op.execute(_ASSERT_UPGRADED)


def downgrade() -> None:
    op.execute(_LOCK)
    # Shared ``public`` objects: every schema still at core_312 runs code that expects the
    # bound body, no cascade and ``orphaned``, so only the last schema leaving restores them.
    if _core_312_installation_count() > 1:
        return
    _execute_best_effort(CORE_255_ENQUEUE_FN)
    _execute_best_effort(
        "UPDATE public.insight_amendments SET state = 'folded' WHERE state = 'orphaned'"
    )
    _set_states(_CORE_255_STATES)
    _execute_best_effort(f"ALTER TABLE public.insight_amendments DROP CONSTRAINT IF EXISTS {_FK}")
    _execute_best_effort(
        f"ALTER TABLE public.insight_amendments ADD CONSTRAINT {_FK} FOREIGN KEY (candidate_id) "
        "REFERENCES public.insight_candidates(id) ON DELETE CASCADE NOT VALID"
    )
