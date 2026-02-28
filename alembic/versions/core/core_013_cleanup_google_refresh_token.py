"""core_013_cleanup_google_refresh_token

Revision ID: core_013
Revises: core_012
Create Date: 2026-02-28 00:00:00.000000

Remove GOOGLE_REFRESH_TOKEN from butler_secrets, completing the migration
to shared.contact_info started in core_008.

Background:
  core_008 seeded shared.contact_info with GOOGLE_REFRESH_TOKEN mapped to
  type=google_oauth_refresh on the owner contact.  core_009 cleaned up the
  other migrated butler_secrets keys (BUTLER_TELEGRAM_CHAT_ID, USER_EMAIL_*,
  TELEGRAM_API_*) but intentionally deferred GOOGLE_REFRESH_TOKEN removal
  to a later migration while runtime consumers were updated.

  The butlers-e6ts epic (Phases 1–3) has now updated all runtime consumers
  (google_credentials.py, calendar.py, contacts module) to read the refresh
  token exclusively from shared.contact_info.  This migration removes the
  now-stale butler_secrets row.

Downgrade:
  Cannot restore a deleted secret value — it now lives in shared.contact_info.
  A manual re-seed from contact_info → butler_secrets would be required if
  rolling back past this point, but that scenario is not expected.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_013"
down_revision = "core_012"
branch_labels = None
depends_on = None

_REMOVED_KEYS = ["GOOGLE_REFRESH_TOKEN"]


def upgrade() -> None:
    keys_sql = ", ".join(f"'{key}'" for key in _REMOVED_KEYS)
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('butler_secrets') IS NOT NULL THEN
                DELETE FROM butler_secrets
                WHERE secret_key IN ({keys_sql});
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Cannot restore deleted secret values — they now live in shared.contact_info.
    # A manual re-seed from contact_info → butler_secrets would be needed if
    # reverting past this point, but that scenario is not expected.
    pass
