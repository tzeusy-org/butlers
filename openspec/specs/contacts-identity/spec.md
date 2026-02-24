## ADDED Requirements

### Requirement: Contacts table in shared schema

The `contacts` table SHALL reside in the `shared` PostgreSQL schema. All butler roles SHALL have `SELECT, INSERT, UPDATE, DELETE` grants on `shared.contacts`. The table SHALL be accessible via unqualified name `contacts` through every butler's `search_path` (which includes `shared`).

#### Scenario: Contacts accessible from any butler schema

- **WHEN** any butler daemon queries `SELECT * FROM contacts WHERE id = $1`
- **THEN** the query MUST resolve to `shared.contacts` via `search_path`
- **AND** the result MUST include all columns defined on the contacts table

#### Scenario: Schema migration from relationship to shared

- **WHEN** the Alembic migration runs on a database where `contacts` exists in the `relationship` schema
- **THEN** the migration MUST execute `ALTER TABLE relationship.contacts SET SCHEMA shared`
- **AND** all existing data, indexes, and sequences MUST be preserved
- **AND** all foreign key constraints from relationship-schema tables (relationships, interactions, notes, important_dates, gifts, loans, groups, contact_labels, quick_facts, addresses, life_events, tasks, activity_feed, reminders) MUST be re-created to reference `shared.contacts(id)`

---

### Requirement: Roles column on contacts

The `contacts` table SHALL have a `roles TEXT[] NOT NULL DEFAULT '{}'` column. Each element in the array represents a role assigned to that contact. The initial supported role value is `'owner'`.

#### Scenario: Owner contact has owner role

- **WHEN** a contact is the system owner
- **THEN** the contact's `roles` column MUST contain `'owner'`

#### Scenario: Non-owner contact has empty roles

- **WHEN** a contact is created without explicit role assignment
- **THEN** the contact's `roles` column MUST be `'{}'` (empty array)

#### Scenario: Query contacts by role

- **WHEN** a system component queries `SELECT * FROM contacts WHERE 'owner' = ANY(roles)`
- **THEN** the query MUST return exactly the contacts that have `'owner'` in their roles array

---

### Requirement: Role modification restricted to dashboard API

Contact roles SHALL only be modifiable through the dashboard API (authenticated HTTP endpoints). Butler runtime instances (MCP tool calls from LLM CLI sessions) MUST NOT be able to modify the `roles` column. The `contact_update` MCP tool MUST explicitly exclude `roles` from its writable fields.

#### Scenario: MCP tool attempts to set roles

- **WHEN** a runtime instance calls `contact_update(contact_id, roles=['owner'])`
- **THEN** the tool MUST ignore the `roles` field and NOT modify it
- **AND** the tool MUST return a success result with the contact's unchanged roles

#### Scenario: Dashboard API updates roles

- **WHEN** `PATCH /api/contacts/{id}` is called with `{"roles": ["owner", "family"]}` from an authenticated dashboard session
- **THEN** the contact's `roles` column MUST be updated to `['owner', 'family']`
- **AND** the response MUST include the updated roles

---

### Requirement: Owner contact bootstrap on first startup

When any butler daemon starts, it SHALL check for the existence of a contact with `'owner' = ANY(roles)` in `shared.contacts`. If no such contact exists, the daemon MUST create a seed contact with `roles = ['owner']`, `name = 'Owner'`, and no channel identifiers. The operation MUST be idempotent across concurrent butler startups.

#### Scenario: First butler starts with empty contacts table

- **WHEN** the first butler daemon starts and `shared.contacts` contains no rows
- **THEN** the daemon MUST create a contact with `roles = ['owner']` and `name = 'Owner'`
- **AND** the contact MUST have no `contact_info` entries

#### Scenario: Multiple butlers start concurrently

- **WHEN** three butler daemons start simultaneously and no owner contact exists
- **THEN** exactly one owner contact MUST be created (no duplicates)
- **AND** subsequent startups MUST detect the existing owner and skip creation

#### Scenario: Owner contact already exists

