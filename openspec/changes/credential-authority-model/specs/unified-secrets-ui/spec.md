## ADDED Requirements

### Requirement: Secrets page has System and User tabs
The `/secrets` dashboard page SHALL display a tab toggle with two tabs: "System" and "User". The System tab SHALL display ecosystem-wide credentials from `butler_secrets`. The User tab SHALL display identity-bound credentials from `entity_info` on the owner entity.

#### Scenario: Default tab is System
- **WHEN** a user navigates to `/secrets`
- **THEN** the System tab is active by default and displays the existing butler_secrets table

#### Scenario: Switching to User tab
- **WHEN** a user clicks the "User" tab
- **THEN** the page displays entity_info entries for the owner entity in a category-grouped table

### Requirement: User tab lists owner entity_info with templates
The User tab SHALL display known credential templates (Telegram API keys, HA token/URL, email, WhatsApp phone, etc.) as expected rows. Configured entries SHALL show as "local" with their value masked (secured) or visible (non-secured). Missing entries SHALL show as "missing" with a "Set value" action.

#### Scenario: Template row for missing credential
- **WHEN** the owner entity has no `home_assistant_token` entry in entity_info
- **THEN** the User tab shows a row for "home_assistant_token" with status "Missing" and a "Set value" button

#### Scenario: Template row for configured credential
- **WHEN** the owner entity has a `home_assistant_token` entry in entity_info
- **THEN** the User tab shows the row with status "Local configured", a masked value with reveal button, and edit/delete actions

### Requirement: User tab supports full CRUD
The User tab SHALL support creating, reading (with reveal for secured values), updating, and deleting entity_info entries on the owner entity.

#### Scenario: Adding a new user credential
- **WHEN** a user clicks "Add Credential" on the User tab and selects type "home_assistant_url", enters a value, and submits
- **THEN** a new entity_info entry is created on the owner entity via `POST /api/relationship/entities/{entity_id}/info`

#### Scenario: Revealing a secured value
- **WHEN** a user clicks the reveal button on a secured entry (e.g., `home_assistant_token`)
- **THEN** the actual value is fetched via `GET /api/relationship/entities/{entity_id}/secrets/{info_id}` and displayed

#### Scenario: Deleting a user credential
- **WHEN** a user clicks delete on a configured entry and confirms
- **THEN** the entity_info entry is removed via `DELETE /api/relationship/entities/{entity_id}/info/{info_id}`

### Requirement: Backend endpoint resolves owner entity internally
The API SHALL expose `GET /api/relationship/owner/entity-info` which resolves the owner entity by role (`'owner' = ANY(roles)`) and returns all entity_info entries. The frontend SHALL NOT need to know the owner entity UUID.

#### Scenario: Owner entity exists
- **WHEN** the endpoint is called and an owner entity exists
- **THEN** it returns `{ entity_id, entity_name, entries: EntityInfoEntry[] }` with secured values masked

#### Scenario: No owner entity
- **WHEN** the endpoint is called and no owner entity exists
- **THEN** it returns HTTP 404 with detail "No owner entity found"

### Requirement: Non-secured values display directly
Non-secured entity_info entries (e.g., `home_assistant_url`, `telegram_api_id`) SHALL display their value directly in the table without requiring a reveal action.

#### Scenario: Non-secured value visible
- **WHEN** a user views the User tab with a configured `home_assistant_url` entry (secured=false)
- **THEN** the URL value is shown directly in the Value column without masking

#### Scenario: Secured value masked
- **WHEN** a user views the User tab with a configured `home_assistant_token` entry (secured=true)
- **THEN** the value shows as masked dots with a reveal toggle button
