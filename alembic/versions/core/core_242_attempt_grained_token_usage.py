"""Make token usage evidence attempt-grained and uncertainty-aware.

Revision ID: core_242
Revises: core_241
Create Date: 2026-09-21 00:00:00.000000

Historical rows remain measured evidence but cannot be linked retroactively to
one dispatch attempt. New runtime writers attach the corresponding
``model_dispatch_attempts.id`` and use an explicit unmeasurable row when an
invoked attempt yields no parseable usage.
"""

from __future__ import annotations

from alembic import op

revision = "core_242"
down_revision = "core_241"
branch_labels = None
depends_on = None

_USAGE_SOURCE_CONSTRAINT = "ck_token_usage_ledger_usage_source"
_USAGE_EVIDENCE_CONSTRAINT = "ck_token_usage_ledger_usage_evidence"


def _add_constraint(name: str, body: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conrelid = 'public.token_usage_ledger'::regclass
                   AND conname = '{name}'
            ) THEN
                ALTER TABLE public.token_usage_ledger
                    ADD CONSTRAINT {name} CHECK ({body}) NOT VALID;
            END IF;
        END
        $$
        """
    )
    op.execute(f"ALTER TABLE public.token_usage_ledger VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.token_usage_ledger
            ADD COLUMN IF NOT EXISTS attempt_id BIGINT
                REFERENCES public.model_dispatch_attempts(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS usage_source TEXT NOT NULL DEFAULT 'measured',
            ALTER COLUMN input_tokens DROP NOT NULL,
            ALTER COLUMN output_tokens DROP NOT NULL,
            ALTER COLUMN cached_input_tokens DROP NOT NULL,
            ALTER COLUMN cache_creation_tokens DROP NOT NULL
        """
    )
    _add_constraint(
        _USAGE_SOURCE_CONSTRAINT,
        "usage_source IN ('measured', 'unmeasurable')",
    )
    _add_constraint(
        _USAGE_EVIDENCE_CONSTRAINT,
        "(usage_source = 'measured' "
        "AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
        "AND cached_input_tokens IS NOT NULL AND cache_creation_tokens IS NOT NULL) "
        "OR (usage_source = 'unmeasurable' "
        "AND input_tokens IS NULL AND output_tokens IS NULL "
        "AND cached_input_tokens IS NULL AND cache_creation_tokens IS NULL)",
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_token_usage_ledger_attempt
        ON public.token_usage_ledger (attempt_id, recorded_at)
        WHERE attempt_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            has_unmeasurable BOOLEAN;
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'token_usage_ledger'
                   AND column_name = 'usage_source'
            ) THEN
                EXECUTE
                    'SELECT EXISTS (SELECT 1 FROM public.token_usage_ledger '
                    'WHERE usage_source = ''unmeasurable'')'
                    INTO has_unmeasurable;
                IF has_unmeasurable THEN
                    RAISE EXCEPTION
                        'cannot downgrade core_242 while unmeasurable usage evidence exists';
                END IF;
            END IF;
        END
        $$
        """
    )
    op.execute("DROP INDEX IF EXISTS public.idx_token_usage_ledger_attempt")
    op.execute(
        f"ALTER TABLE public.token_usage_ledger "
        f"DROP CONSTRAINT IF EXISTS {_USAGE_EVIDENCE_CONSTRAINT}"
    )
    op.execute(
        f"ALTER TABLE public.token_usage_ledger "
        f"DROP CONSTRAINT IF EXISTS {_USAGE_SOURCE_CONSTRAINT}"
    )
    op.execute(
        """
        ALTER TABLE public.token_usage_ledger
            ALTER COLUMN input_tokens SET NOT NULL,
            ALTER COLUMN output_tokens SET NOT NULL,
            ALTER COLUMN cached_input_tokens SET NOT NULL,
            ALTER COLUMN cache_creation_tokens SET NOT NULL,
            DROP COLUMN IF EXISTS usage_source,
            DROP COLUMN IF EXISTS attempt_id
        """
    )
