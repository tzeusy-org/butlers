"""Create the shared expected-signals absence ledger.

Revision ID: core_210
Revises: core_209
Create Date: 2026-09-03 00:00:00.000000

The core chain runs once per target schema while this table is database-global.
An advisory transaction lock serializes first creation and later idempotent
verification across those concurrent schema runs.
"""

from __future__ import annotations

from alembic import op

revision = "core_210"
down_revision = "core_209"
branch_labels = None
depends_on = None

_ALL_BUTLER_ROLES = (
    "butler_chronicler_rw",
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
)


def _grant_best_effort(role: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.expected_signals') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE ON TABLE public.expected_signals '
                        'TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_210:expected_signals', 0))"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.expected_signals (
            signal_key                 TEXT PRIMARY KEY,
            producer                   TEXT NOT NULL,
            producer_role              TEXT NOT NULL DEFAULT current_user,
            expected_cadence_seconds   BIGINT NOT NULL
                                       CHECK (expected_cadence_seconds > 0),
            last_observed_at            TIMESTAMPTZ,
            measurability               TEXT NOT NULL
                                       CHECK (measurability IN (
                                           'present', 'absent', 'unmeasurable'
                                       )),
            unmeasurable_reason         TEXT,
            evaluated_at                TIMESTAMPTZ NOT NULL,
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (
                (measurability = 'unmeasurable' AND unmeasurable_reason IS NOT NULL)
                OR (measurability <> 'unmeasurable' AND unmeasurable_reason IS NULL)
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_expected_signals_measurability_updated
            ON public.expected_signals (measurability, updated_at DESC)
        """
    )
    op.execute("ALTER TABLE public.expected_signals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.expected_signals FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS expected_signals_read ON public.expected_signals")
    op.execute(
        "CREATE POLICY expected_signals_read ON public.expected_signals FOR SELECT USING (true)"
    )
    op.execute("DROP POLICY IF EXISTS expected_signals_insert_own ON public.expected_signals")
    op.execute(
        "CREATE POLICY expected_signals_insert_own ON public.expected_signals "
        "FOR INSERT WITH CHECK (producer_role = current_user)"
    )
    op.execute("DROP POLICY IF EXISTS expected_signals_update_own ON public.expected_signals")
    op.execute(
        "CREATE POLICY expected_signals_update_own ON public.expected_signals "
        "FOR UPDATE USING (producer_role = current_user) "
        "WITH CHECK (producer_role = current_user)"
    )
    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort(role)


def downgrade() -> None:
    op.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_210:expected_signals', 0))"
    )
    op.execute("DROP TABLE IF EXISTS public.expected_signals")
