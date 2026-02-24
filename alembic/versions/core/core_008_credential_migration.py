"""credential_migration: seed owner contact_info from butler_secrets

Revision ID: core_008
Revises: core_007
Create Date: 2026-02-25 00:00:00.000000

Migrates owner channel identifiers from butler_secrets to shared.contact_info
entries linked to the owner contact.

Mapped keys:
  BUTLER_TELEGRAM_CHAT_ID  → type=telegram,              secured=false
  USER_EMAIL_ADDRESS       → type=email,                  secured=false
  USER_EMAIL_PASSWORD      → type=email_password,         secured=true
  GOOGLE_REFRESH_TOKEN     → type=google_oauth_refresh,   secured=true
  TELEGRAM_API_HASH        → type=telegram_api_hash,      secured=true
  TELEGRAM_API_ID          → type=telegram_api_id,        secured=true
  TELEGRAM_USER_SESSION    → type=telegram_user_session,  secured=true
  USER_TELEGRAM_TOKEN      → type=telegram_bot_token,     secured=true

Design notes:
  - Each INSERT uses ON CONFLICT DO NOTHING (uq_shared_contact_info_type_value)
    so the migration is safe to re-run.
  - Only rows where the butler_secrets value is non-NULL and non-empty are
    migrated; missing secrets are silently skipped.
  - shared.contacts, shared.contact_info, and butler_secrets may all not yet
    exist on fresh installs — every step is wrapped in existence guards.
  - downgrade() removes only the rows inserted by this migration (matched by
    type); it does not touch user-created contact_info entries.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_008"
down_revision = "core_007"
branch_labels = None
depends_on = None

# Mapping: (butler_secrets key, contact_info type, secured flag)
_SECRET_TO_CONTACT_INFO: list[tuple[str, str, bool]] = [
    ("BUTLER_TELEGRAM_CHAT_ID", "telegram", False),
    ("USER_EMAIL_ADDRESS", "email", False),
    ("USER_EMAIL_PASSWORD", "email_password", True),
    ("GOOGLE_REFRESH_TOKEN", "google_oauth_refresh", True),
    ("TELEGRAM_API_HASH", "telegram_api_hash", True),
    ("TELEGRAM_API_ID", "telegram_api_id", True),
    ("TELEGRAM_USER_SESSION", "telegram_user_session", True),
    ("USER_TELEGRAM_TOKEN", "telegram_bot_token", True),
]

# contact_info types inserted by this migration (used by downgrade).
_MIGRATED_TYPES = [ci_type for _key, ci_type, _secured in _SECRET_TO_CONTACT_INFO]


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Guard: skip entirely if required tables don't exist yet (fresh installs
    # where the contacts module and/or relationship chain haven't run).
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        DECLARE
            v_owner_id UUID;
            v_value TEXT;
        BEGIN
            -- Abort early if shared.contacts or shared.contact_info are missing.
            IF to_regclass('shared.contacts') IS NULL
               OR to_regclass('shared.contact_info') IS NULL
            THEN
                RETURN;
            END IF;

            -- Resolve the owner contact ID (singleton row with 'owner' = ANY(roles)).
            SELECT id INTO v_owner_id
            FROM shared.contacts
            WHERE 'owner' = ANY(roles)
            LIMIT 1;

            IF v_owner_id IS NULL THEN
                -- Owner contact not yet bootstrapped; skip.
                RETURN;
            END IF;

            -- ---------------------------------------------------------------
            -- BUTLER_TELEGRAM_CHAT_ID -> type=telegram (not secured)
            -- ---------------------------------------------------------------
            IF to_regclass('butler_secrets') IS NOT NULL THEN
                SELECT secret_value INTO v_value
                FROM butler_secrets
                WHERE secret_key = 'BUTLER_TELEGRAM_CHAT_ID'
                  AND secret_value IS NOT NULL
                  AND secret_value <> '';

                IF v_value IS NOT NULL THEN
                    INSERT INTO shared.contact_info
                        (contact_id, type, value, secured, is_primary)
                    VALUES (v_owner_id, 'telegram', v_value, false, true)
                    ON CONFLICT DO NOTHING;
                END IF;

                -- ---------------------------------------------------------------
                -- USER_EMAIL_ADDRESS -> type=email (not secured)
                -- ---------------------------------------------------------------
                SELECT secret_value INTO v_value
                FROM butler_secrets
                WHERE secret_key = 'USER_EMAIL_ADDRESS'
                  AND secret_value IS NOT NULL
                  AND secret_value <> '';

                IF v_value IS NOT NULL THEN
                    INSERT INTO shared.contact_info
                        (contact_id, type, value, secured, is_primary)
                    VALUES (v_owner_id, 'email', v_value, false, true)
                    ON CONFLICT DO NOTHING;
                END IF;

                -- ---------------------------------------------------------------
                -- USER_EMAIL_PASSWORD -> type=email_password (secured)
                -- ---------------------------------------------------------------
                SELECT secret_value INTO v_value
                FROM butler_secrets
                WHERE secret_key = 'USER_EMAIL_PASSWORD'
                  AND secret_value IS NOT NULL
                  AND secret_value <> '';

                IF v_value IS NOT NULL THEN
                    INSERT INTO shared.contact_info
                        (contact_id, type, value, secured, is_primary)
                    VALUES (v_owner_id, 'email_password', v_value, true, true)
                    ON CONFLICT DO NOTHING;
                END IF;

                -- ---------------------------------------------------------------
                -- GOOGLE_REFRESH_TOKEN -> type=google_oauth_refresh (secured)
                -- ---------------------------------------------------------------
                SELECT secret_value INTO v_value
                FROM butler_secrets
                WHERE secret_key = 'GOOGLE_REFRESH_TOKEN'
                  AND secret_value IS NOT NULL
                  AND secret_value <> '';

                IF v_value IS NOT NULL THEN
                    INSERT INTO shared.contact_info
                        (contact_id, type, value, secured, is_primary)
                    VALUES (v_owner_id, 'google_oauth_refresh', v_value, true, true)
                    ON CONFLICT DO NOTHING;
                END IF;

                -- ---------------------------------------------------------------
                -- TELEGRAM_API_HASH -> type=telegram_api_hash (secured)
                -- ---------------------------------------------------------------
                SELECT secret_value INTO v_value
                FROM butler_secrets
                WHERE secret_key = 'TELEGRAM_API_HASH'
                  AND secret_value IS NOT NULL
                  AND secret_value <> '';

                IF v_value IS NOT NULL THEN
                    INSERT INTO shared.contact_info
                        (contact_id, type, value, secured, is_primary)
                    VALUES (v_owner_id, 'telegram_api_hash', v_value, true, true)
                    ON CONFLICT DO NOTHING;
                END IF;

                -- ---------------------------------------------------------------
                -- TELEGRAM_API_ID -> type=telegram_api_id (secured)
                -- ---------------------------------------------------------------
                SELECT secret_value INTO v_value
                FROM butler_secrets
                WHERE secret_key = 'TELEGRAM_API_ID'
                  AND secret_value IS NOT NULL
                  AND secret_value <> '';

                IF v_value IS NOT NULL THEN
                    INSERT INTO shared.contact_info
                        (contact_id, type, value, secured, is_primary)
                    VALUES (v_owner_id, 'telegram_api_id', v_value, true, true)
                    ON CONFLICT DO NOTHING;
                END IF;

                -- ---------------------------------------------------------------
                -- TELEGRAM_USER_SESSION -> type=telegram_user_session (secured)
                -- ---------------------------------------------------------------
                SELECT secret_value INTO v_value
                FROM butler_secrets
                WHERE secret_key = 'TELEGRAM_USER_SESSION'
                  AND secret_value IS NOT NULL
                  AND secret_value <> '';

                IF v_value IS NOT NULL THEN
                    INSERT INTO shared.contact_info
                        (contact_id, type, value, secured, is_primary)
                    VALUES (v_owner_id, 'telegram_user_session', v_value, true, true)
                    ON CONFLICT DO NOTHING;
                END IF;

                -- ---------------------------------------------------------------
                -- USER_TELEGRAM_TOKEN -> type=telegram_bot_token (secured)
                -- ---------------------------------------------------------------
                SELECT secret_value INTO v_value
                FROM butler_secrets
                WHERE secret_key = 'USER_TELEGRAM_TOKEN'
                  AND secret_value IS NOT NULL
                  AND secret_value <> '';

                IF v_value IS NOT NULL THEN
                    INSERT INTO shared.contact_info
                        (contact_id, type, value, secured, is_primary)
                    VALUES (v_owner_id, 'telegram_bot_token', v_value, true, true)
                    ON CONFLICT DO NOTHING;
                END IF;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Remove contact_info rows for migrated types on the owner contact.
    # Only removes rows where the type matches one of the migrated types.
    # Does not touch user-created entries with the same type (if any).
    op.execute("""
        DO $$
        DECLARE
            v_owner_id UUID;
        BEGIN
            IF to_regclass('shared.contacts') IS NULL
               OR to_regclass('shared.contact_info') IS NULL
            THEN
                RETURN;
            END IF;

            SELECT id INTO v_owner_id
            FROM shared.contacts
            WHERE 'owner' = ANY(roles)
            LIMIT 1;

            IF v_owner_id IS NULL THEN
                RETURN;
            END IF;

            DELETE FROM shared.contact_info
            WHERE contact_id = v_owner_id
              AND type IN (
                  'telegram',
                  'email',
                  'email_password',
                  'google_oauth_refresh',
                  'telegram_api_hash',
                  'telegram_api_id',
                  'telegram_user_session',
                  'telegram_bot_token'
              );
        END
        $$;
    """)
