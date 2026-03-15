## ADDED Requirements

### Requirement: Google Account entity role

Entities with `'google_account'` in their `roles` array SHALL be treated as companion entities for Google account credential storage. They are infrastructure entities, not identity entities.

#### Scenario: Google account role recognized

- **WHEN** an entity has `roles = ['google_account']`
- **THEN** it SHALL be recognized as a Google account companion entity
- **AND** it SHALL anchor `entity_info` rows (type `google_oauth_refresh`) for that account's credentials

#### Scenario: Google account entities excluded from identity resolution

- **WHEN** `entity_resolve()` searches for entities by name
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be excluded from candidate results
- **AND** they SHALL NOT appear in fuzzy name matching or alias resolution

#### Scenario: Google account entities excluded from graph traversal defaults

- **WHEN** `entity_neighbors()` traverses the entity graph with default parameters
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be excluded from traversal results
- **AND** edge facts pointing to/from google_account entities SHALL NOT be followed

#### Scenario: Google account entities excluded from dashboard entity lists

- **WHEN** the dashboard fetches entities for display (entity list, unidentified entities)
- **THEN** entities with `'google_account' = ANY(roles)` SHALL be filtered out
- **AND** they SHALL NOT appear in entity count statistics

### Requirement: Entity info supports multiple Google accounts

The `shared.entity_info` table's `UNIQUE(entity_id, type)` constraint SHALL naturally support multiple `google_oauth_refresh` rows — one per Google account companion entity. No constraint change is needed.

#### Scenario: Two accounts with independent refresh tokens

- **WHEN** Google account A has companion entity E1 and account B has companion entity E2
- **THEN** `entity_info` SHALL contain two rows: `(E1, 'google_oauth_refresh', token_A)` and `(E2, 'google_oauth_refresh', token_B)`
- **AND** the `UNIQUE(entity_id, type)` constraint is satisfied because `E1 != E2`

#### Scenario: Owner entity no longer stores Google refresh token

- **WHEN** the multi-account migration completes
- **THEN** the owner entity SHALL NOT have a `google_oauth_refresh` row in `entity_info`
- **AND** Google refresh tokens SHALL only exist on companion entities referenced by `google_accounts.entity_id`

## MODIFIED Requirements

### Requirement: Entity info type registry (frontend ↔ backend coupling)

The entity detail page (`/butlers/entities/:id`) provides an "Add property" form with a type dropdown. **This dropdown is the sole UI for provisioning credentials that backend modules resolve at startup.** If a credential type is missing from the dropdown, users cannot configure it through the dashboard.

The frontend `ENTITY_INFO_TYPES` array and the backend module credential lookups (via `resolve_owner_entity_info(pool, info_type)` or `resolve_google_account_entity(pool, email)`) form a tight coupling: every `info_type` that a module resolves MUST be present in the frontend dropdown, and the frontend MUST mark credential types as secured.

#### Canonical type registry

| Type | Label | Secured | Consumed by |
|---|---|---|---|
| `email` | Email | no | Identity / contact info |
| `telegram` | Telegram Handle | no | Identity / contact info |
| `telegram_chat_id` | Telegram Chat ID | no | Identity / Switchboard routing |
| `api_key` | API Key | yes | (generic) |
| `api_secret` | API Secret | yes | (generic) |
| `token` | Token | yes | (generic) |
| `password` | Password | yes | (generic) |
| `username` | Username | no | (generic) |
| `url` | URL | no | (generic) |
| `telegram_api_id` | Telegram API ID | no | Contacts module (`on_startup`) |
| `telegram_api_hash` | Telegram API Hash | yes | Contacts module (`on_startup`) |
| `telegram_user_session` | Telegram User Session | yes | Contacts module (`on_startup`) |
| `home_assistant_url` | Home Assistant URL | no | Home module (`on_startup`) |
| `home_assistant_token` | Home Assistant Token | yes | Home module (`on_startup`) |
| `google_oauth_refresh` | Google OAuth Refresh | yes | Google account registry (companion entities only) |
| `email_password` | Email Password | yes | Email module |
| `other` | Other | no | (generic) |

**Change note:** `google_oauth_refresh` is now consumed by the Google account registry on companion entities, not directly by modules via `resolve_owner_entity_info()`. The type remains in the registry for visibility but manual editing of `google_oauth_refresh` rows on the owner entity is no longer meaningful — Google OAuth tokens are managed exclusively through the `/api/oauth/google/*` endpoints.

**Maintenance rule:** When a new module introduces a credential dependency via `resolve_owner_entity_info()`, the developer MUST add the corresponding type to:
1. The frontend `ENTITY_INFO_TYPES` array in `frontend/src/pages/EntityDetailPage.tsx`
2. The `SECURED_TYPES` set (if the value is a secret)
3. The `entityInfoTypeLabel()` switch for a human-readable label
4. This spec's canonical type registry table

#### Scenario: Module credential type missing from frontend dropdown

- **WHEN** a backend module calls `resolve_owner_entity_info(pool, 'new_credential_type')` at startup
- **AND** `'new_credential_type'` is NOT in the frontend `ENTITY_INFO_TYPES` array
- **THEN** users CANNOT configure this credential through the dashboard entity detail page
- **AND** the module will fail to start or degrade (depending on its error handling)
- **AND** this is considered a bug — the type MUST be added to the frontend

#### Scenario: All module credential types are present in the dropdown

- **WHEN** a user navigates to the entity detail page for the owner entity
- **THEN** the type dropdown MUST include all credential types listed in the canonical type registry
- **AND** selecting a secured type MUST use a password input field and auto-set `secured = true`

#### Scenario: Adding a new module with credential dependency

- **WHEN** a developer creates a new module that resolves credentials via `resolve_owner_entity_info()`
- **THEN** the module's credential types MUST be added to the frontend dropdown before the module is deployed
- **AND** the canonical type registry in this spec MUST be updated

#### Scenario: Google OAuth refresh token not editable on owner entity

- **WHEN** a user views the owner entity's entity_info on the dashboard
- **THEN** `google_oauth_refresh` rows SHALL NOT appear (they live on companion entities)
- **AND** the dashboard SHALL direct users to the Google Accounts management page for OAuth management
