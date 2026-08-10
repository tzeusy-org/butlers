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
3. **Environment variable** -- Falls back to `os.environ["TELEGRAM_BOT_TOKEN"]` only when `env_fallback=True` is explicitly passed. This step is **disabled by default** — callers must opt in.

This layered approach means credentials stored via the dashboard (which writes to DB) always take precedence over environment variables.

### Codex uses an explicit system-global authority

`cli-auth/codex` is the one exception to the generic local-first resolution
order. It represents the shared Codex CLI identity, not a per-schema domain
secret. Every Codex path must receive an explicitly selected
`CredentialStore(..., system_global_pool=...)` and use its strict
`load_codex_cli_auth()`, `store_codex_cli_auth()`, conditional rotation, and
conditional health methods. A flat deployment may deliberately pass the same
pool object as both local and system-global, but it still has to name that
selection.

Codex never uses `load()`, `resolve()`, `shared_pool`, `store_shared()`, or an
environment value as an authority fallback. If the explicit authority is
absent, unavailable, slow, or malformed, new Codex work fails closed; it does
not copy an old schema-local row or local `auth.json` back into the authority.
An already-running session is not replayed or changed. Ordinary credentials
and the other CLI providers retain the normal local/fallback behavior.

This is the implementation of `REQ-core-credentials-001` and
`REQ-core-daemon-001`.

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

For Codex only, use the explicit APIs instead of either generic writer:

```python
# `system_global_pool` is selected by the daemon/dashboard/connector boundary.
authority = CredentialStore(local_pool, system_global_pool=shared_pool)
await authority.store_codex_cli_auth(auth_document)
current = await authority.load_codex_cli_auth()
```

### Reading

```python
# DB-only lookup (local + fallback pools)
value = await store.load("GOOGLE_OAUTH_CLIENT_ID")

# DB-only by default; pass env_fallback=True to also check os.environ
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

Some credentials are stored in `public.entity_info` rather than `butler_secrets`. This applies to identity-bound credentials that belong to a specific entity (person or service account):

- **`google_oauth_refresh`** -- OAuth refresh tokens stored on Google account companion entities.
- **`telegram_api_id`**, **`telegram_api_hash`**, **`telegram_user_session`** -- Telegram user-client credentials on the owner entity.

The `resolve_owner_entity_info(pool, info_type)` function provides a dedicated lookup path:

```python
value = await resolve_owner_entity_info(pool, "telegram_api_id")
```

This queries `public.entities` for the owner entity (`'owner' = ANY(roles)`) and returns the matching `public.entity_info` value. Primary entries (`is_primary = true`) are preferred.

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

For Codex, `restore_tokens()` reads only the explicit system-global authority
and atomically replaces `~/.codex/auth.json` with that exact valid JSON object
at mode `0600`. It intentionally does not merge a stale local document. Before
each new runtime subprocess, reconciliation repeats the same authority check;
conditional writes fence CLI-driven token rotation and health state against a
concurrent dashboard replacement.

This means CLI credentials do not require persistent volume mounts in Kubernetes -- they are reconstructed from the DB on every startup.

### Codex authority coverage

| Surface | Authority boundary |
|---|---|
| Daemon and dashboard startup restore | Lifecycle/API constructs an explicit global store before restore. |
| Dashboard device auth, Passport mutation, and health probe | `cli_auth` and Passport name the shared store explicitly for persistence/revoke; device auth, manual probe, and scheduled passport probe converge through the same strict `cli_auth` boundary. |
| Runtime and speculative prewarm | `CodexAdapter` reconciles and revalidates the selected authority before every new child. |
| Direct and scheduled dispatch | `DiscretionDispatcher` accepts a separate `codex_auth_authority`; scheduled handlers receive the daemon-selected object through context injection. |
| Standalone Codex-dependent connectors | The connector opens an explicit shared authority, restores through it, injects it into dispatch, and closes that pool on shutdown. |

Safe operator evidence may identify that an authority channel was unavailable or
that a local scope was ignored. It must never include an auth document, token,
fingerprint, or raw provider error.

## Security Model

Butlers runs as a **user-federated platform** where each user owns their instance. This shapes credential storage decisions:

- Secrets are stored in plaintext in PostgreSQL -- the user controls the database directly.
- Encryption at rest adds minimal value in this model.
- API-level masking prevents accidental exposure in dashboard responses.
- `is_sensitive=True` secrets are excluded from list responses; a "Reveal" button provides on-demand access.
- Secret values are never logged -- even at DEBUG level.

## Verification

To confirm the credential store described here matches the running system:

```bash
# 1. butler_secrets table exists in each butler's schema
psql -h localhost -U butlers -d butlers -c \
  "SELECT secret_key, category, is_sensitive, (secret_value IS NOT NULL) AS has_value,
          created_at, expires_at
   FROM general.butler_secrets ORDER BY category, secret_key;"
# Expected: rows for configured credentials; secret_value not shown here (use Reveal in dashboard)

# 2. CLI auth tokens are stored under the cli-auth category
psql -h localhost -U butlers -d butlers -c \
  "SELECT secret_key, category, updated_at FROM general.butler_secrets
   WHERE category = 'cli-auth';"
# Expected: rows like "cli-auth/claude", "cli-auth/codex" after OAuth flow completes

# 3. DB-first resolution: DB value takes precedence over environment variable
# In Python (with a running pool and CredentialStore instance), call:
#   from butlers.core.credential_store import CredentialStore
#   value = await store.resolve('BUTLER_TEST_KEY', env_fallback=False)
#   # Returns the DB value (or None) — env var is NOT consulted unless env_fallback=True

# 4. Sensitive secrets are masked in dashboard API list responses
curl -s http://localhost:41200/api/butlers/general/secrets | python3 -m json.tool | grep -i "value"
# Expected: no raw secret values in list response; "is_set": true/false instead

# 5. Entity-based credentials exist in public.entity_info for the owner
psql -h localhost -U butlers -d butlers -c \
  "SELECT ei.type, (ei.value IS NOT NULL) AS has_value, ei.is_primary
   FROM public.entity_info ei
   JOIN public.entities e ON e.id = ei.entity_id
   WHERE 'owner' = ANY(e.roles)
   ORDER BY ei.type;"
# Expected: rows for google_oauth_refresh, telegram_api_id, etc. if configured

# 6. Focused Codex authority regression checks (no credential values required)
uv run pytest \
  tests/config/test_credential_store.py \
  tests/adapters/test_codex_auth_sync.py \
  tests/cli/test_cli_auth.py \
  tests/connectors/test_connector_codex_auth_restore.py \
  tests/connectors/test_discretion_dispatcher.py
```

## Related Pages

- [Schema Topology](schema-topology.md) -- Where `butler_secrets` lives
- [Owner Identity](../identity_and_secrets/owner-identity.md) -- Entity-based credential storage
- [CLI Runtime Auth](../identity_and_secrets/cli-runtime-auth.md) -- Token persistence details
