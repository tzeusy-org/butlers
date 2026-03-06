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

### Requirement: Roles sourced from entity (not contacts)

Identity roles (e.g., `'owner'`) are stored on `shared.entities.roles`, NOT on `shared.contacts.roles`. The `contacts.roles` column is retained during the transition period but is no longer the source of truth. All role lookups MUST JOIN through `shared.entities` via `contacts.entity_id`. See the `entity-identity` spec for the authoritative roles definition.

#### Scenario: Contact roles resolved via entity JOIN

- **WHEN** a system component needs to determine a contact's roles
- **THEN** it MUST query `COALESCE(e.roles, '{}')` via `LEFT JOIN shared.entities e ON e.id = c.entity_id`
- **AND** MUST NOT read roles directly from `c.roles`

---

### Requirement: Role modification restricted to dashboard API

Entity roles SHALL only be modifiable through the dashboard API (authenticated HTTP endpoints). Butler runtime instances (MCP tool calls from LLM CLI sessions) MUST NOT be able to modify roles. The `entity_create` and `entity_update` MCP tools MUST NOT expose `roles` as a writable field.

#### Scenario: MCP tool attempts to set roles

- **WHEN** a runtime instance calls `entity_update(entity_id, roles=['owner'])`
- **THEN** the tool MUST ignore the `roles` field and NOT modify it

#### Scenario: Dashboard API updates roles

- **WHEN** `PATCH /api/contacts/{id}` is called with `{"roles": ["owner", "family"]}` from an authenticated dashboard session
- **THEN** the linked entity's `roles` column MUST be updated to `['owner', 'family']` via `UPDATE shared.entities SET roles = $1 WHERE id = $2`
- **AND** the response MUST include the updated roles

---

### Requirement: Owner entity and contact bootstrap on first startup

When any butler daemon starts, it SHALL follow entity-first bootstrap:
1. Create an owner entity in `shared.entities` with `roles=['owner']`, `tenant_id='shared'`, `entity_type='person'` (if `shared.entities` exists).
2. Create an owner contact in `shared.contacts` linked to the entity via `entity_id`.

The operation MUST be idempotent across concurrent butler startups. See the `entity-identity` spec for the authoritative entity bootstrap definition.

#### Scenario: First butler starts with empty tables

- **WHEN** the first butler daemon starts and both `shared.entities` and `shared.contacts` contain no rows
- **THEN** the daemon MUST create an entity with `roles = ['owner']` in `shared.entities`
- **AND** MUST create a contact with `name = 'Owner'` linked to the entity via `entity_id`
- **AND** the contact MUST have no `contact_info` entries

#### Scenario: Entities table not ready

- **WHEN** a butler daemon starts and `shared.entities` does not yet exist
- **THEN** the daemon MUST fall back to creating a contact without entity link
- **AND** the contact MUST still have `roles = ['owner']` (backward compat)

#### Scenario: Multiple butlers start concurrently

- **WHEN** three butler daemons start simultaneously and no owner exists
- **THEN** exactly one owner entity and one owner contact MUST be created (no duplicates)

#### Scenario: Owner already exists

- **WHEN** a butler daemon starts and the owner entity already exists
- **THEN** the daemon MUST NOT create a new entity or contact
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

The system SHALL provide a `resolve_contact_by_channel(type, value)` function that returns the contact and their role-set for a given channel identifier. The function MUST query `shared.contact_info JOIN shared.contacts LEFT JOIN shared.entities` to resolve roles from the entity.

#### Scenario: Known channel identifier resolves to contact

- **WHEN** `resolve_contact_by_channel('telegram', '12345')` is called
- **AND** a `contact_info` entry exists linked to contact "Chloe" whose entity has `roles = []`
- **THEN** the function MUST return the contact's `id`, `name`, `roles` (from entity), and `entity_id`

#### Scenario: Owner channel identifier resolves with owner role

- **WHEN** `resolve_contact_by_channel('telegram', '99999')` is called
- **AND** the contact_info entry is linked to the owner contact whose entity has `roles = ['owner']`
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

### Requirement: Telegram-specific contact_info types

The `shared.contact_info` table supports Telegram-specific channel identifiers alongside existing types (email, phone, telegram). These types enable identity resolution for contacts sourced from both the Telegram user-client connector (message routing) and the Contacts module's TelegramContactsProvider (address book sync).

| contact_info type | Description | Example value | Stable? |
|---|---|---|---|
| `telegram_user_id` | Numeric Telegram user ID | `"123456789"` | Yes (permanent, survives username changes) |
| `telegram_username` | Telegram @handle (without `@` prefix) | `"alice"` | No (user can change username) |
| `telegram_chat_id` | Private chat ID for DMs with this contact | `"987654321"` | Yes (stable for a given DM pair) |