- **WHEN** a butler daemon starts and a contact with `'owner' = ANY(roles)` already exists
- **THEN** the daemon MUST NOT create a new contact
- **AND** startup MUST proceed normally

---

### Requirement: Contact info uniqueness constraint

The `shared.contact_info` table SHALL have a `UNIQUE(type, value)` constraint. Each channel identifier (e.g., a Telegram chat ID, an email address) MUST map to exactly one contact. The existing non-unique index `idx_shared_contact_info_type_value` SHALL be replaced by this unique constraint.

#### Scenario: Insert duplicate channel identifier

- **WHEN** a contact_info entry with `type='telegram'` and `value='12345'` already exists for contact A
- **AND** an attempt is made to insert `type='telegram'` and `value='12345'` for contact B
- **THEN** the insert MUST fail with a unique constraint violation

#### Scenario: Same contact can have multiple identifiers of same type

- **WHEN** a contact has `type='email'` and `value='alice@work.com'`
- **AND** an insert is made for the same contact with `type='email'` and `value='alice@personal.com'`
- **THEN** the insert MUST succeed (different value)

---

### Requirement: Foreign key from contact_info to contacts

The `shared.contact_info` table SHALL have a foreign key constraint `contact_info(contact_id) REFERENCES shared.contacts(id) ON DELETE CASCADE`. This replaces the previous application-layer referential integrity that was necessary when the tables were in different schemas.

#### Scenario: Delete a contact cascades to contact_info

- **WHEN** a contact is deleted from `shared.contacts`
- **THEN** all `shared.contact_info` rows with that `contact_id` MUST be automatically deleted

#### Scenario: Insert contact_info with invalid contact_id

- **WHEN** a `contact_info` row is inserted with a `contact_id` that does not exist in `shared.contacts`
- **THEN** the insert MUST fail with a foreign key violation

---

### Requirement: Reverse-lookup from channel identifier to contact

The system SHALL provide a `resolve_contact_by_channel(type, value)` function that returns the contact and their role-set for a given channel identifier. The function MUST query `shared.contact_info JOIN shared.contacts` using the `UNIQUE(type, value)` index.

#### Scenario: Known channel identifier resolves to contact

- **WHEN** `resolve_contact_by_channel('telegram', '12345')` is called
- **AND** a `contact_info` entry exists with `type='telegram'` and `value='12345'` linked to contact "Chloe" with `roles = []`
- **THEN** the function MUST return the contact's `id`, `name`, `roles`, and `entity_id`

#### Scenario: Owner channel identifier resolves with owner role

- **WHEN** `resolve_contact_by_channel('telegram', '99999')` is called
- **AND** the contact_info entry is linked to the owner contact with `roles = ['owner']`
- **THEN** the function MUST return `roles` containing `'owner'`

#### Scenario: Unknown channel identifier returns null

- **WHEN** `resolve_contact_by_channel('telegram', '00000')` is called
- **AND** no `contact_info` entry exists with that type and value
- **THEN** the function MUST return `None`

---

### Requirement: Secured contact info entries

The `shared.contact_info` table SHALL have a `secured BOOLEAN NOT NULL DEFAULT false` column. Entries with `secured = true` contain sensitive credentials (passwords, tokens, API keys) that MUST be masked in dashboard API responses.

#### Scenario: Secured entry masked in API response

- **WHEN** `GET /api/contacts/{id}` returns a contact with a `contact_info` entry that has `secured = true`
- **THEN** the `value` field in the response MUST be replaced with a masked string (e.g., `"********"`)

#### Scenario: Secured entry revealed via dedicated endpoint

- **WHEN** `GET /api/contacts/{id}/secrets/{info_id}` is called for a secured `contact_info` entry
- **THEN** the actual `value` MUST be returned in the response

#### Scenario: Non-secured entries shown normally

- **WHEN** a `contact_info` entry has `secured = false`
- **THEN** the `value` field MUST be returned as-is in all API responses

#### Scenario: MCP tools can read secured values

