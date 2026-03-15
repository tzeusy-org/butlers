## Context

Butlers stores Google OAuth credentials in two places: app credentials (client_id, client_secret) in `butler_secrets`, and the refresh token in `shared.entity_info` keyed to the singleton owner entity. All Google-consuming modules — Gmail connector, Calendar, Contacts — resolve credentials through `load_google_credentials()` / `resolve_google_credentials()`, which hardcode resolution to the owner entity via `resolve_owner_entity_info(pool, "google_oauth_refresh")`.

The `shared.entity_info` table has a `UNIQUE(entity_id, type)` constraint. Since there's one owner entity and one `google_oauth_refresh` type, only one refresh token can exist. The OAuth flow overwrites the token on re-auth.

The Contacts module already has a `(provider, account_id)` keying concept in its sync state store, and the Gmail connector already supports multiple concurrent instances with unique auto-resolved `endpoint_identity` values. The multi-account plumbing partially exists — the blocker is credential storage and resolution.

## Goals / Non-Goals

**Goals:**
- Support N concurrent Google accounts with independent OAuth tokens
- Account-aware credential resolution for all Google consumers (Gmail, Calendar, Contacts)
- Primary account designation for backward-compatible default resolution
- Dashboard management of connected Google accounts (list, connect, disconnect, re-auth)
- Zero-downtime migration: existing single-account deployments continue working

**Non-Goals:**
- Multi-tenant / multi-user support (this is still single-owner, multiple Google accounts)
- Per-account OAuth app credentials (all accounts share the same OAuth client_id/secret)
- Automatic account discovery (accounts are explicitly connected via OAuth flow)
- Google Workspace domain-wide delegation or service account support
- Per-account scope restrictions (all accounts get the same requested scopes)

## Decisions

### D1: Account entities vs. new table

**Decision:** Create a `shared.google_accounts` registry table rather than repurposing entity rows.

**Rationale:** Entity rows are identity records (people, organizations, places). A Google account is an *authentication context*, not an identity. Overloading entities with `role = 'google_account'` would blur the entity model and complicate entity resolution, merge, and graph traversal. A dedicated table is cleaner:

```sql
CREATE TABLE shared.google_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR NOT NULL UNIQUE,
    display_name VARCHAR,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    granted_scopes TEXT[],
    status VARCHAR NOT NULL DEFAULT 'active',  -- active, revoked, expired
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_token_refresh_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb
);
```

**Alternative considered:** Store everything in `entity_info` with a compound type like `google_oauth_refresh:<email>`. Rejected because: (a) it bends the type system — `entity_info.type` is a simple enum, not a namespaced key; (b) listing all accounts requires LIKE queries on type; (c) account metadata (display_name, scopes, status) would need separate rows or JSON encoding.

**Refresh token storage:** Each account's refresh token is still stored in `shared.entity_info`, but keyed by `(google_account.id, "google_oauth_refresh")` where `google_account.id` is used as the `entity_id`. To make this work without a real entity row, we add a foreign key from `google_accounts.id` to `entity_info.entity_id` — OR we create a lightweight entity per account purely as an FK anchor for `entity_info`.

**Revised decision:** Each `google_accounts` row gets a companion entity in `shared.entities` with `entity_type = 'other'` and `roles = ['google_account']`. This entity anchors the `entity_info` row for the refresh token. The `UNIQUE(entity_id, type)` constraint on `entity_info` naturally supports one refresh token per account entity. The companion entity is an implementation detail — it does not appear in identity resolution or contact resolution (filtered by role).

### D2: App credentials remain shared

**Decision:** `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` stay in `butler_secrets` as they are. They represent the OAuth application, not a user account. All accounts authenticate against the same OAuth client.

**Alternative considered:** Per-account client credentials. Rejected — unnecessary complexity; Google OAuth apps are typically per-deployment, not per-user-account.

### D3: Account-aware credential resolution API

**Decision:** Add `account` parameter (email string or UUID) to all Google credential functions. `None` means "use the primary account."

```python
# Before
async def load_google_credentials(store, *, pool=None) -> GoogleCredentials | None

# After
async def load_google_credentials(
    store, *, pool=None, account: str | UUID | None = None
) -> GoogleCredentials | None
```

