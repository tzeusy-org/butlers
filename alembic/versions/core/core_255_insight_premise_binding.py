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
constraints, then drops the columns and definers. ``insight_amendments`` is kept
(durable evidence; see downgrade).
"""

from __future__ import annotations

from alembic import op

revision = "core_255"
down_revision = "core_254"
branch_labels = None
depends_on = None

_SWITCHBOARD_ROLE = "butler_switchboard_rw"
# Every runtime role that reconciles an owner-condition snapshot may enqueue.
_ENQUEUE_ROLES = (
    "butler_chronicler_rw",
    "butler_concierge_rw",
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
    "butler_calendar_rw",
)
_BILL_STATUS_SIG = "public.resolve_finance_bill_status(uuid)"
_ENQUEUE_SIG = "public.enqueue_premise_amendments(text, text, timestamptz, text)"
_AMENDMENTS_POLICY = "insight_amendments_switchboard"

_OLD_LEDGER_OUTCOMES = ("delivered", "coalesced", "deferred", "suppressed", "failed", "expired")
_NEW_LEDGER_OUTCOMES = (*_OLD_LEDGER_OUTCOMES, "withdrawn", "amended")
_OLD_STATUSES = ("pending", "delivered", "expired", "filtered")
_NEW_STATUSES = (*_OLD_STATUSES, "withdrawn")

# plpgsql (not sql) so the finance.bills reference resolves at call time. The
# search_path is exactly ``pg_catalog, pg_temp`` (the only form
# ``butlers.core.definer_search_path.is_pinned`` accepts) and every relation in
# the body is schema-qualified.
CREATE_BILL_STATUS_FN = """
CREATE OR REPLACE FUNCTION public.resolve_finance_bill_status(p_bill_id uuid)
RETURNS TABLE(status text)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
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


def _grant_execute_only_to(signature: str, roles: tuple[str, ...]) -> None:
    """REVOKE the default PUBLIC execute, then grant to each existing intended role."""
    _execute_best_effort(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for role in roles:
        op.execute(
            f"""
            DO $do$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    EXECUTE 'GRANT EXECUTE ON FUNCTION {signature} TO {role}';
                END IF;
            EXCEPTION
                WHEN insufficient_privilege OR undefined_object OR undefined_function THEN NULL;
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

    _execute_best_effort(
        f"GRANT SELECT, INSERT, UPDATE ON TABLE public.insight_amendments TO {_SWITCHBOARD_ROLE}"
    )
    _execute_best_effort(CREATE_BILL_STATUS_FN)
    _grant_execute_only_to(_BILL_STATUS_SIG, (_SWITCHBOARD_ROLE,))
    _execute_best_effort(CREATE_ENQUEUE_FN)
    _grant_execute_only_to(_ENQUEUE_SIG, _ENQUEUE_ROLES)


def downgrade() -> None:
    _execute_best_effort(
        "DROP FUNCTION IF EXISTS public.enqueue_premise_amendments(text, text, timestamptz, text)"
    )
    _execute_best_effort("DROP FUNCTION IF EXISTS public.resolve_finance_bill_status(uuid)")
    # public.insight_amendments is deliberately kept: queued amendments are
    # durable evidence of owner-visible corrections, and re-creating the table
    # on the next upgrade (possibly under a different owner) is not symmetric.
    # A later upgrade finds it through CREATE TABLE IF NOT EXISTS.

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
