## ADDED Requirements

### Requirement: Three-Tier Credential Authority Model
The project SHALL classify all credentials into exactly one of three authority tiers. Each credential has a single authoritative storage location. Environment variables MAY override any tier for development/testing, but the authoritative source is what the dashboard writes to and what connectors/modules read from at runtime.

#### Scenario: Tier 0 — Bootstrap environment variables
- **WHEN** a credential is required before the database is available (e.g., `POSTGRES_HOST`, `POSTGRES_PASSWORD`, `SWITCHBOARD_MCP_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT`)
- **THEN** the credential SHALL be classified as Tier 0 and read exclusively from environment variables

#### Scenario: Tier 1 — Ecosystem-wide system credentials in butler_secrets
- **WHEN** a credential is ecosystem-wide and not bound to a specific user identity (e.g., `BUTLER_TELEGRAM_TOKEN`, `GOOGLE_OAUTH_CLIENT_ID`, `BLOB_S3_*`, LLM API keys, `owntracks_webhook_token`)
- **THEN** the credential SHALL be classified as Tier 1, stored in `butler_secrets`, and managed via the System tab on the dashboard `/secrets` page

#### Scenario: Tier 2 — User-identity credentials in entity_info
- **WHEN** a credential is bound to the owner's personal identity or account (e.g., `home_assistant_token`, `telegram_api_hash`, `spotify_refresh_token`, `email_password`, `whatsapp_phone`)
- **THEN** the credential SHALL be classified as Tier 2, stored in `public.entity_info` on the owner entity, and managed via the User tab on the dashboard `/secrets` page

### Requirement: Owner entity_info resolution utility
The `resolve_owner_entity_info(pool, info_type)` function SHALL be the standard way to resolve Tier 2 credentials at runtime. It queries `public.entity_info` joined to `public.entities` where `'owner' = ANY(e.roles)`, preferring `is_primary=true` rows.

#### Scenario: Connector resolves Tier 2 credential
- **WHEN** a connector needs an identity-bound credential (e.g., HA connector needs `home_assistant_token`)
- **THEN** it SHALL call `resolve_owner_entity_info(pool, "home_assistant_token")` instead of `CredentialStore.load()`

#### Scenario: Companion entity credentials
- **WHEN** a credential is per-account rather than per-owner (e.g., Google OAuth refresh token, Steam API key)
- **THEN** it SHALL be stored in `entity_info` on the companion entity (not the owner entity) and resolved via direct SQL keyed by the companion entity UUID

### Requirement: Tier classification for existing credentials
Each credential in the system SHALL be classified according to the three-tier model. The following classification is normative:

**Tier 0 (Bootstrap env vars):**
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_SSLMODE`
- `DATABASE_URL`
- `SWITCHBOARD_MCP_URL`
- `GOOGLE_OAUTH_REDIRECT_URI`, `SPOTIFY_OAUTH_REDIRECT_URI`
- All `CONNECTOR_*` configuration variables

**Tier 1 (System — butler_secrets):**
- `BUTLER_TELEGRAM_TOKEN`, `BUTLER_EMAIL_ADDRESS`, `BUTLER_EMAIL_PASSWORD`
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_SCOPES`
- `GOOGLE_CALENDAR_ID`
- `BLOB_S3_*` (all 5 keys)
- `SPOTIFY_CLIENT_ID`
- `cli-auth/*` (LLM API keys)
- `DISCORD_BOT_TOKEN`
- `GMAIL_PUBSUB_WEBHOOK_TOKEN`
- `owntracks_webhook_token`

**Tier 2 (User — entity_info on owner entity):**
- `home_assistant_url`, `home_assistant_token`
- `telegram_api_id`, `telegram_api_hash`, `telegram_user_session`, `telegram_chat_id`
- `email`, `email_password`
- `whatsapp_phone`
- `google_oauth_refresh` (on companion entities)
- `steam_api_key` (on companion entities)
- `spotify_access_token`, `spotify_refresh_token`, `spotify_token_expires_at`, `spotify_granted_scopes` (migration pending)

#### Scenario: New credential added to the system
- **WHEN** a developer adds a new credential to any connector or module
- **THEN** they SHALL classify it into one of the three tiers based on whether it is infrastructure (Tier 0), ecosystem-wide (Tier 1), or identity-bound (Tier 2)
- **AND** they SHALL read it from the authoritative store for that tier
