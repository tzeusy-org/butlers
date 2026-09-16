"""Add owner feedback and category attribution to proactive insights.

Revision ID: core_241
Revises: core_240
"""

from __future__ import annotations

from alembic import op

revision = "core_241"
down_revision = "core_240"
branch_labels = None
depends_on = None

_OLD_LEDGER_OUTCOMES = ("delivered", "coalesced", "deferred", "suppressed", "failed")
_NEW_LEDGER_OUTCOMES = (*_OLD_LEDGER_OUTCOMES, "expired")


def _set_ledger_outcomes(outcomes: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{outcome}'" for outcome in outcomes)
    op.execute(
        "ALTER TABLE public.attention_ledger DROP CONSTRAINT IF EXISTS chk_attention_ledger_outcome"
    )
    op.execute(
        "ALTER TABLE public.attention_ledger "
        "ADD CONSTRAINT chk_attention_ledger_outcome "
        f"CHECK (outcome IN ({allowed}))"
    )


def _grant_switchboard() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'butler_switchboard_rw') THEN
                GRANT SELECT, INSERT ON public.insight_feedback TO butler_switchboard_rw;
                GRANT USAGE, SELECT ON SEQUENCE public.insight_feedback_id_seq
                    TO butler_switchboard_rw;
                GRANT SELECT, UPDATE ON public.insight_engagement TO butler_switchboard_rw;
            END IF;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object OR undefined_table THEN NULL;
        END $$
        """
    )


def upgrade() -> None:
    _set_ledger_outcomes(_NEW_LEDGER_OUTCOMES)
    op.execute(
        """
        ALTER TABLE public.insight_engagement
            ADD COLUMN IF NOT EXISTS category TEXT,
            ADD COLUMN IF NOT EXISTS origin_butler TEXT
        """
    )
    op.execute(
        """
        UPDATE public.insight_engagement AS engagement
        SET category = candidate.category,
            origin_butler = candidate.origin_butler
        FROM public.insight_candidates AS candidate
        WHERE candidate.id = engagement.insight_id
          AND (engagement.category IS NULL OR engagement.origin_butler IS NULL)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_insight_engagement_category_recent
        ON public.insight_engagement (category, delivered_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.insight_feedback (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            insight_id UUID NOT NULL REFERENCES public.insight_candidates(id) ON DELETE CASCADE,
            dedup_family TEXT NOT NULL CHECK (btrim(dedup_family) <> ''),
            category TEXT NOT NULL CHECK (btrim(category) <> ''),
            origin_butler TEXT NOT NULL CHECK (btrim(origin_butler) <> ''),
            verdict TEXT NOT NULL CHECK (verdict IN ('useful', 'not_now', 'never')),
            snooze_until TIMESTAMPTZ,
            actor TEXT NOT NULL CHECK (btrim(actor) <> ''),
            decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            evidence_ref TEXT,
            CHECK ((verdict = 'not_now') = (snooze_until IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_insight_feedback_family_decided
        ON public.insight_feedback (dedup_family, decided_at DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_insight_feedback_useful_category
        ON public.insight_feedback (category, decided_at DESC, id DESC)
        WHERE verdict = 'useful'
        """
    )
    _grant_switchboard()


def downgrade() -> None:
    # The legacy vocabulary had no expiry outcome. Preserve the row and its
    # blocked_by reason while folding only the typed outcome to its nearest
    # pre-core_241 representation.
    op.execute("UPDATE public.attention_ledger SET outcome='suppressed' WHERE outcome='expired'")
    _set_ledger_outcomes(_OLD_LEDGER_OUTCOMES)
    op.execute("DROP TABLE IF EXISTS public.insight_feedback")
    op.execute("DROP INDEX IF EXISTS public.idx_insight_engagement_category_recent")
    op.execute(
        """
        ALTER TABLE public.insight_engagement
            DROP COLUMN IF EXISTS origin_butler,
            DROP COLUMN IF EXISTS category
        """
    )
