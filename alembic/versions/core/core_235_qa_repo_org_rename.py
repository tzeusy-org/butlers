"""Retarget QA repository configuration to the organization-owned repository.

Revision ID: core_235
Revises: core_234
Create Date: 2026-09-14 00:00:00.000000

The QA repository configuration and allowed-repository whitelist predate the
repository's move to ``tzeusy-org/butlers``. This migration changes only
recognised variants of the retired target, preserves an already-configured
organization row (including its enabled state), and leaves unrelated operator
configuration untouched.

The data retarget is intentionally non-reversible. After upgrade, a value may
be an explicit operator choice rather than a migration write, so a downgrade
must not guess its provenance and overwrite it.
"""

from __future__ import annotations

from alembic import op

revision = "core_235"
down_revision = "core_234"
branch_labels = None
depends_on = None


RETARGET_QA_REPOSITORY_SQL = """
DO $$
DECLARE
    current_repo_url_default TEXT;
    selected_allowed_repo_id UUID;
BEGIN
    IF to_regclass('public.qa_repo_config') IS NOT NULL THEN
        SELECT pg_get_expr(default_value.adbin, default_value.adrelid)
        INTO current_repo_url_default
        FROM pg_attrdef AS default_value
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = default_value.adrelid
         AND attribute.attnum = default_value.adnum
        WHERE default_value.adrelid = 'public.qa_repo_config'::regclass
          AND attribute.attname = 'repo_url';

        -- Only replace the known historical default. An operator-supplied
        -- default is configuration, not evidence that this migration owns it.
        IF current_repo_url_default = '''https://github.com/Tzeusy/butlers''::text' THEN
            ALTER TABLE public.qa_repo_config
            ALTER COLUMN repo_url SET DEFAULT 'https://github.com/tzeusy-org/butlers';
        END IF;

        -- The settings endpoint accepts HTTPS and SSH remotes. Canonicalise
        -- every recognised spelling of the retired target, but leave any
        -- different repository alone.
        UPDATE public.qa_repo_config
        SET repo_url = 'https://github.com/tzeusy-org/butlers', updated_at = now()
        WHERE lower(btrim(repo_url)) ~
            '^(https?://github\\.com/tzeusy/butlers(?:\\.git)?/?|git@github\\.com:tzeusy/butlers(?:\\.git)?)$'
          AND repo_url <> 'https://github.com/tzeusy-org/butlers';
    END IF;

    IF to_regclass('public.qa_allowed_repositories') IS NOT NULL THEN
        -- Prefer a pre-existing organisation row over a retired-owner row so
        -- its enabled state remains the operator's source of truth. If no
        -- organisation row exists, promote the oldest retired row and retain
        -- its id and enabled state. Delete only redundant spellings of this
        -- one target before normalising the selected row, avoiding the
        -- case-sensitive UNIQUE(owner, repo) collision.
        SELECT id
        INTO selected_allowed_repo_id
        FROM public.qa_allowed_repositories
        WHERE lower(owner) IN ('tzeusy', 'tzeusy-org')
          AND lower(repo) = 'butlers'
        ORDER BY
            CASE WHEN lower(owner) = 'tzeusy-org' THEN 0 ELSE 1 END,
            created_at ASC,
            id ASC
        LIMIT 1;

        IF selected_allowed_repo_id IS NOT NULL THEN
            DELETE FROM public.qa_allowed_repositories
            WHERE lower(owner) IN ('tzeusy', 'tzeusy-org')
              AND lower(repo) = 'butlers'
              AND id <> selected_allowed_repo_id;

            UPDATE public.qa_allowed_repositories
            SET owner = 'tzeusy-org', repo = 'butlers', updated_at = now()
            WHERE id = selected_allowed_repo_id
              AND (owner <> 'tzeusy-org' OR repo <> 'butlers');
        END IF;
    END IF;
END
$$;
"""


def upgrade() -> None:
    """Retarget only the known legacy QA repository configuration."""
    op.execute(RETARGET_QA_REPOSITORY_SQL)


def downgrade() -> None:
    """Preserve post-upgrade operator choices rather than guessing provenance."""
