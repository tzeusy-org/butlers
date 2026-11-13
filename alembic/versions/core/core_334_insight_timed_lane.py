"""Timed insight lane and a shared delivery claim (bu-jv1moa.8 PR-A).

Revision ID: core_334
Revises: core_331
Create Date: 2026-11-14 00:00:00.000000

``public.insight_candidates`` gains:

- ``lane`` (``digest`` default, or ``timed``): a timed candidate is delivered
  between ``deliver_after`` and ``expires_at`` or not at all (RFC 0011). The
  digest and urgent cycles never select a timed candidate.
- ``deliver_after``: required exactly for the timed lane, and before
  ``expires_at``.
- ``claim_id`` / ``claimed_at``: one claim every delivery worker (digest, urgent
  and timed) takes before it sends, so one candidate cannot be sent by two
  overlapping cycles. The final disposition is written only by the claim holder.
- ``attempt_started_at``: committed after the claim and immediately before the
  transport call. A stale timed claim with it set may have been sent and is
  never resent (it expires as ambiguous); one without it never reached
  transport and takes the ordinary failure path.
- a partial unique index on ``dedup_key`` for the timed lane: one timed
  candidate per canonical edge.

No new table, role, grant or policy. The core chain runs once per butler schema
over this shared table, so every statement is idempotent under an advisory lock,
and only the last schema still at core_334 drops the columns again. The
downgrade refuses while a timed candidate exists.
"""

from __future__ import annotations

from alembic import op

revision = "core_334"
down_revision = "core_331"
branch_labels = None
depends_on = None

_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_334:insight-lane', 0))"
_LANE_CHECK = "chk_insight_candidates_lane"
_WINDOW_CHECK = "chk_insight_candidates_timed_window"
_TIMED_INDEX = "uq_insight_candidates_timed_dedup"
_DUE_INDEX = "idx_insight_candidates_timed_due"
_ROLLBACK_REFUSAL = "core_334 downgrade refused: timed insight candidates exist"


def _installation_count() -> int:
    """Count schema-local core chains that still depend on the added columns."""
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
                    THEN substring(version_num FROM 6)::integer >= 334
                    ELSE false END
            )
            """
        ).scalar()
        count += int(bool(installed))
    return count


def upgrade() -> None:
    op.execute(_LOCK)
    op.execute(
        """
        ALTER TABLE public.insight_candidates
            ADD COLUMN IF NOT EXISTS lane TEXT NOT NULL DEFAULT 'digest',
            ADD COLUMN IF NOT EXISTS deliver_after TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS claim_id UUID,
            ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS attempt_started_at TIMESTAMPTZ
        """
    )
    op.execute(f"ALTER TABLE public.insight_candidates DROP CONSTRAINT IF EXISTS {_LANE_CHECK}")
    op.execute(
        f"""
        ALTER TABLE public.insight_candidates ADD CONSTRAINT {_LANE_CHECK}
            CHECK (lane IN ('digest', 'timed'))
        """
    )
    op.execute(f"ALTER TABLE public.insight_candidates DROP CONSTRAINT IF EXISTS {_WINDOW_CHECK}")
    op.execute(
        f"""
        ALTER TABLE public.insight_candidates ADD CONSTRAINT {_WINDOW_CHECK}
            CHECK (
                (lane = 'timed') = (deliver_after IS NOT NULL)
                AND (deliver_after IS NULL OR deliver_after < expires_at)
            )
        """
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_TIMED_INDEX}
        ON public.insight_candidates (dedup_key) WHERE lane = 'timed'
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {_DUE_INDEX}
        ON public.insight_candidates (deliver_after)
        WHERE lane = 'timed' AND status = 'pending'
        """
    )


def downgrade() -> None:
    op.execute(_LOCK)
    if _installation_count() > 1:
        return
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.insight_candidates WHERE lane = 'timed') THEN
                RAISE EXCEPTION '{_ROLLBACK_REFUSAL}';
            END IF;
        END
        $$
        """
    )
    op.execute(f"DROP INDEX IF EXISTS public.{_DUE_INDEX}")
    op.execute(f"DROP INDEX IF EXISTS public.{_TIMED_INDEX}")
    op.execute(f"ALTER TABLE public.insight_candidates DROP CONSTRAINT IF EXISTS {_WINDOW_CHECK}")
    op.execute(f"ALTER TABLE public.insight_candidates DROP CONSTRAINT IF EXISTS {_LANE_CHECK}")
    op.execute(
        """
        ALTER TABLE public.insight_candidates
            DROP COLUMN IF EXISTS attempt_started_at,
            DROP COLUMN IF EXISTS claimed_at,
            DROP COLUMN IF EXISTS claim_id,
            DROP COLUMN IF EXISTS deliver_after,
            DROP COLUMN IF EXISTS lane
        """
    )
