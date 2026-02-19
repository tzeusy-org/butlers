"""Migrate google_oauth_credentials into butler_secrets and drop the old table.

Revision ID: core_009
Revises: core_008
Create Date: 2026-02-20 00:00:00.000000

The google_oauth_credentials singleton table stores all Google OAuth material
in a single JSONB blob under credential_key='google'.  This migration moves
those values into the butler_secrets key-per-row pattern:

  GOOGLE_OAUTH_CLIENT_ID     ← credentials.client_id
  GOOGLE_OAUTH_CLIENT_SECRET ← credentials.client_secret  (is_sensitive=true)
  GOOGLE_REFRESH_TOKEN       ← credentials.refresh_token  (is_sensitive=true)
  GOOGLE_OAUTH_SCOPES        ← credentials.scope          (is_sensitive=false)

The google_oauth_credentials table is dropped at the end of upgrade().
The downgrade() re-creates the table and restores the four rows back into a
single JSONB blob (best-effort — data already in butler_secrets is
re-assembled from the four keys).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_009"
down_revision = "core_008"
branch_labels = None
depends_on = None

_OLD_TABLE = "google_oauth_credentials"
_NEW_TABLE = "butler_secrets"
_SINGLETON_KEY = "google"

# Key names in butler_secrets
_KEY_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
_KEY_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"
_KEY_REFRESH_TOKEN = "GOOGLE_REFRESH_TOKEN"
_KEY_SCOPES = "GOOGLE_OAUTH_SCOPES"


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Migrate existing data from google_oauth_credentials → butler_secrets
    # ------------------------------------------------------------------ #
    # We use a single SQL block to keep the migration atomic.
    # The DO block reads the JSONB blob and inserts each field as a
    # separate row in butler_secrets (ON CONFLICT DO UPDATE so re-running
    # the migration is safe).
    #
    # If google_oauth_credentials is empty or does not exist, the DO block
    # is a no-op.
    op.execute(f"""
        DO $$
        DECLARE
            _creds  JSONB;
            _val    TEXT;
        BEGIN
            -- Guard: bail early if the old table no longer exists
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = '{_OLD_TABLE}'
            ) THEN
                RETURN;
            END IF;

            SELECT credentials
              INTO _creds
              FROM {_OLD_TABLE}
             WHERE credential_key = '{_SINGLETON_KEY}'
             LIMIT 1;

            -- Nothing stored yet → nothing to migrate
            IF _creds IS NULL THEN
                RETURN;
            END IF;

            -- GOOGLE_OAUTH_CLIENT_ID  (not sensitive)
            _val := _creds->>'client_id';
            IF _val IS NOT NULL AND _val <> '' THEN
                INSERT INTO {_NEW_TABLE}
                    (secret_key, secret_value, category, description, is_sensitive)
                VALUES (
                    '{_KEY_CLIENT_ID}',
                    _val,
                    'google',
                    'Google OAuth client ID',
                    false
                )
                ON CONFLICT (secret_key) DO UPDATE SET
                    secret_value = EXCLUDED.secret_value,
                    category     = EXCLUDED.category,
                    description  = EXCLUDED.description,
                    is_sensitive = EXCLUDED.is_sensitive,
                    updated_at   = now();
            END IF;

            -- GOOGLE_OAUTH_CLIENT_SECRET  (sensitive)
            _val := _creds->>'client_secret';
            IF _val IS NOT NULL AND _val <> '' THEN
                INSERT INTO {_NEW_TABLE}
                    (secret_key, secret_value, category, description, is_sensitive)
                VALUES (
                    '{_KEY_CLIENT_SECRET}',
                    _val,
                    'google',
                    'Google OAuth client secret',
                    true
                )
                ON CONFLICT (secret_key) DO UPDATE SET
                    secret_value = EXCLUDED.secret_value,
                    category     = EXCLUDED.category,
                    description  = EXCLUDED.description,
                    is_sensitive = EXCLUDED.is_sensitive,
                    updated_at   = now();
            END IF;

            -- GOOGLE_REFRESH_TOKEN  (sensitive)
            _val := _creds->>'refresh_token';
            IF _val IS NOT NULL AND _val <> '' THEN
                INSERT INTO {_NEW_TABLE}
                    (secret_key, secret_value, category, description, is_sensitive)
                VALUES (
                    '{_KEY_REFRESH_TOKEN}',
                    _val,
                    'google',
                    'Google OAuth refresh token',
                    true
                )
                ON CONFLICT (secret_key) DO UPDATE SET
                    secret_value = EXCLUDED.secret_value,
                    category     = EXCLUDED.category,
                    description  = EXCLUDED.description,
                    is_sensitive = EXCLUDED.is_sensitive,
                    updated_at   = now();
            END IF;

            -- GOOGLE_OAUTH_SCOPES  (not sensitive)
            _val := _creds->>'scope';
            IF _val IS NOT NULL AND _val <> '' THEN
                INSERT INTO {_NEW_TABLE}
                    (secret_key, secret_value, category, description, is_sensitive)
                VALUES (
                    '{_KEY_SCOPES}',
                    _val,
                    'google',
                    'Google OAuth granted scopes',
                    false
                )
                ON CONFLICT (secret_key) DO UPDATE SET
                    secret_value = EXCLUDED.secret_value,
                    category     = EXCLUDED.category,
                    description  = EXCLUDED.description,
                    is_sensitive = EXCLUDED.is_sensitive,
                    updated_at   = now();
            END IF;
        END
        $$;
    """)

    # ------------------------------------------------------------------ #
    # 2. Drop the old table
    # ------------------------------------------------------------------ #
    op.execute(f"DROP TABLE IF EXISTS {_OLD_TABLE}")


def downgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Re-create google_oauth_credentials
    # ------------------------------------------------------------------ #
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {_OLD_TABLE} (
            credential_key TEXT PRIMARY KEY,
            credentials JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ------------------------------------------------------------------ #
    # 2. Restore the JSONB blob from butler_secrets (best-effort)
    # ------------------------------------------------------------------ #
    op.execute(f"""
        DO $$
        DECLARE
            _client_id     TEXT;
            _client_secret TEXT;
            _refresh_token TEXT;
            _scopes        TEXT;
            _payload       JSONB;
        BEGIN
            SELECT secret_value INTO _client_id
              FROM {_NEW_TABLE} WHERE secret_key = '{_KEY_CLIENT_ID}';

            SELECT secret_value INTO _client_secret
              FROM {_NEW_TABLE} WHERE secret_key = '{_KEY_CLIENT_SECRET}';

            SELECT secret_value INTO _refresh_token
              FROM {_NEW_TABLE} WHERE secret_key = '{_KEY_REFRESH_TOKEN}';

            SELECT secret_value INTO _scopes
              FROM {_NEW_TABLE} WHERE secret_key = '{_KEY_SCOPES}';

            -- Only restore if at least client_id is present
            IF _client_id IS NOT NULL THEN
                _payload := jsonb_build_object(
                    'client_id',     coalesce(_client_id, ''),
                    'client_secret', coalesce(_client_secret, ''),
                    'refresh_token', coalesce(_refresh_token, ''),
                    'scope',         _scopes
                );

                INSERT INTO {_OLD_TABLE} (credential_key, credentials)
                VALUES ('{_SINGLETON_KEY}', _payload)
                ON CONFLICT (credential_key) DO UPDATE SET
                    credentials = EXCLUDED.credentials,
                    updated_at  = now();
            END IF;
        END
        $$;
    """)

    # ------------------------------------------------------------------ #
    # 3. Remove migrated rows from butler_secrets
    # ------------------------------------------------------------------ #
    op.execute(f"""
        DELETE FROM {_NEW_TABLE}
        WHERE secret_key IN (
            '{_KEY_CLIENT_ID}',
            '{_KEY_CLIENT_SECRET}',
            '{_KEY_REFRESH_TOKEN}',
            '{_KEY_SCOPES}'
        )
    """)
