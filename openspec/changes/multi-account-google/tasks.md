## 1. Database Schema & Migration

- [ ] 1.1 Create Alembic migration for `shared.google_accounts` table with columns: id, entity_id, email, display_name, is_primary, granted_scopes, status, connected_at, last_token_refresh_at, metadata. Include unique index on email and partial unique index for primary singleton.
- [ ] 1.2 Write data migration logic: detect existing `google_oauth_refresh` on owner entity → create companion entity (`roles=['google_account']`) → create `google_accounts` row (`is_primary=true`, `email=NULL` initially) → re-point `entity_info.entity_id` from owner entity to companion entity.
- [ ] 1.3 Add grants on `shared.google_accounts` for all butler roles (matching the pattern from `core_014`).

## 2. Google Account Registry (Core)

- [ ] 2.1 Create `src/butlers/google_account_registry.py` with: `create_google_account(pool, email, display_name, scopes)`, `list_google_accounts(pool)`, `get_google_account(pool, email_or_id)`, `set_primary_account(pool, account_id)`, `disconnect_account(pool, account_id, hard_delete=False)`.
- [ ] 2.2 Implement companion entity creation in `create_google_account()` — create entity with `tenant_id='shared'`, `canonical_name='google-account:<email>'`, `entity_type='other'`, `roles=['google_account']`, then insert `google_accounts` row referencing it.
- [ ] 2.3 Implement primary auto-promotion in `disconnect_account()` — when primary is disconnected with other accounts remaining, promote oldest by `connected_at`.
- [ ] 2.4 Implement Google token revocation in `disconnect_account()` — call `https://oauth2.googleapis.com/revoke` with the refresh token, handle failure gracefully.
- [ ] 2.5 Write tests for `google_account_registry.py`: create, list, get, set_primary, disconnect, auto-promote, soft limit enforcement.

## 3. Account-Aware Credential Resolution

- [ ] 3.1 Add `resolve_google_account_entity(pool, email=None)` to `credential_store.py` — resolves account email (or primary if None) to companion entity_id via `google_accounts` table.
- [ ] 3.2 Modify `store_google_credentials()` signature: add `account: str | UUID | None = None` parameter. Route refresh token storage to companion entity via `resolve_google_account_entity()`.
- [ ] 3.3 Modify `load_google_credentials()` signature: add `account: str | UUID | None = None` parameter. Route refresh token loading to companion entity.
- [ ] 3.4 Modify `delete_google_credentials()` signature: add `account: str | UUID | None = None` and `delete_all: bool = False` parameters. Per-account deletes only the refresh token; `delete_all` removes app credentials too.
- [ ] 3.5 Modify `resolve_google_credentials()` signature: add `account: str | UUID | None = None` parameter. Pass through to `load_google_credentials()`.
- [ ] 3.6 Update all existing tests in `test_google_credentials.py` and `test_google_credentials_credential_store.py` for new signatures. Add multi-account test cases.

## 4. Entity Identity Filtering

- [ ] 4.1 Update `entity_resolve()` in `src/butlers/modules/memory/tools/entities.py` to exclude entities with `'google_account' = ANY(roles)` from candidate results.
- [ ] 4.2 Update `entity_neighbors()` to exclude `google_account` entities from traversal results.
- [ ] 4.3 Update dashboard entity list queries to filter out `google_account` entities.
- [ ] 4.4 Write tests verifying google_account entities are invisible in resolve, neighbors, and dashboard queries.

## 5. OAuth Flow (Multi-Account)

- [ ] 5.1 Update `oauth_google_start()` in `api/routers/oauth.py`: accept `account_hint` query param, pass as `login_hint` in Google auth URL, store in state token, add account limit check.
- [ ] 5.2 Update `oauth_google_callback()`: after token exchange, call Google userinfo endpoint to get email/name, resolve-or-create `google_accounts` row via registry, store refresh token on companion entity instead of owner entity.
- [ ] 5.3 Add `force_consent` query param to `oauth_google_start()` — when true, add `prompt=consent` to auth URL (for scope upgrade / refresh token re-issue).
- [ ] 5.4 Handle callback without refresh_token: if account exists and already has a token, preserve it; if new account, return error directing to re-auth with `prompt=consent`.
- [ ] 5.5 Update `_check_google_credential_status()` to report per-account status.
- [ ] 5.6 Update tests in `api/test_oauth.py` for multi-account flows: new account, re-auth existing, account limit, missing refresh token.

## 6. Dashboard Account Management API

