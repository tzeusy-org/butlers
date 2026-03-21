# Credential Store

> **Purpose:** Document the DB-first credential storage system and CLI auth token persistence.
> **Audience:** Developers integrating with secrets, operators configuring credentials.
> **Prerequisites:** [Schema Topology](schema-topology.md).

## Overview

Butlers uses a **DB-first** credential resolution strategy. Instead of scattering `os.environ.get()` calls across modules, all secrets flow through the `CredentialStore` class backed by the `butler_secrets` PostgreSQL table. Environment variables serve as a fallback for backward compatibility.

## Resolution Order

When a module calls `store.resolve("TELEGRAM_BOT_TOKEN")`:

1. **Local database** -- Queries the `butler_secrets` table in the butler's own schema.
2. **Shared database** -- Queries `butler_secrets` in configured fallback pools (the shared `butlers` database).
3. **Environment variable** -- Falls back to `os.environ["TELEGRAM_BOT_TOKEN"]` if `env_fallback=True` (the default).

This layered approach means credentials stored via the dashboard (which writes to DB) always take precedence over environment variables.

## The `butler_secrets` Table

```sql
CREATE TABLE butler_secrets (
    secret_key   TEXT PRIMARY KEY,
    secret_value TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    description  TEXT,
    is_sensitive BOOLEAN NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ
);
```

Key design decisions:
- **`is_sensitive`** controls whether values are masked in dashboard UI and logs.
- **`category`** groups secrets for dashboard display (e.g., `"telegram"`, `"google"`, `"cli-auth"`).
- **`expires_at`** supports optional time-bounded secrets.
- Raw secret values are **never** exposed by `list_secrets()` -- it returns `SecretMetadata` objects only.

## CredentialStore API

### Writing

```python
await store.store("telegram_bot_token", "1234:ABCD...", category="telegram")
await store.store_shared("GOOGLE_OAUTH_CLIENT_ID", "...", category="google")
```

`store()` writes to the local pool. `store_shared()` writes to the first fallback (shared) pool, falling back to local if no shared pool is configured.

### Reading

```python
# DB-only lookup (local + fallback pools)
value = await store.load("GOOGLE_OAUTH_CLIENT_ID")

# DB + env fallback
value = await store.resolve("TELEGRAM_BOT_TOKEN")

# Check existence
exists = await store.has("telegram_bot_token")
```

### Metadata

```python
# List secrets without revealing values
secrets = await store.list_secrets(category="telegram")
for meta in secrets:
    print(meta.key, meta.is_set, meta.source, meta.category)
```

### Deletion

```python
deleted = await store.delete("old_secret_key")
```

## Entity-Based Credentials

Some credentials are stored in `shared.entity_info` rather than `butler_secrets`. This applies to identity-bound credentials that belong to a specific entity (person or service account):

- **`google_oauth_refresh`** -- OAuth refresh tokens stored on Google account companion entities.
- **`telegram_api_id`**, **`telegram_api_hash`**, **`telegram_user_session`** -- Telegram user-client credentials on the owner entity.

The `resolve_owner_entity_info(pool, info_type)` function provides a dedicated lookup path:

```python
value = await resolve_owner_entity_info(pool, "telegram_api_id")
```

This queries `shared.entities` for the owner entity (`'owner' = ANY(roles)`) and returns the matching `shared.entity_info` value. Primary entries (`is_primary = true`) are preferred.

## CLI Auth Token Persistence

CLI runtime tokens (for Claude, Codex, etc.) are persisted to the credential store so they survive container restarts.

The persistence module at `src/butlers/cli_auth/persistence.py` handles two operations:

### Persist (after auth flow)

After a successful device-code auth flow, `persist_token()` reads the CLI's token file from disk and stores it in `butler_secrets` with:
- Key: `cli-auth/<provider_name>` (e.g., `cli-auth/codex`)
- Category: `cli-auth`
- `is_sensitive=True`

### Restore (on startup)

During application startup, `restore_tokens()` reads all CLI auth tokens from DB and writes them back to the filesystem paths the CLIs expect:
- Creates parent directories as needed.
- Sets file permissions to `0o600`.
- Merges JSON content when multiple providers share the same token path (e.g., opencode-openai and opencode-go both use `auth.json`).

This means CLI credentials do not require persistent volume mounts in Kubernetes -- they are reconstructed from the DB on every startup.

## Security Model

Butlers runs as a **user-federated platform** where each user owns their instance. This shapes credential storage decisions:

- Secrets are stored in plaintext in PostgreSQL -- the user controls the database directly.
- Encryption at rest adds minimal value in this model.
- API-level masking prevents accidental exposure in dashboard responses.
- `is_sensitive=True` secrets are excluded from list responses; a "Reveal" button provides on-demand access.
- Secret values are never logged -- even at DEBUG level.

## Related Pages

- [Schema Topology](schema-topology.md) -- Where `butler_secrets` lives
- [Owner Identity](../identity_and_secrets/owner-identity.md) -- Entity-based credential storage
- [CLI Runtime Auth](../identity_and_secrets/cli-runtime-auth.md) -- Token persistence details
