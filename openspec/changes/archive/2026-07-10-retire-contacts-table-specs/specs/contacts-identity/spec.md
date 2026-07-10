## REMOVED Requirements

### Requirement: Contacts table in public schema
**Reason**: `public.contacts` was dropped by `core_134_drop_public_contacts`. The identity anchor is now `public.entities` (see the `entity-identity` spec, "Entities table in public schema"). The retirement is guarded by `tests/contracts/test_contacts_schema_retired.py`.
**Migration**: Use `public.entities` as the cross-butler identity anchor. Non-secret channel identifiers live in `relationship.entity_facts`; secrets live in `public.entity_info`.

### Requirement: Roles sourced from entity (not contacts)
**Reason**: The `public.contacts.roles` column and the contacts table it lived on are dropped (`core_134`). Roles are authoritatively defined on `public.entities.roles`.
**Migration**: See the `entity-identity` spec, "Roles column on entities" and "Role lookup via entity JOIN".

### Requirement: Role modification restricted to dashboard API
**Reason**: The write-authority contract (roles editable only through the dashboard, never via runtime MCP tools) is authoritatively specified against entities, not the dropped contacts table.
**Migration**: See the `entity-identity` spec, "Roles not exposed to runtime MCP tools" (its "Dashboard API can update entity roles" scenario covers the live `PATCH /api/contacts/{id}` -> `UPDATE public.entities.roles` path in `roster/relationship/api/router.py`).

### Requirement: Owner entity and contact bootstrap on first startup
**Reason**: Bootstrap is entity-first and the contact half of the flow targeted the dropped `public.contacts` table.
**Migration**: See the `entity-identity` spec, "Entity-first owner bootstrap" and "Owner entity singleton".

### Requirement: Contacts must always link to an entity
**Reason**: The invariant governed rows in the dropped `public.contacts` table.
**Migration**: See the `entity-identity` spec, "Entity-first data model" (the Entity -> Contact -> Contact Details hierarchy and the entity-link invariant).

### Requirement: Contact info uniqueness constraint
**Reason**: `public.contact_info` was dropped by `core_115_drop_contact_info`; its `UNIQUE(type, value)` constraint no longer exists.
**Migration**: Non-secret channel identifiers are stored as `relationship.entity_facts` triples (see the `relationship-facts` spec); secret credentials use `public.entity_info` with its `UNIQUE(entity_id, type)` constraint (see the `entity-identity` spec, "Entity info table for per-entity properties and credentials").

### Requirement: Foreign key from contact_info to contacts
**Reason**: Both `public.contact_info` and `public.contacts` are dropped (`core_115` / `core_134`), so the cross-table foreign key no longer exists.
**Migration**: Channel identifiers attach to entities via `relationship.entity_facts` (keyed by `entity_id`) and `public.entity_info` (FK to `public.entities`).

### Requirement: Reverse-lookup from channel identifier to contact
**Reason**: The `resolve_contact_by_channel(type, value)` function no longer queries `public.contact_info JOIN public.contacts`; it resolves an entity via `relationship.entity_facts` joined to `public.entities` and always returns `contact_id = None` (`src/butlers/identity.py`).
**Migration**: See the `entity-identity` spec, "Role lookup via entity JOIN" (the resolver's live contract), and the `relationship-facts` spec for the `entity_facts` identifier model.

### Requirement: Secured contact info entries
**Reason**: The `public.contact_info.secured` column is gone with the dropped table. Secrets are now a dedicated store.
**Migration**: See the `entity-identity` spec, "Entity info table for per-entity properties and credentials" (`public.entity_info` with `secured = true`, masked in API responses).

### Requirement: Owner credential migration from secrets to contact_info
**Reason**: This was a one-time transitional migration into the now-dropped `public.contact_info` table.
**Migration**: Owner credentials resolve from `public.entity_info` (secrets) and `butler_secrets`; owner channel identifiers resolve from `relationship.entity_facts`. No contact_info fallback remains.

### Requirement: Telegram-specific contact_info types
**Reason**: These `contact_info` types described rows in the dropped `public.contact_info` table.
**Migration**: Telegram identifiers are stored as `relationship.entity_facts` triples; the Telegram handle is stored prefixed `telegram:<id>` under the `has-handle` predicate (`src/butlers/identity.py`). See the `entity-identity` spec, "Entity info type registry" for the credential types (`telegram_api_id`, `telegram_api_hash`, `telegram_user_session`).

### Requirement: Cross-provider contact disambiguation
**Reason**: The disambiguation scenarios were phrased over `public.contact_info` / `public.contacts` rows that no longer exist.
**Migration**: Cross-provider identity matching now routes through `public.entities` and `relationship.entity_facts`. See the `module-contacts` spec, "Identity Resolution Pipeline" and "Cross-Provider Contact Backfill".

### Requirement: Temporary contact for unknown senders
**Reason**: Unknown senders no longer create a temporary row in the dropped `public.contacts` table; they create a transitory entity.
**Migration**: See the `entity-identity` spec, "Transitory entity convention via metadata.unidentified" (its "Transitory entity created by contacts system" scenario covers the unknown-sender path).

### Requirement: Temporary contact disambiguation
**Reason**: Resolution of temporary contacts operated on the dropped `public.contacts` table.
**Migration**: See the `entity-identity` spec, "Transitory entity convention via metadata.unidentified" (promote, merge, and delete scenarios for transitory entities).

### Requirement: Owner notification for unknown senders
**Reason**: The trigger for this behavior was creation of a temporary contact row in the dropped `public.contacts` table.
**Migration**: Unknown-sender surfacing is now the transitory-entity convention in the `entity-identity` spec. If owner notification on first contact from an unknown sender is still desired, it should be re-specified against transitory entities (see follow-up).

### Requirement: Contacts sync preserves entity roles and secured fields
**Reason**: The "do not overwrite secured flag" half referenced `public.contact_info.secured`, now dropped. Entity `roles` are never a sync-owned field.
**Migration**: Roles are authoritative on `public.entities` and are not writable by sync or runtime MCP tools (see the `entity-identity` spec, "Roles not exposed to runtime MCP tools"). Secret preservation is governed by `public.entity_info` (`secured = true`).