- [ ] 6.1 Add `GET /api/oauth/google/accounts` endpoint — returns list of connected accounts (no credential material).
- [ ] 6.2 Add `PUT /api/oauth/google/accounts/<id>/primary` endpoint — set primary account.
- [ ] 6.3 Add `DELETE /api/oauth/google/accounts/<id>` endpoint — disconnect account with optional `hard_delete` query param.
- [ ] 6.4 Add `GET /api/oauth/google/accounts/<id>/status` endpoint — per-account credential status with scope validation.
- [ ] 6.5 Update `GET /api/oauth/status` to include `accounts` array alongside legacy flat fields.
- [ ] 6.6 Write tests for all new account management endpoints.

## 7. Gmail Connector (Single Multi-Account Process)

- [ ] 7.1 Create `GmailConnectorManager` class — top-level orchestrator that discovers accounts from `shared.google_accounts`, spawns/stops `GmailAccountLoop` instances, and manages the lifecycle dict keyed by account email.
- [ ] 7.2 Create `GmailAccountLoop` class — encapsulates per-account state: credentials, cursor, label filters, watch subscription, backfill state. Runs as an independent asyncio task within the single process.
- [ ] 7.3 Implement account discovery at startup: query `shared.google_accounts WHERE status = 'active'` and filter to accounts with `gmail.modify` or `gmail.readonly` in `granted_scopes`. Spawn a loop per qualifying account.
- [ ] 7.4 Implement per-account credential resolution: each `GmailAccountLoop` resolves its own refresh token via `resolve_google_credentials(store, account=<email>)` and derives `CONNECTOR_ENDPOINT_IDENTITY` as `gmail:user:<email>`.
- [ ] 7.5 Implement per-account config overrides via `google_accounts.metadata.gmail` — label_include, label_exclude, poll_interval_s, pubsub_enabled, pubsub_topic. Fall back to process-level env var defaults.
- [ ] 7.6 Implement per-account error isolation: loop-level try/except with independent backoff/retry. A failed loop does not affect other loops.
- [ ] 7.7 Implement dynamic account discovery: periodic re-scan at `GMAIL_ACCOUNT_RESCAN_INTERVAL_S` (default 300). Spawn loops for new accounts, gracefully stop loops for removed/revoked accounts.
- [ ] 7.8 Add `connector_reload_accounts` MCP tool (and SIGHUP handler) to trigger immediate re-scan.
- [ ] 7.9 Update health endpoint to aggregate per-account health: worst-case overall status, per-account status array with email, endpoint_identity, status, timestamps, errors.
- [ ] 7.10 Remove `CONNECTOR_ENDPOINT_IDENTITY` from required env vars (now derived per-account). Remove `GMAIL_ACCOUNT` env var (replaced by DB discovery).
- [ ] 7.11 Update tests in `test_gmail_connector.py`: multi-account discovery, per-account isolation, dynamic add/remove, degraded startup with no qualifying accounts, per-account config overrides.

## 8. Calendar Module (Account Selection)

- [ ] 8.1 Add `account` field to `CalendarConfig` (optional email string).
- [ ] 8.2 Update `_GoogleProvider.__init__()` to pass `account` through to credential resolution.
- [ ] 8.3 Add scope validation at provider startup — check `granted_scopes` includes `calendar`.
- [ ] 8.4 Update `last_token_refresh_at` on `google_accounts` row after successful token refresh.
- [ ] 8.5 Write tests for calendar module with account selection and scope validation.

## 9. Contacts Module (Multi-Account)

- [ ] 9.1 Add `account` field to Google provider config entries in `ContactsConfig`.
- [ ] 9.2 Relax duplicate provider type validation: allow multiple `type = "google"` entries when `account` values are distinct. Reject duplicates with same account or both missing account.
- [ ] 9.3 Update `GoogleContactsProvider` credential resolution to pass `account` to `resolve_google_credentials()`.
- [ ] 9.4 Update `ContactsSyncStateStore` keying from `(provider)` to `(provider, account)`.
- [ ] 9.5 Update `contacts_sync_now` and `contacts_sync_status` MCP tools to accept optional `account` filter.
- [ ] 9.6 Add scope validation — check `granted_scopes` for `contacts.readonly` or `contacts`.
- [ ] 9.7 Write tests for multi-Google-account contacts sync: independent state, failure isolation, sync filtering.

## 10. butler.toml Config Updates

- [ ] 10.1 Update all roster `butler.toml` files to document the new `account` field in `[modules.calendar]` and `[[modules.contacts.providers]]` sections (no breaking changes — field is optional, defaults to primary).

## 11. Integration & Migration Testing

- [ ] 11.1 Write integration test: connect two Google accounts, verify independent credential storage and resolution.
- [ ] 11.2 Write integration test: disconnect primary, verify auto-promotion and module fallback.
- [ ] 11.3 Write migration test: verify existing single-account credentials are correctly promoted to first account entity.
- [ ] 11.4 Verify backward compatibility: modules with no `account` config resolve to primary, identical to pre-migration behavior.
