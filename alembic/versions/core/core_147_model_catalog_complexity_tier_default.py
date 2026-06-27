"""model_catalog: fix stale complexity_tier column DEFAULT.

Revision ID: core_147
Revises: core_146
Create Date: 2026-06-28 00:00:00.000000

core_004 created ``public.model_catalog.complexity_tier`` with
``DEFAULT 'medium'`` and a CHECK accepting the legacy six tiers.  core_093
renamed the tier vocabulary (``medium`` -> ``workhorse``) and replaced the CHECK
constraint with the canonical six
(``reasoning|workhorse|cheap|specialty|local|legacy``) but did NOT update the
column DEFAULT.  As a result the default stayed ``'medium'`` -- a value the
post-core_093 CHECK no longer accepts.  Any INSERT that omits
``complexity_tier`` falls back to ``'medium'`` and fails the CHECK.

Impact is low today because every application write supplies ``complexity_tier``
explicitly, but the bare-INSERT path is a latent bug.  This migration aligns the
column DEFAULT with the canonical tier ``medium`` mapped to: ``'workhorse'``
(see ``_DEPRECATED_TIER_MAP`` in ``src/butlers/core/model_routing.py``).

This is an ALTER DEFAULT only -- no data rows are touched.
"""

from __future__ import annotations

from alembic import op

revision = "core_147"
down_revision = "core_146"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.model_catalog ALTER COLUMN complexity_tier SET DEFAULT 'workhorse'"
    )


def downgrade() -> None:
    # Restore the pre-core_147 (core_004) default of 'medium'.  Note that under
    # the post-core_093 CHECK this default is itself invalid for bare INSERTs;
    # the downgrade only restores prior state, it does not re-introduce the bug
    # on a fresh DB.
    op.execute("ALTER TABLE public.model_catalog ALTER COLUMN complexity_tier SET DEFAULT 'medium'")
