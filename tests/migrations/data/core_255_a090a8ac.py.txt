"""Premise-bound proactive speech: premise, delivery_ref, amendments, bill probe.

Revision ID: core_255
Revises: core_254
Create Date: 2026-10-03 00:00:00.000000

bu-q7vx1q.5.  A proactive insight asserts a fact ("a bill is due tomorrow").
Until now the only delivery-time check was a prepared action's status, so a
nudge whose fact stopped being true was either sent anyway or stayed on the
owner's screen asserting it.  This migration adds the durable pieces:

- ``insight_candidates.premise`` (JSONB, NULL = unbound, behaves as before):
  ``{"kind": "owner_condition", "source", "fingerprint"}`` or
  ``{"kind": "probe", "butler", "probe", "args"}``.
- ``insight_candidates.delivery_ref`` (JSONB): where a delivered candidate
  landed (notification id, channel, chat id, provider message id, digest line
  index), so it can be amended in place later.
- ``insight_candidates.status`` gains ``withdrawn`` (premise false at send).
- ``attention_ledger.outcome`` gains ``withdrawn`` and ``amended``.
- ``public.insight_amendments``: one row per (candidate, premise episode); the
  UNIQUE key makes enqueue idempotent across repeated reconciles. ``state`` is
  ``pending`` (to try an in-place edit), ``applied`` (edited), ``fold`` (edit
  not possible; ride the next delivery as a "since last digest" line) or
  ``folded`` (that line was sent).
- ``public.enqueue_premise_amendments()`` (SECURITY DEFINER): the producer's
  ledger ``post_write`` hook calls it inside the reconcile transaction, so a
  producer role never needs access to the Switchboard-owned tables.
- ``public.resolve_finance_bill_status()`` (SECURITY DEFINER): the narrow,
  hard-coded lookup behind the finance ``bill_still_pending`` probe, mirroring
  core_226's rationale (the Switchboard pool cannot read ``finance.bills``).

Every statement is idempotent: the core chain runs once per butler schema
against these shared ``public`` objects.  Downgrade folds new ledger outcomes
and ``withdrawn`` rows back into the prior vocabulary before narrowing the
constraints, then drops the new objects.
"""

from __future__ import annotations

from alembic import op

revision = "core_255"
down_revision = "core_254"
branch_labels = None
depends_on = None

_SWITCHBOARD_ROLE = "butler_switchboard_rw"
_AMENDMENTS_POLICY = "insight_amendments_switchboard"

_OLD_LEDGER_OUTCOMES = ("delivered", "coalesced", "deferred", "suppressed", "failed", "expired")
_NEW_LEDGER_OUTCOMES = (*_OLD_LEDGER_OUTCOMES, "withdrawn", "amended")
_OLD_STATUSES = ("pending", "delivered", "expired", "filtered")
_NEW_STATUSES = (*_OLD_STATUSES, "withdrawn")

# plpgsql (not sql) so the finance.bills reference resolves at call time, and
# search_path is pinned to pg_catalog, the standard SECURITY DEFINER hardening.
CREATE_BILL_STATUS_FN = """
CREATE OR REPLACE FUNCTION public.resolve_finance_bill_status(p_bill_id uuid)
RETURNS TABLE(status text)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $fn$
BEGIN
    RETURN QUERY
    SELECT b.status::text
    FROM finance.bills b
    WHERE b.id = p_bill_id;
END;
$fn$;
"""

# Idempotent on (candidate_id, episode_key): episode_key is the premise
# fingerprint plus the instant its condition resolved, so a recurrence (a new
# episode) can amend again but a repeated reconcile of the same resolution
# cannot.  Only candidates delivered at or before the resolution are amended.
CREATE_ENQUEUE_FN = """
CREATE OR REPLACE FUNCTION public.enqueue_premise_amendments(
    p_source text,
    p_fingerprint text,
    p_resolved_at timestamptz,
    p_summary text
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
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


def _set_check(table: str, constraint: str, column: str, values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{v}'" for v in values)
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK ({column} IN ({allowed}))")


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.insight_candidates
            ADD COLUMN IF NOT EXISTS premise JSONB,
            ADD COLUMN IF NOT EXISTS delivery_ref JSONB
        """
    )
    _set_check(
        "public.insight_candidates", "chk_insight_candidates_status", "status", _NEW_STATUSES
    )
    _set_check(
        "public.attention_ledger", "chk_attention_ledger_outcome", "outcome", _NEW_LEDGER_OUTCOMES
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.insight_amendments (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            candidate_id UUID NOT NULL
                REFERENCES public.insight_candidates(id) ON DELETE CASCADE,
            episode_key  TEXT NOT NULL,
            reason       TEXT NOT NULL,
            summary      TEXT,
            state        TEXT NOT NULL DEFAULT 'pending'
                CONSTRAINT chk_insight_amendments_state
                CHECK (state IN ('pending', 'applied', 'fold', 'folded')),
            attempts     INTEGER NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at  TIMESTAMPTZ,
            UNIQUE (candidate_id, episode_key)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_insight_amendments_state
        ON public.insight_amendments (state, created_at)
        WHERE state IN ('pending', 'fold')
        """
    )
    # Switchboard-only, same fence as insight_feedback (core_241): init-db
    # re-grants public DML to every runtime role, so RLS is the durable boundary.
    op.execute("ALTER TABLE public.insight_amendments ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_AMENDMENTS_POLICY} ON public.insight_amendments")
    op.execute(
        f"""
        CREATE POLICY {_AMENDMENTS_POLICY} ON public.insight_amendments
            FOR ALL TO PUBLIC
            USING (current_user = '{_SWITCHBOARD_ROLE}')
            WITH CHECK (current_user = '{_SWITCHBOARD_ROLE}')
        """
    )

    _execute_best_effort(CREATE_BILL_STATUS_FN)
    _execute_best_effort(
        "GRANT EXECUTE ON FUNCTION public.resolve_finance_bill_status(uuid) TO PUBLIC"
    )
    _execute_best_effort(CREATE_ENQUEUE_FN)
    _execute_best_effort(
        "GRANT EXECUTE ON FUNCTION "
        "public.enqueue_premise_amendments(text, text, timestamptz, text) TO PUBLIC"
    )


def downgrade() -> None:
    _execute_best_effort(
        "DROP FUNCTION IF EXISTS public.enqueue_premise_amendments(text, text, timestamptz, text)"
    )
    _execute_best_effort("DROP FUNCTION IF EXISTS public.resolve_finance_bill_status(uuid)")
    op.execute("DROP TABLE IF EXISTS public.insight_amendments")

    # Fold rows the narrower vocabularies reject into their nearest prior value.
    op.execute(
        "UPDATE public.attention_ledger SET outcome = 'suppressed' WHERE outcome = 'withdrawn'"
    )
    op.execute("UPDATE public.attention_ledger SET outcome = 'delivered' WHERE outcome = 'amended'")
    op.execute(
        "UPDATE public.insight_candidates SET status = 'filtered' WHERE status = 'withdrawn'"
    )
    _set_check(
        "public.attention_ledger", "chk_attention_ledger_outcome", "outcome", _OLD_LEDGER_OUTCOMES
    )
    _set_check(
        "public.insight_candidates", "chk_insight_candidates_status", "status", _OLD_STATUSES
    )
    op.execute(
        """
        ALTER TABLE public.insight_candidates
            DROP COLUMN IF EXISTS delivery_ref,
            DROP COLUMN IF EXISTS premise
        """
    )
