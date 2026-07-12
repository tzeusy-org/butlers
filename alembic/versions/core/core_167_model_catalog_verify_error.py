"""model_catalog: add last_verified_error for stored verification error text.

Revision ID: core_167
Revises: core_166
Create Date: 2026-07-12 00:00:00.000000

bu-hmdqz.2 (2026-07-12 JARVIS pursuit, move 2 "close the model-selection
loop") — ``POST /api/settings/models/verify-all`` already captures a failed
verification's exception via ``except Exception as exc`` but only logs it
(``logger.warning``); ``last_verified_ok`` flips to ``false`` with no trace
of *why*. The settings-secrets auditor's evidence for this move was exactly
that gap: "failure reasons log-only". This migration adds one nullable TEXT
column so the next verify-all write (manual or the new hourly sweep,
``butlers.jobs.model_verify``) can persist the error text for the Models tab
to surface next to the stale/fresh verification badge.
"""

from __future__ import annotations

from alembic import op

revision = "core_167"
down_revision = "core_166"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.model_catalog
        ADD COLUMN IF NOT EXISTS last_verified_error TEXT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.model_catalog
        DROP COLUMN IF EXISTS last_verified_error
    """)
