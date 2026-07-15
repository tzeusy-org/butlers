"""model_dispatch_attempts: index (outcome, ts DESC) for fleet outcome-mode queries.

bu-ij9xl. PR #3161 added fleet-wide outcome-mode queries to
``GET /api/dispatch/attempts`` (``model_settings.py`` list_dispatch_attempts,
the ``outcome is not None`` branch):

    SELECT ... FROM public.model_dispatch_attempts
    WHERE outcome = $1 [AND left(failure_reason, ...) = $n] [AND ts >= $n]
    ORDER BY ts <ASC|DESC> LIMIT $n

plus the paired server-side count:

    SELECT count(*) FROM public.model_dispatch_attempts
    WHERE outcome = $1 [AND ...]

This mode powers /spend's fleet-halt state and is hit ~3x per Spend/Overview
mount via useFleetHaltStatus. core_104 only indexed ``(catalog_entry_id,
ts DESC)``, ``session_id``, and ``logical_session_id``, so the ``outcome``
predicate had no supporting index and sequential-scanned
``public.model_dispatch_attempts`` as the table grows.

This adds the composite ``(outcome, ts DESC)`` which serves both the filtered
``ORDER BY ts`` (equality on ``outcome`` + range/order on ``ts``, scannable in
either direction) and the ``COUNT(*) WHERE outcome = $1`` (an index range over
the leading column). The optional ``ts >= $n`` window is covered by the second
column; the rare ``reason_prefix`` refinement is left as a filter on top.

Verification (local, seeded 2000 rows across 3 outcomes then ANALYZE):
``EXPLAIN`` of ``WHERE outcome = 'runtime_failure' ORDER BY ts DESC LIMIT 50``
switches from ``Seq Scan on model_dispatch_attempts`` to
``Index Scan using idx_model_dispatch_attempts_outcome_ts`` (no Sort node), and
the ``COUNT(*) WHERE outcome = $1`` uses the same index. A
``SET enable_seqscan = off`` integration test
(``tests/migrations/test_model_dispatch_attempts_outcome_index.py``) pins that
the planner CAN serve the query from this index.

Additive, ``IF NOT EXISTS``; downgrade drops the index only.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_173"
down_revision = "core_172"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_model_dispatch_attempts_outcome_ts
        ON public.model_dispatch_attempts (outcome, ts DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_outcome_ts")
