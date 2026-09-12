"""qa_repo_org_rename: repoint QA staffer config at github.com/tzeusy-org/butlers.

Revision ID: core_215
Revises: core_214
Create Date: 2026-09-05 00:00:00.000000

The repository moved from the personal account ``github.com/tzeusy/butlers``
to the org ``github.com/tzeusy-org/butlers``.  Two ``public`` config rows
seeded before the move still point at the old owner, so the QA staffer's
managed clone (``src/butlers/core/qa/repo_clone.py``) fetches/pushes against
the stale URL and every self-healing PR push 403s (bu-vr6sz):

  git push failed: remote: Permission to tzeusy/butlers.git denied to Tzeusy.
  fatal: unable to access 'https://github.com/tzeusy/butlers.git/': The
  requested URL returned error: 403

This migration:
  1. Repoints the ``qa_repo_config.repo_url`` column DEFAULT so any future
     fresh install seeds the correct URL (mirrors the code-side default in
     ``repo_clone.py``'s ``_DEFAULT_REPO_URL``).
  2. Corrects any existing row still on the old URL (idempotent no-op once
     applied or on installs that already point at tzeusy-org).
  3. Corrects the ``qa_allowed_repositories`` whitelist row's owner from
     ``tzeusy`` to ``tzeusy-org`` for the ``butlers`` repo, if present.
"""

from __future__ import annotations

from alembic import op

revision = "core_215"
down_revision = "core_214"
branch_labels = None
depends_on = None

_OLD_REPO_URL = "https://github.com/Tzeusy/butlers"
_NEW_REPO_URL = "https://github.com/tzeusy-org/butlers"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE public.qa_repo_config
        ALTER COLUMN repo_url SET DEFAULT '{_NEW_REPO_URL}'
    """)

    op.execute(f"""
        UPDATE public.qa_repo_config
        SET repo_url = '{_NEW_REPO_URL}', updated_at = now()
        WHERE lower(repo_url) = lower('{_OLD_REPO_URL}')
    """)

    op.execute("""
        UPDATE public.qa_allowed_repositories
        SET owner = 'tzeusy-org', updated_at = now()
        WHERE lower(owner) = 'tzeusy' AND lower(repo) = 'butlers'
    """)


def downgrade() -> None:
    op.execute(f"""
        ALTER TABLE public.qa_repo_config
        ALTER COLUMN repo_url SET DEFAULT '{_OLD_REPO_URL}'
    """)

    op.execute(f"""
        UPDATE public.qa_repo_config
        SET repo_url = '{_OLD_REPO_URL}', updated_at = now()
        WHERE lower(repo_url) = lower('{_NEW_REPO_URL}')
    """)

    op.execute("""
        UPDATE public.qa_allowed_repositories
        SET owner = 'tzeusy', updated_at = now()
        WHERE lower(owner) = 'tzeusy-org' AND lower(repo) = 'butlers'
    """)
