"""cleanup_migrated_secrets: remove owner-identity secrets from butler_secrets

Revision ID: core_009
Revises: core_008
Create Date: 2026-02-26 00:00:00.000000

Removes the five owner-identity secret keys that were migrated to
shared.contact_info by core_008.  These keys are now resolved exclusively
from the owner contact's contact_info entries.

Removed keys:
  BUTLER_TELEGRAM_CHAT_ID   (migrated to contact_info type=telegram)
  TELEGRAM_CHAT_ID          (renamed alias of BUTLER_TELEGRAM_CHAT_ID)
  USER_EMAIL_ADDRESS        (migrated to contact_info type=email)
  USER_EMAIL_PASSWORD       (migrated to contact_info type=email_password)
  TELEGRAM_API_HASH         (migrated to contact_info type=telegram_api_hash)
  TELEGRAM_API_ID           (migrated to contact_info type=telegram_api_id)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_009"
down_revision = "core_008"
branch_labels = None
depends_on = None

_REMOVED_KEYS = [
    "BUTLER_TELEGRAM_CHAT_ID",
    "TELEGRAM_CHAT_ID",
    "USER_EMAIL_ADDRESS",
    "USER_EMAIL_PASSWORD",
    "TELEGRAM_API_HASH",
    "TELEGRAM_API_ID",
]


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('butler_secrets') IS NOT NULL THEN
                DELETE FROM butler_secrets
                WHERE secret_key IN (
                    'BUTLER_TELEGRAM_CHAT_ID',
                    'TELEGRAM_CHAT_ID',
                    'USER_EMAIL_ADDRESS',
                    'USER_EMAIL_PASSWORD',
                    'TELEGRAM_API_HASH',
                    'TELEGRAM_API_ID'
                );
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Cannot restore deleted secret values — they now live in shared.contact_info.
    # A manual re-seed from contact_info → butler_secrets would be needed if
    # reverting past this point, but that scenario is unlikely.
    pass