- **WHEN** a butler daemon queries `shared.contact_info` for credential resolution
- **THEN** the query MUST return the actual `value` regardless of the `secured` flag (no masking at the DB layer)

---

### Requirement: Owner credential migration from secrets to contact_info

Owner channel identifiers and credentials currently stored in `butler_secrets` SHALL be migrated to `shared.contact_info` entries linked to the owner contact. The migration SHALL map secret keys to contact_info types as follows:

| Secret key | contact_info type | secured |
|---|---|---|
| `BUTLER_TELEGRAM_CHAT_ID` | `telegram` | false |
| `USER_EMAIL_ADDRESS` | `email` | false |
| `USER_EMAIL_PASSWORD` | `email_password` | true |
| `GOOGLE_REFRESH_TOKEN` | `google_oauth_refresh` | true |
| `TELEGRAM_API_HASH` | `telegram_api_hash` | true |
| `TELEGRAM_API_ID` | `telegram_api_id` | true |
| `TELEGRAM_USER_SESSION` | `telegram_user_session` | true |
| `USER_TELEGRAM_TOKEN` | `telegram_bot_token` | true |

#### Scenario: Secrets migrated during Alembic upgrade

- **WHEN** the Alembic migration runs and `butler_secrets` contains `BUTLER_TELEGRAM_CHAT_ID = '12345'`
- **THEN** the migration MUST create a `contact_info` entry with `type='telegram'`, `value='12345'`, `secured=false` linked to the owner contact

#### Scenario: Secured credentials marked correctly

- **WHEN** `USER_EMAIL_PASSWORD` exists in `butler_secrets`
- **THEN** the migrated `contact_info` entry MUST have `secured = true`

#### Scenario: Credential resolution falls back to butler_secrets

- **WHEN** `credential_store.resolve('TELEGRAM_CHAT_ID')` is called
- **THEN** the resolver MUST first check the owner contact's `contact_info` for a `type='telegram'` entry
- **AND** if not found, MUST fall back to querying `butler_secrets` for the legacy key

---

### Requirement: Temporary contact for unknown senders

When a message arrives from an unknown channel identifier (reverse-lookup returns null), the system SHALL create a temporary contact with `metadata` containing `{"needs_disambiguation": true, "source_channel": "<type>", "source_value": "<value>"}` and a corresponding `contact_info` entry linking the channel identifier.

#### Scenario: Unknown Telegram sender creates temporary contact

- **WHEN** a message arrives from Telegram chat ID `55555` and no `contact_info` entry exists for `('telegram', '55555')`
- **THEN** the system MUST create a new contact with `metadata.needs_disambiguation = true`
- **AND** the contact MUST have a `contact_info` entry with `type='telegram'` and `value='55555'`
- **AND** the contact's `name` MUST be set from available channel metadata (e.g., Telegram display name) or `"Unknown (telegram 55555)"`

#### Scenario: Temporary contact has associated entity

- **WHEN** a temporary contact is created
- **THEN** the system MUST also create a memory entity via `entity_create` and link it via `entity_id` on the contact
- **AND** subsequent facts from this sender MUST be stored against this `entity_id`

#### Scenario: Repeated messages from same unknown sender reuse temporary contact

- **WHEN** a second message arrives from the same unknown Telegram chat ID `55555`
- **THEN** the system MUST NOT create a new temporary contact
- **AND** the existing temporary contact MUST be resolved via the `contact_info` entry

---

### Requirement: Temporary contact disambiguation

Temporary contacts (those with `metadata.needs_disambiguation = true`) SHALL be resolvable through the dashboard. The owner can merge a temporary contact into an existing contact, confirm it as a new contact, or archive it.

#### Scenario: Merge temporary contact into existing

- **WHEN** the owner merges temporary contact T into existing contact C via the dashboard
- **THEN** all `contact_info` entries from T MUST be moved to C
- **AND** if both T and C have `entity_id` values, `entity_merge` MUST be called to re-point all facts from T's entity to C's entity
- **AND** temporary contact T MUST be deleted
- **AND** the `needs_disambiguation` flag MUST be cleared