When `account is None`: query `shared.google_accounts WHERE is_primary = true`, resolve its entity_id, load refresh token from `entity_info`. When `account` is an email string: query by email. When `account` is a UUID: query by id.

**Backward compatibility:** All existing call sites that pass no `account` parameter will resolve to the primary account — identical to current behavior after migration promotes the existing token to a primary account.

### D4: Primary account guarantee

**Decision:** Exactly one account MUST be primary at all times (when at least one account exists). Enforced by:

1. A partial unique index: `CREATE UNIQUE INDEX ON shared.google_accounts ((true)) WHERE is_primary = true`
2. The first connected account is automatically primary
3. Disconnecting the primary account auto-promotes the next oldest account (or leaves no primary if no accounts remain)
4. Setting a new primary atomically clears the old one in a single transaction

### D5: Module configuration for account selection

**Decision:** Modules specify `account = "<email>"` in their `butler.toml` config section. If omitted, the primary account is used.

```toml
[modules.calendar]
provider = "google"
account = "work@gmail.com"       # optional, defaults to primary

[[modules.contacts.providers]]
type = "google"
account = "personal@gmail.com"   # optional, defaults to primary
```

The Gmail connector does NOT use per-account env vars or config. See D8.

### D6: Contacts module — multiple Google providers

**Decision:** Relax the "duplicate provider types" constraint in `ContactsConfig`. When multiple providers share the same `type = "google"`, they MUST have distinct `account` fields. The sync state key becomes `(provider_type, account)` instead of just `(provider_type)`.

```toml
[[modules.contacts.providers]]
type = "google"
account = "personal@gmail.com"

[[modules.contacts.providers]]
type = "google"
account = "work@gmail.com"
```

### D8: Single multi-account Gmail connector process

**Decision:** The Gmail connector runs as a single process that manages all connected Google accounts. At startup it queries `shared.google_accounts` for all active accounts with `gmail.modify` (or `gmail.readonly`) in their `granted_scopes`, and spawns an independent watch/poll loop per account. No `GMAIL_ACCOUNT` env var.

**Architecture:**
- `GmailConnectorManager` is the top-level orchestrator. It holds a dict of `GmailAccountLoop` instances keyed by account email.
- Each `GmailAccountLoop` owns: its own `GmailConnectorConfig` (with account-specific credentials), its own cursor, its own label filters (from `google_accounts.metadata` or shared config), and its own history/watch state.
- `endpoint_identity` is auto-resolved per-account as `gmail:user:<email>` — one identity per loop, not one per process.
- Account discovery happens at startup and can be re-triggered via a `connector_reload_accounts` MCP tool or SIGHUP signal. New accounts start a loop; removed/revoked accounts gracefully stop theirs.
- Health endpoint aggregates per-account health: overall status is the worst-case across loops.

**Per-account overrides via `google_accounts.metadata`:**
```json
{
  "gmail": {
    "label_include": ["INBOX"],
    "label_exclude": ["SPAM", "TRASH"],
    "poll_interval_s": 30,
    "pubsub_enabled": false
  }
}
```
If absent, process-level env var defaults apply (`GMAIL_POLL_INTERVAL_S`, `GMAIL_LABEL_INCLUDE`, etc.).

**Alternative considered:** One process per account via `GMAIL_ACCOUNT` env var. Rejected because: (a) operationally heavy — N accounts = N processes to deploy and monitor; (b) doesn't scale dynamically when accounts are added/removed via dashboard; (c) env-var-per-account config is fragile and not discoverable.

**Trade-off:** Single process means a crash affects all accounts. Mitigated by per-loop error isolation — a failed token refresh or API error in one loop does not propagate to others. The loop enters a backoff/retry cycle independently.

### D7: OAuth flow changes

**Decision:** The `/api/oauth/google/start` endpoint accepts an optional `account_hint` query parameter (email). This is passed to Google as `login_hint` to pre-select the account. The callback:

