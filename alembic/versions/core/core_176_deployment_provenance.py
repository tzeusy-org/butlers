"""Record the source and serving provenance of deployment-ledger rows.

Revision ID: core_176
Revises: core_175
Create Date: 2026-07-19 00:00:00.000000

``public.deployments`` predates this migration, so provenance stays nullable:
historical rows cannot honestly be backfilled as either a CLI deploy or a
process boot, nor can their serving mode be inferred. New writers always pass
the constrained values enforced below.
"""

from __future__ import annotations

from alembic import op

revision = "core_176"
down_revision = "core_175"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Core migrations are applied once per butler schema but this table lives
    # in public, so each operation must be safely repeatable.
    op.execute(
        """
        ALTER TABLE public.deployments
            ADD COLUMN IF NOT EXISTS source TEXT,
            ADD COLUMN IF NOT EXISTS serving_mode TEXT,
            ADD COLUMN IF NOT EXISTS serving_worktree TEXT
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'public.deployments'::regclass
                  AND conname = 'chk_deployments_source'
            ) THEN
                ALTER TABLE public.deployments
                    ADD CONSTRAINT chk_deployments_source
                    CHECK (source IS NULL OR source IN ('boot', 'deploy'));
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'public.deployments'::regclass
                  AND conname = 'chk_deployments_serving_mode'
            ) THEN
                ALTER TABLE public.deployments
                    ADD CONSTRAINT chk_deployments_serving_mode
                    CHECK (
                        serving_mode IS NULL
                        OR serving_mode IN ('image', 'hotreload-worktree')
                    );
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'public.deployments'::regclass
                  AND conname = 'chk_deployments_serving_worktree'
            ) THEN
                ALTER TABLE public.deployments
                    ADD CONSTRAINT chk_deployments_serving_worktree
                    CHECK (
                        (
                            serving_mode IS NOT DISTINCT FROM 'hotreload-worktree'
                            AND source IS NOT DISTINCT FROM 'boot'
                            AND serving_worktree IS NOT NULL
                            AND serving_worktree ~ '^\\.worktrees/[^/]+$'
                            AND serving_worktree NOT IN ('.worktrees/.', '.worktrees/..')
                        )
                        OR (
                            serving_mode IS DISTINCT FROM 'hotreload-worktree'
                            AND serving_worktree IS NULL
                        )
                    );
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'public.deployments'::regclass
                  AND conname = 'chk_deployments_deploy_image'
            ) THEN
                ALTER TABLE public.deployments
                    ADD CONSTRAINT chk_deployments_deploy_image
                    CHECK (
                        source IS DISTINCT FROM 'deploy'
                        OR (
                            serving_mode IS NOT DISTINCT FROM 'image'
                            AND serving_worktree IS NULL
                        )
                    );
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'public.deployments'::regclass
                  AND conname = 'chk_deployments_provenance_tuple'
            ) THEN
                ALTER TABLE public.deployments
                    ADD CONSTRAINT chk_deployments_provenance_tuple
                    CHECK (
                        (
                            source IS NULL
                            AND serving_mode IS NULL
                            AND serving_worktree IS NULL
                        )
                        OR (
                            source IS NOT NULL
                            AND (
                                (
                                    source IS NOT DISTINCT FROM 'deploy'
                                    AND serving_mode IS NOT DISTINCT FROM 'image'
                                    AND serving_worktree IS NULL
                                )
                                OR (
                                    source IS NOT DISTINCT FROM 'boot'
                                    AND (
                                        (
                                            serving_mode IS NULL
                                            AND serving_worktree IS NULL
                                        )
                                        OR (
                                            serving_mode IS NOT DISTINCT FROM 'image'
                                            AND serving_worktree IS NULL
                                        )
                                        OR (
                                            serving_mode IS NOT DISTINCT FROM 'hotreload-worktree'
                                            AND serving_worktree IS NOT NULL
                                            AND serving_worktree ~ '^\\.worktrees/[^/]+$'
                                            AND serving_worktree NOT IN (
                                                '.worktrees/.',
                                                '.worktrees/..'
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    );
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.deployments
            DROP CONSTRAINT IF EXISTS chk_deployments_provenance_tuple,
            DROP CONSTRAINT IF EXISTS chk_deployments_deploy_image,
            DROP CONSTRAINT IF EXISTS chk_deployments_serving_worktree,
            DROP CONSTRAINT IF EXISTS chk_deployments_serving_mode,
            DROP CONSTRAINT IF EXISTS chk_deployments_source,
            DROP COLUMN IF EXISTS serving_worktree,
            DROP COLUMN IF EXISTS serving_mode,
            DROP COLUMN IF EXISTS source
        """
    )
