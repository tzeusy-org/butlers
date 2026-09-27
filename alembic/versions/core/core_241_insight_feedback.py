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
_FEEDBACK_TABLE = "public.insight_feedback"
_FEEDBACK_SEQUENCE = "public.insight_feedback_id_seq"
_FEEDBACK_POLICY = "insight_feedback_switchboard"
_SWITCHBOARD_ROLE = "butler_switchboard_rw"
_NON_SWITCHBOARD_RUNTIME_ROLES = (
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
    "butler_travel_rw",
    "butler_calendar_rw",
    "connector_writer",
    "restore_drill_executor",
)


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


def _for_existing_role(role: str, statement: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE '{statement}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege OR undefined_object OR undefined_table THEN NULL;
        END $$
        """
    )


def _fence_feedback_table() -> None:
    """Keep feedback readable and writable only by Switchboard runtime code.

    ``scripts/init-db.sql`` deliberately grants public-table DML to every
    runtime role, including on later bootstrap replays.  RLS is therefore the
    durable authority boundary; the revokes make the initial ACL narrow, while
    the policy survives a later broad grant.
    """
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_FEEDBACK_TABLE} FROM PUBLIC")
    op.execute(f"REVOKE ALL PRIVILEGES ON SEQUENCE {_FEEDBACK_SEQUENCE} FROM PUBLIC")
    for role in _NON_SWITCHBOARD_RUNTIME_ROLES:
        _for_existing_role(role, f"REVOKE ALL PRIVILEGES ON TABLE {_FEEDBACK_TABLE} FROM {role}")
        _for_existing_role(
            role, f"REVOKE ALL PRIVILEGES ON SEQUENCE {_FEEDBACK_SEQUENCE} FROM {role}"
        )
    _for_existing_role(
        _SWITCHBOARD_ROLE, f"GRANT SELECT, INSERT ON TABLE {_FEEDBACK_TABLE} TO {_SWITCHBOARD_ROLE}"
    )
    _for_existing_role(
        _SWITCHBOARD_ROLE,
        f"GRANT USAGE, SELECT ON SEQUENCE {_FEEDBACK_SEQUENCE} TO {_SWITCHBOARD_ROLE}",
    )
    _for_existing_role(
        _SWITCHBOARD_ROLE,
        "GRANT SELECT, UPDATE ON public.insight_engagement TO butler_switchboard_rw",
    )

    # API-managed pools run as the separate trusted dashboard/migration owner,
    # not via SET ROLE, so FORCE would deny the server-side feedback route.
    # That owner is never a runtime role; every runtime/connector role remains
    # fenced by this policy even after init-db re-grants public DML.
    op.execute(f"ALTER TABLE {_FEEDBACK_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_FEEDBACK_POLICY} ON {_FEEDBACK_TABLE}")
    op.execute(
        f"""
        CREATE POLICY {_FEEDBACK_POLICY} ON {_FEEDBACK_TABLE}
            FOR ALL TO PUBLIC
            USING (current_user = '{_SWITCHBOARD_ROLE}')
            WITH CHECK (current_user = '{_SWITCHBOARD_ROLE}')
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
    _fence_feedback_table()


def downgrade() -> None:
    # The legacy vocabulary had no expiry outcome. Preserve the row and its
    # blocked_by reason while folding only the typed outcome to its nearest
    # pre-core_241 representation.
    op.execute("UPDATE public.attention_ledger SET outcome='suppressed' WHERE outcome='expired'")
    _set_ledger_outcomes(_OLD_LEDGER_OUTCOMES)
    op.execute(f"DROP TABLE IF EXISTS {_FEEDBACK_TABLE}")
    op.execute("DROP INDEX IF EXISTS public.idx_insight_engagement_category_recent")
    op.execute(
        """
        ALTER TABLE public.insight_engagement
            DROP COLUMN IF EXISTS origin_butler,
            DROP COLUMN IF EXISTS category
        """
    )
