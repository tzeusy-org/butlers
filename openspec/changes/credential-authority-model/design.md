## Context

Butlers uses two credential storage backends:
- `butler_secrets` table (per-butler schema + shared) — accessed via `CredentialStore` class
- `public.entity_info` table — identity-bound credentials on entities, accessed via `resolve_owner_entity_info()`

Over time, different connectors adopted different patterns. The Telegram user client reads exclusively from `entity_info`. The Spotify connector reads exclusively from `butler_secrets`. The HA connector was reading from `butler_secrets` but the HA jobs/module read from `entity_info` — creating a split where the dashboard wrote to one store but the runtime read from another.

The dashboard has two separate UIs: `/secrets` for `butler_secrets` CRUD and the entity detail page for `entity_info` CRUD. Users must navigate to different pages depending on credential type, with no guidance about which page to use for what.

## Goals / Non-Goals

**Goals:**
- Formalize the three-tier credential authority model as project doctrine
- Unify credential management UX: single `/secrets` page with System/User tabs
- Ensure every connector reads from its authoritative tier
- Make `entity_info` the single source of truth for user-identity credentials

**Non-Goals:**
- Migrating Spotify OAuth tokens to `entity_info` (tracked as future work)
- Fixing the HA settings dashboard write path (tracked as future work)
- Supporting multiple user profiles (only the owner entity for now)
- Encrypting credentials at rest (single-user model; user controls the database)
- Removing `CredentialStore` — it remains authoritative for Tier 1 system secrets

## Decisions

### 1. Three-tier classification, not two stores
**Decision:** Classify by authority tier (bootstrap / system / user), not by storage backend.
**Rationale:** The two storage backends (`butler_secrets` and `entity_info`) serve different purposes. Env vars are a third source. Framing by tier clarifies ownership: each credential has exactly one authoritative source. Env vars can override any tier for dev/testing, but the authoritative source is what the dashboard writes to and what connectors read from.

### 2. Owner entity_info as the authoritative Tier 2 store
**Decision:** All user-identity credentials live on the owner entity in `public.entity_info`.
**Rationale:** `entity_info` already stores Telegram API keys, Google OAuth refresh tokens, and HA credentials. The `resolve_owner_entity_info()` utility is well-established. Companion entities (Google, Steam accounts) follow the same pattern for per-account credentials. This is consistent with the entity-centric data model.
**Alternative considered:** A dedicated `user_credentials` table — rejected because `entity_info` already exists and is well-integrated.

### 3. Adapter layer for SecretsTable reuse
**Decision:** Map `EntityInfoEntry[]` into `SecretDisplayRow[]` via an adapter function, enabling full reuse of the existing `SecretsTable` component.
**Rationale:** The System and User tabs share the same UX pattern (category-grouped table, masked values, reveal, CRUD). Building a separate table component would duplicate ~500 lines of UI code. The adapter adds ~50 lines and the polymorphic callbacks add ~40 lines of changes to `SecretsTable`.

### 4. New backend endpoint for owner entity_info
**Decision:** Add `GET /api/relationship/owner/entity-info` that resolves the owner entity internally.
**Rationale:** The frontend shouldn't need to know the owner entity UUID to list user credentials. The endpoint follows the existing `GET /owner/setup-status` pattern.

## Risks / Trade-offs

- **[Data shape mismatch]** `SecretEntry` and `EntityInfoEntry` have different fields (timestamps, categories, sensitivity flags). The adapter normalizes these, but `entity_info` has no `updated_at` column — the "Last Updated" column shows "N/A" for user entries. → Acceptable for v1; add `updated_at` column to `entity_info` if needed later.

- **[Spotify migration complexity]** Moving Spotify OAuth tokens from `butler_secrets` to `entity_info` requires changes in the connector, module, dashboard OAuth callback, and SpotifyClient. The connector also writes refreshed tokens back to the store. → Deferred to a separate change to limit blast radius.

- **[HA settings page still writes to wrong store]** The `/settings/home-assistant` dashboard page writes to `butler_secrets` (`home_assistant:base_url`, `home_assistant:access_token`) — these entries are now orphaned since the connector reads from `entity_info`. → Tracked as future work; users can use the User tab on `/secrets` instead.

- **[Single owner entity assumption]** The User tab hardcodes resolution to the entity with `'owner' = ANY(roles)`. Multi-user scenarios would need a profile selector. → Non-goal for v1; matches the single-user deployment model.