#### Scenario: Confirm temporary contact as new

- **WHEN** the owner confirms temporary contact T as a new contact via the dashboard
- **THEN** the `needs_disambiguation` flag MUST be removed from T's metadata
- **AND** the owner MAY optionally update T's name and other fields

#### Scenario: Archive temporary contact

- **WHEN** the owner archives temporary contact T via the dashboard
- **THEN** T's `listed` column MUST be set to `false`
- **AND** subsequent messages from T's channel identifier MUST still resolve to T (not create a new temp contact)

---

### Requirement: Owner notification for unknown senders

When a temporary contact is created for an unknown sender, the system SHALL notify the owner via their preferred channel with a message identifying the sender and providing a link to resolve the identity.

#### Scenario: Owner notified of new unknown sender

- **WHEN** a temporary contact is created for an unknown Telegram sender with display name "Chloe L"
- **THEN** the owner MUST receive a notification: "Received a message from Chloe L (Telegram). Who is this? Resolve at /butlers/contacts/{temp_contact_id}"

#### Scenario: No notification for repeated messages from known temporary contact

- **WHEN** a second message arrives from an already-created temporary contact
- **THEN** the owner MUST NOT receive another disambiguation notification

---

### Requirement: Contacts sync preserves roles and secured fields

The Google Contacts sync module MUST NOT overwrite the `roles` column or `secured` flag on `contact_info` entries during sync operations. These fields are owner-managed and MUST be preserved during upserts.

#### Scenario: Google sync does not overwrite roles

- **WHEN** the Google Contacts sync runs and updates a contact that has `roles = ['owner']`
- **THEN** the sync MUST NOT modify the `roles` column

#### Scenario: Google sync does not overwrite secured flag

- **WHEN** the Google Contacts sync runs and a `contact_info` entry has `secured = true`
- **THEN** the sync MUST NOT change the `secured` flag to `false`

---

### Requirement: I/O model removal

The `user_*/bot_*` tool naming convention, `ToolIODescriptor` dataclass, and all four `Module` ABC methods (`user_inputs`, `user_outputs`, `bot_inputs`, `bot_outputs`) SHALL be removed. Tool names SHALL revert to plain `<channel>_<action>` format (e.g., `telegram_send_message`, `email_send_message`). The `_validate_tool_name()`, `_validate_module_io_descriptors()`, `_is_user_send_or_reply_tool()`, `_with_default_gated_user_outputs()`, `_CHANNEL_EGRESS_ACTIONS`, and `ModuleToolValidationError` SHALL be removed from the daemon.

#### Scenario: Tool registered with plain name

- **WHEN** the Telegram module registers a send tool
- **THEN** the tool MUST be named `telegram_send_message` (not `user_telegram_send_message` or `bot_telegram_send_message`)

#### Scenario: Module ABC no longer requires descriptor methods

- **WHEN** a module class implements the `Module` ABC
- **THEN** it MUST NOT be required to implement `user_inputs()`, `user_outputs()`, `bot_inputs()`, or `bot_outputs()`

#### Scenario: Legacy tool names rejected

- **WHEN** a tool call uses a legacy `user_*` or `bot_*` prefixed name
- **THEN** the daemon MUST log a warning with the legacy name and the new name
- **AND** the call MUST fail with an error indicating the tool name has changed

---

### Requirement: Secret key renames

Owner-identity secret keys SHALL be renamed for consistency. The following renames MUST be applied:

| Old key | New key |
|---|---|
| `BUTLER_TELEGRAM_CHAT_ID` | `TELEGRAM_CHAT_ID` |

Secret keys that are not identity-bound (API keys, webhook URLs, service tokens) SHALL remain in `butler_secrets` and are not affected by this change.

#### Scenario: Legacy secret key resolves via fallback

- **WHEN** code references `BUTLER_TELEGRAM_CHAT_ID` and the key has been migrated to `TELEGRAM_CHAT_ID`
- **THEN** the credential resolver MUST check the new key first, then fall back to the legacy key during the transition period