1. Exchanges code for tokens
2. Calls Google's `userinfo` endpoint to get the authenticated email
3. Upserts `shared.google_accounts` row for that email
4. Creates companion entity if not exists
5. Stores refresh token in `entity_info` for the companion entity
6. If this is the first account, sets `is_primary = true`

Re-authenticating an existing account updates its refresh token and `granted_scopes` without affecting other accounts.

## Risks / Trade-offs

**[Risk] Companion entity pollution** → Entity resolution and graph traversal could surface account entities. **Mitigation:** Filter `roles @> '{google_account}'` out of identity resolution queries. Add a `WHERE NOT 'google_account' = ANY(roles)` guard to `entity_resolve`.

**[Risk] Primary account deletion breaks modules** → If the primary account is disconnected while modules reference it by default. **Mitigation:** Auto-promote next account. Modules that reference a specific account by email get a startup error if that account doesn't exist (fail-fast).

**[Risk] Token refresh race conditions** → Multiple modules sharing an account may attempt concurrent token refreshes. **Mitigation:** This already exists today with the single account. The Google OAuth token endpoint is idempotent for refresh_token grants — concurrent refreshes succeed and return independent access tokens.

**[Risk] Migration complexity** → Existing deployments have a refresh token on the owner entity_info. **Mitigation:** Migration creates a `google_accounts` row from the existing token, creates a companion entity, moves the `entity_info` row from owner entity to companion entity, and sets `is_primary = true`. Rollback: reverse the entity_info re-pointing.

**[Risk] Single-process Gmail connector crash radius** → One process crash takes down all account loops. **Mitigation:** Per-loop error isolation with independent backoff. Crash-level faults (OOM, segfault) are mitigated by process supervisor restart — all loops resume from their persisted cursors. Monitoring via per-account health status.

**[Risk] Dynamic account discovery drift** → Connector starts with N accounts but a new account is connected via dashboard mid-flight. **Mitigation:** `connector_reload_accounts` MCP tool and SIGHUP handler trigger re-discovery. Alternatively, a periodic re-scan (every 5 minutes) catches new/removed accounts.

**[Trade-off] Account as email string vs. UUID in config** → Using email strings in `butler.toml` is human-readable but couples config to the email address (if user changes their Google email, config breaks). Accepted because: Google email addresses rarely change, and the alternative (UUIDs in config) is terrible UX.

## Migration Plan

1. **Add `shared.google_accounts` table** — new Alembic migration, no impact on existing data
2. **Create companion entity for existing account** — if `entity_info` has a `google_oauth_refresh` row on the owner entity:
   - Query the Google userinfo API (or store email during next OAuth callback) to get the email
   - Create `shared.entities` row with `roles = ['google_account']`
   - Create `shared.google_accounts` row with `is_primary = true`
   - Re-point `entity_info.entity_id` from owner entity to companion entity
3. **Update credential functions** — add `account` parameter with `None` default (backward compatible)
4. **Update OAuth endpoints** — add account resolution to callback, add management endpoints
5. **Update module configs** — add optional `account` field, validate at startup
6. **Update consumers** — Gmail connector, Calendar, Contacts pass account through to credential resolution

**Rollback:** If issues arise, the migration can be reversed by re-pointing `entity_info.entity_id` back to the owner entity. The `google_accounts` table can be dropped. Credential functions with `account=None` resolve to primary, which maps to the original account.

## Open Questions

1. **Email discovery during migration**: For existing deployments, we need the email associated with the current refresh token to create the `google_accounts` row. Options: (a) call Google userinfo API during migration, (b) leave email NULL and populate on next OAuth callback, (c) require a manual re-auth. Recommendation: option (b) — create with `email = NULL` initially, populate on first successful token refresh that returns email, block operations requiring email until populated.

2. **Scope per account**: Should we track and enforce per-account granted scopes? If a user connects their work account with only Calendar scope, should the Gmail connector refuse to start for that account? Recommendation: yes — the `granted_scopes` array on `google_accounts` is checked at module startup, and modules that require scopes not in the array fail-fast with an actionable re-auth message.

3. **Account limit**: Should there be a maximum number of connected accounts? Recommendation: soft limit of 10 (configurable), enforced at the OAuth start endpoint. This prevents accidental OAuth loops and bounds resource usage.
