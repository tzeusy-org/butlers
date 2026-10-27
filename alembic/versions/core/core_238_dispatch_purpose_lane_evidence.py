"""Persist the closed purpose lane on dispatch and token-usage evidence.

Revision ID: core_238
Revises: core_237
Create Date: 2026-09-15 00:00:00.000000

Legacy rows remain NULL. New writers stamp ``standard`` or ``private_content``
without changing the existing open-ended token-usage ``purpose`` dimension.
When a requested target crosses the protected core_198 boundary, downgrade
preflights that boundary before changing this revision's schema.
"""

from __future__ import annotations

from alembic import context, op
from butlers.migration_preflight import preflight_runtime_attention_downgrade

revision = "core_238"
down_revision = "core_237"
branch_labels = None
depends_on = None

_DISPATCH_CONSTRAINT = "ck_model_dispatch_attempts_purpose_lane"
_TOKEN_CONSTRAINT = "ck_token_usage_ledger_purpose_lane"


def _add_closed_lane(table: str, constraint: str) -> None:
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS purpose_lane TEXT")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conrelid = '{table}'::regclass
                   AND conname = '{constraint}'
            ) THEN
                ALTER TABLE {table} ADD CONSTRAINT {constraint}
                CHECK (purpose_lane IS NULL OR purpose_lane IN ('standard', 'private_content'))
                NOT VALID;
            END IF;
        END
        $$
        """
    )
    op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint}")


def upgrade() -> None:
    _add_closed_lane("public.model_dispatch_attempts", _DISPATCH_CONSTRAINT)
    _add_closed_lane("public.token_usage_ledger", _TOKEN_CONSTRAINT)


def downgrade() -> None:
    preflight_runtime_attention_downgrade(op, context)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.model_dispatch_attempts WHERE purpose_lane IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM public.token_usage_ledger WHERE purpose_lane IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'cannot downgrade core_238 while purpose-lane evidence exists';
            END IF;
        END
        $$
        """
    )
    op.execute(
        f"ALTER TABLE public.token_usage_ledger DROP CONSTRAINT IF EXISTS {_TOKEN_CONSTRAINT}"
    )
    op.execute("ALTER TABLE public.token_usage_ledger DROP COLUMN IF EXISTS purpose_lane")
    op.execute(
        f"ALTER TABLE public.model_dispatch_attempts "
        f"DROP CONSTRAINT IF EXISTS {_DISPATCH_CONSTRAINT}"
    )
    op.execute("ALTER TABLE public.model_dispatch_attempts DROP COLUMN IF EXISTS purpose_lane")