#### Scenario: Telegram user ID stored as contact_info

- **WHEN** a contact is synced from Telegram with user ID `123456789`
- **THEN** a `contact_info` entry is created with `type = 'telegram_user_id'` and `value = '123456789'`
- **AND** this entry is the stable identifier for cross-provider resolution (survives username changes)

#### Scenario: Telegram username stored as contact_info

- **WHEN** a Telegram contact has username `@alice`
- **THEN** a `contact_info` entry is created with `type = 'telegram_username'` and `value = 'alice'` (without `@` prefix)
- **AND** this entry is updated on subsequent syncs if the username changes

#### Scenario: Telegram chat ID stored as contact_info

- **WHEN** a private DM chat exists with a Telegram contact
- **THEN** a `contact_info` entry is created with `type = 'telegram_chat_id'` and `value = '<chat_id>'`
- **AND** this is used by the Switchboard for reverse-lookup routing of inbound Telegram messages

#### Scenario: Reverse-lookup by telegram_user_id

- **WHEN** `resolve_contact_by_channel('telegram_user_id', '123456789')` is called
- **AND** a `contact_info` entry exists with that type and value
- **THEN** the function returns the linked contact with roles resolved from the entity

#### Scenario: Uniqueness constraint applies to Telegram types

- **WHEN** a `contact_info` entry with `type = 'telegram_user_id'` and `value = '123456789'` exists for contact A
- **AND** an attempt is made to insert the same type and value for contact B
- **THEN** the insert fails with a unique constraint violation (same as all contact_info types)

#### Scenario: Relationship between telegram and telegram_chat_id types

- **WHEN** the existing `type = 'telegram'` contact_info entry stores a chat ID (legacy from `BUTLER_TELEGRAM_CHAT_ID` migration)
- **THEN** the legacy `telegram` type is equivalent to `telegram_chat_id` for reverse-lookup purposes
- **AND** new Telegram contact syncs use the more specific `telegram_chat_id` type
- **AND** the legacy `telegram` type remains valid for backward compatibility

---

### Requirement: Cross-provider contact disambiguation

When contacts arrive from multiple providers (e.g., Google Contacts and Telegram), the identity resolution pipeline in `ContactBackfillResolver` determines whether they represent the same person. The resolution order (`source_link → email → phone → name`) already supports multi-provider merging. This section documents the specific cross-provider scenarios.

#### Scenario: Google and Telegram contact merge by phone number

- **WHEN** a Google-sourced contact "Alice Smith" has phone `+15550100` in `shared.contact_info`
- **AND** a Telegram contact "Alice" has the same phone number `+15550100`
- **THEN** the resolver matches them via phone strategy (step 3)
- **AND** the Telegram sync adds `telegram_user_id`, `telegram_username`, and `telegram_chat_id` entries to the existing contact's `contact_info`
- **AND** the existing Google-sourced fields (email, address, etc.) are preserved
- **AND** a second `contacts_source_links` row is created with `provider = "telegram"`

#### Scenario: Cross-provider merge does not overwrite provider-owned fields

- **WHEN** a CRM contact has `display_name = "Alice Smith"` sourced from Google (provenance: `{"source": "google"}`)
- **AND** Telegram sync provides `display_name = "Alice"` for the same contact
- **THEN** the display name is NOT overwritten (Google owns the field)
- **AND** the Telegram-provided name is recorded in metadata for reference but does not replace the canonical value

#### Scenario: Ambiguous name match skips auto-merge

- **WHEN** a Telegram contact named "John" has no phone or email
- **AND** three existing CRM contacts have names matching "John" (from Google or manual entry)
- **THEN** auto-merge is skipped (ambiguous name returns multiple candidates)
- **AND** the Telegram contact is created as a new CRM record
- **AND** the dashboard shows a disambiguation prompt for the owner to manually resolve

#### Scenario: Email-based cross-provider match

- **WHEN** a future provider (e.g., Apple Contacts) provides a contact with email `alice@example.com`
- **AND** an existing CRM contact (from Google) has the same email in `shared.contact_info`
- **THEN** the resolver matches them via email strategy (step 2)
- **AND** provider-specific contact_info entries are added alongside existing entries

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

### Requirement: Contacts sync preserves entity roles and secured fields

The Google Contacts sync module MUST NOT overwrite entity `roles` or `secured` flag on `contact_info` entries during sync operations. These fields are owner-managed and MUST be preserved during upserts.

#### Scenario: Google sync does not overwrite entity roles

- **WHEN** the Google Contacts sync runs and updates a contact linked to an entity with `roles = ['owner']`
- **THEN** the sync MUST NOT modify the entity's `roles` column

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
