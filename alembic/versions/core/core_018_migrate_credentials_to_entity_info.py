"""migrate_credentials_to_entity_info: move owner credentials from contact_info to entity_info

Revision ID: core_018
Revises: core_017
Create Date: 2026-03-06 00:00:00.000000

Moves owner secured credentials from shared.contact_info to shared.entity_info.
These credential types are entity-level metadata that was previously stored on
the contact_info table alongside genuine channel identifiers.

Types moved to entity_info (keyed by owner entity_id):
  telegram_api_hash, telegram_api_id, telegram_user_session,
  home_assistant_token, google_oauth_refresh, email_password

Types kept on contact_info (genuine channel identifiers):
  telegram (chat ID), email (address), telegram_bot_token

Design notes:
  - Upgrade copies matching rows to entity_info with ON CONFLICT DO NOTHING,
    then deletes the originals from contact_info.
  - Downgrade reverses by copying rows back to contact_info (via owner contact),
    then deleting from entity_info.
  - All steps are guarded with table-existence checks for idempotency.
  - Requires the owner contact to have an entity_id link (established by core_014).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_018"
down_revision = "core_017"
branch_labels = None
depends_on = None

# Credential types that belong on entity_info, not contact_info.
_CREDENTIAL_TYPES = (
    "telegram_api_hash",
    "telegram_api_id",
    "telegram_user_session",
    "home_assistant_token",
    "google_oauth_refresh",
    "email_password",
)

_TYPES_SQL = ", ".join(f"'{t}'" for t in _CREDENTIAL_TYPES)


def upgrade() -> None:
    op.execute(f"""
        DO $$
        DECLARE
            v_owner_entity_id UUID;
            v_owner_contact_id UUID;
        BEGIN
            -- Guard: skip if required tables don't exist.
            IF to_regclass('shared.contacts') IS NULL
               OR to_regclass('shared.contact_info') IS NULL
               OR to_regclass('shared.entity_info') IS NULL
               OR to_regclass('shared.entities') IS NULL
            THEN
                RETURN;
            END IF;

            -- Resolve owner entity_id via shared.entities.
            SELECT e.id INTO v_owner_entity_id
            FROM shared.entities e
            WHERE 'owner' = ANY(e.roles)
            LIMIT 1;

            IF v_owner_entity_id IS NULL THEN
                RETURN;
            END IF;

            -- Resolve owner contact_id.
            SELECT c.id INTO v_owner_contact_id
            FROM shared.contacts c
            WHERE c.entity_id = v_owner_entity_id
            LIMIT 1;

            IF v_owner_contact_id IS NULL THEN
                RETURN;
            END IF;

            -- Copy credential rows from contact_info to entity_info.
            INSERT INTO shared.entity_info (entity_id, type, value, label, is_primary, secured)
            SELECT v_owner_entity_id, ci.type, ci.value, ci.label, ci.is_primary, ci.secured
            FROM shared.contact_info ci
            WHERE ci.contact_id = v_owner_contact_id
              AND ci.type IN ({_TYPES_SQL})
            ON CONFLICT (entity_id, type) DO NOTHING;

            -- Delete the originals from contact_info.
            DELETE FROM shared.contact_info
            WHERE contact_id = v_owner_contact_id
              AND type IN ({_TYPES_SQL});
        END
        $$;
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$
        DECLARE
            v_owner_entity_id UUID;
            v_owner_contact_id UUID;
        BEGIN
            -- Guard: skip if required tables don't exist.
            IF to_regclass('shared.contacts') IS NULL
               OR to_regclass('shared.contact_info') IS NULL
               OR to_regclass('shared.entity_info') IS NULL
               OR to_regclass('shared.entities') IS NULL
            THEN
                RETURN;
            END IF;

            -- Resolve owner entity_id via shared.entities.
            SELECT e.id INTO v_owner_entity_id
            FROM shared.entities e
            WHERE 'owner' = ANY(e.roles)
            LIMIT 1;

            IF v_owner_entity_id IS NULL THEN
                RETURN;
            END IF;

            -- Resolve owner contact_id.
            SELECT c.id INTO v_owner_contact_id
            FROM shared.contacts c
            WHERE c.entity_id = v_owner_entity_id
            LIMIT 1;

            IF v_owner_contact_id IS NULL THEN
                RETURN;
            END IF;

            -- Copy credential rows back from entity_info to contact_info.
            INSERT INTO shared.contact_info (contact_id, type, value, label, is_primary, secured)
            SELECT v_owner_contact_id, ei.type, ei.value, ei.label, ei.is_primary, ei.secured
            FROM shared.entity_info ei
            WHERE ei.entity_id = v_owner_entity_id
              AND ei.type IN ({_TYPES_SQL})
            ON CONFLICT (type, value) DO NOTHING;

            -- Delete from entity_info.
            DELETE FROM shared.entity_info
            WHERE entity_id = v_owner_entity_id
              AND type IN ({_TYPES_SQL});
        END
        $$;
    """)
