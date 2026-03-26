# Account Registry Pattern

Every multi-account integration follows the `public.google_accounts` pattern.

## Table Schema Template

```sql
CREATE TABLE public.<service>_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
    <external_id_column> <type> UNIQUE NOT NULL,  -- e.g., steam_id BIGINT, email VARCHAR
    display_name VARCHAR,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    status VARCHAR NOT NULL DEFAULT 'active',
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_poll_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT chk_<service>_accounts_status CHECK (status IN ('active', 'suspended', 'revoked'))
);

-- Unique index on external ID
CREATE UNIQUE INDEX ix_<service>_accounts_<ext_id> ON public.<service>_accounts (<external_id_column>);

-- Singleton primary account
CREATE UNIQUE INDEX ix_<service>_accounts_primary_singleton
    ON public.<service>_accounts ((true)) WHERE is_primary = true;
```

## Companion Entity

Each account row has a companion entity for credential anchoring:

```sql
-- Created on account connect:
INSERT INTO public.entities (tenant_id, canonical_name, entity_type, roles)
VALUES ('shared', '<service>-account:<external_id>', 'other', ARRAY['<service>_account']);

-- Credential stored as secured entity_info:
INSERT INTO public.entity_info (entity_id, type, value, secured)
VALUES (<companion_entity_id>, '<service>_api_key', '<credential>', true);
```

Companion entities with service-specific roles are excluded from identity resolution (they exist solely as credential anchors).

## Required Scenarios

Every registry spec must cover:

1. **Create** — Validate credentials on connect, create companion entity + entity_info
2. **First account auto-primary** — First connected account gets `is_primary = true`
3. **Lookup by external ID** — `WHERE <ext_id_col> = $1`
4. **Lookup by UUID** — `WHERE id = $1`
5. **Default to primary** — `WHERE is_primary = true`, error if none
6. **Disconnect (soft)** — Set `status = 'revoked'`, connector stops polling
7. **Hard delete** — CASCADE deletes companion entity and entity_info
8. **Reconnect** — Update status back to `active`, optionally update credential
9. **Metadata overrides** — JSONB for per-account config (poll intervals, tracked items, etc.)

## Dashboard Connect Flow

For API key integrations (no OAuth):
1. User enters external ID + API key in dashboard form
2. Backend validates key with a test API call
3. On success: create account row + companion entity + secured entity_info
4. On failure: return actionable error with link to registration page

For OAuth integrations:
1. Follow the Spotify PKCE pattern in `openspec/specs/dashboard-spotify-setup/spec.md`
2. Store tokens in entity_info on the companion entity
