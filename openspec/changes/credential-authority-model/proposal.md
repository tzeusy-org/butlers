## Why

Credentials are scattered across two storage backends (`butler_secrets` and `public.entity_info`) with inconsistent resolution patterns across connectors and modules. Some connectors read from `butler_secrets`, some from `entity_info`, some check both with different priority. The dashboard manages these in two completely separate UIs — system secrets at `/secrets` and user credentials buried on the entity detail page. The HA settings page writes to `butler_secrets` but the HA connector reads from `entity_info`, creating orphaned entries. This inconsistency causes startup failures (connector can't find credentials that exist in the wrong store) and confuses users who don't know where to configure what.

## What Changes

- **Formalize a three-tier credential authority model** with clear ownership rules:
  - **Tier 0 (Bootstrap)**: Environment variables for infrastructure (`POSTGRES_*`, `SWITCHBOARD_MCP_URL`, deployment topology)
  - **Tier 1 (System)**: `butler_secrets` via dashboard — ecosystem-wide credentials (bot tokens, OAuth client IDs/secrets, S3 config, LLM API keys, webhook tokens)
  - **Tier 2 (User)**: `entity_info` on the owner entity — identity-bound credentials (HA token/URL, Telegram API id/hash/session, Spotify OAuth tokens, user email, WhatsApp phone)
- **Add System/User toggle on the `/secrets` page** so both tiers are managed in one place
- **Add backend endpoint** `GET /api/relationship/owner/entity-info` to list owner credentials without requiring the entity UUID
- **Migrate the HA connector** to read from `entity_info` instead of `CredentialStore` (already done)
- **Fix HA settings dashboard** to write to `entity_info` instead of `butler_secrets` (future)
- **Migrate Spotify OAuth tokens** from `butler_secrets` to `entity_info` on the owner entity, following the Google pattern (future)
- **Update `core-credentials` spec** with the three-tier authority model and tier classification for each credential

## Capabilities

### New Capabilities
- `unified-secrets-ui`: System/User tab toggle on the `/secrets` dashboard page with full CRUD for both `butler_secrets` (system) and `entity_info` (user) credentials

### Modified Capabilities
- `core-credentials`: Formalize the three-tier credential authority model; classify each credential into its authoritative tier; define resolution rules (env override -> authoritative store)

## Impact

- **Backend**: New endpoint in `roster/relationship/api/router.py`; HA connector credential resolution changed from `CredentialStore` to `resolve_owner_entity_info`
- **Frontend**: New files in `frontend/src/lib/`, `frontend/src/hooks/`, `frontend/src/components/secrets/`; refactored `SecretsTable` for polymorphic mode; restructured `SecretsPage` with tabs
- **Connectors affected (future)**: Spotify connector/module, HA settings dashboard router, OwnTracks (stays as-is)
- **Database**: No schema changes — `entity_info` and `butler_secrets` tables already exist
