# Ingestion Priority Contacts

## Purpose
Provides the structured `priority_contacts` table and associated CRUD API that supersede the flat-file `GMAIL_KNOWN_CONTACTS_PATH` env-var mechanism for marking specific contacts as priority for a butler's ingestion routing. Priority contacts are an FK join table over `public.contacts`, scoped per butler, with full audit trail. This capability owns the priority-contact data model and policy-evaluator wiring; the canonical contact identity model itself lives in the `contacts-identity` capability.

## ADDED Requirements

### Requirement: Priority contacts data model
The system SHALL store priority-contact assignments in a `priority_contacts` table in the `public` schema. The table SHALL be a foreign-key join over `public.contacts(id)` to preserve referential integrity and SHALL be cascade-deleted when the underlying contact is removed.

The table schema:
- `contact_id` UUID NOT NULL REFERENCES `public.contacts(id)` ON DELETE CASCADE
- `butler` TEXT NOT NULL
- `added_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `added_by` TEXT NOT NULL
- PRIMARY KEY (`contact_id`, `butler`)

Indexes:
- PRIMARY KEY on (`contact_id`, `butler`)
- `(butler)` for per-butler lookup

#### Scenario: Priority contact insert
- **WHEN** a row is inserted with `contact_id` matching an existing `public.contacts(id)` and `butler = 'gmail'`
- **THEN** the row is persisted with the composite primary key (`contact_id`, `butler`)

#### Scenario: Contact deletion cascades
- **WHEN** a row in `public.contacts` is deleted
- **THEN** all matching rows in `priority_contacts` are deleted by the ON DELETE CASCADE constraint

#### Scenario: Duplicate insert rejected
- **WHEN** a row with the same (`contact_id`, `butler`) is inserted twice
- **THEN** the second insert fails on the primary key constraint

### Requirement: Priority contacts REST API
The dashboard API SHALL expose CRUD endpoints at `/api/ingestion/priority-contacts` for managing priority-contact assignments. The API SHALL NOT accept writes to `public.contacts.roles` through this endpoint; role mutations remain the sole responsibility of `PATCH /api/contacts`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/priority-contacts` | List priority contacts. Optional query params: `butler`. |
| POST | `/priority-contacts` | Add a priority contact assignment. Body: `{contact_id, butler}`. Returns 201. |
| DELETE | `/priority-contacts/{contact_id}/{butler}` | Remove a priority contact assignment. Returns 204. |

#### Scenario: List priority contacts for a butler
- **WHEN** GET `/api/ingestion/priority-contacts?butler=gmail` is called
- **THEN** the response lists all priority-contact rows where `butler = 'gmail'`
- **AND** each row joins through `public.contacts` to include the canonical contact name

#### Scenario: Add priority contact
- **WHEN** POST `/api/ingestion/priority-contacts` is called with `{contact_id: <uuid>, butler: 'gmail'}`
- **THEN** a row is inserted with `added_by` set to the authenticated actor and `added_at` set to NOW()
- **AND** the API returns 201

#### Scenario: Remove priority contact
- **WHEN** DELETE `/api/ingestion/priority-contacts/<contact_id>/gmail` is called
- **THEN** the matching row is deleted
- **AND** the API returns 204

#### Scenario: Roles writes are prohibited
- **WHEN** any priority-contacts endpoint is called with a payload that includes a `roles` field
- **THEN** the API returns 400
- **AND** an error message directs the caller to `PATCH /api/contacts` for role mutations

### Requirement: Audit emission for priority contact mutations
Every mutation on the priority-contacts surface (POST add, DELETE remove) SHALL emit `audit.append()` to `public.audit_log` with `actor`, `action`, `target`, `reason`, and `request_id`. Audit entries SHALL be retained indefinitely and SHALL NOT be deleted.

#### Scenario: Add emits audit entry
- **WHEN** a priority contact is added via POST
- **THEN** an audit entry is written with `action = 'ingestion.priority_contact.add'`, `target = '<contact_id>:<butler>'`, the originating `actor`, and the originating `request_id`

#### Scenario: Remove emits audit entry
- **WHEN** a priority contact is removed via DELETE
- **THEN** an audit entry is written with `action = 'ingestion.priority_contact.remove'`, `target = '<contact_id>:<butler>'`, the originating `actor`, and the originating `request_id`

### Requirement: Indefinite retention
The `priority_contacts` table SHALL have no TTL or automatic expiry. Entries persist indefinitely until explicitly removed via the DELETE endpoint or until the underlying contact in `public.contacts` is deleted (cascade).

#### Scenario: No TTL job exists
- **WHEN** the migration creates the `priority_contacts` table
- **THEN** no scheduled job, trigger, or background task is configured to delete or expire rows by age

### Requirement: GmailPolicyEvaluator wiring
The Gmail connector's policy evaluation path SHALL load priority contacts from the `priority_contacts` table (filtered by `butler = 'gmail'`) via a DB query with a 15-minute TTL cache. The evaluator SHALL use the resolved contact's `contact_info` rows to recognize incoming sender identities as priority.

#### Scenario: Evaluator loads priority contacts from DB
- **WHEN** the `GmailPolicyEvaluator` initializes or refreshes its cache
- **THEN** it queries `priority_contacts` joined to `public.contact_info` for all rows where `butler = 'gmail'`
- **AND** it builds an in-memory set of sender identities recognized as priority

#### Scenario: Cache TTL is 15 minutes
- **WHEN** the evaluator's cache age exceeds 15 minutes
- **AND** the evaluator is asked to classify a message
- **THEN** the cache is refreshed from the DB before the classification returns

#### Scenario: Fail-open on DB error
- **WHEN** the DB is unreachable during a cache refresh
- **THEN** the evaluator retains its previous cache and logs a warning
- **AND** classification continues to operate against the last-known cache

### Requirement: GMAIL_KNOWN_CONTACTS_PATH deprecation
The `GMAIL_KNOWN_CONTACTS_PATH` env-var flat-file mechanism SHALL be deprecated when `GmailPolicyEvaluator` is wired to the DB-backed lookup. Deprecation proceeds in two phases:

1. **DB-wiring bead** (Wave 2): the evaluator switches to DB-primary lookup with a one-cycle fallback to the flat file (DB results take precedence on conflict). The DB-wiring bead documents the deprecation and references the follow-up cleanup bead.
2. **Cleanup bead** (Wave 3, after one deploy cycle has been measured): the env var, its reader, and the flat-file code path are removed from the Gmail module.

Both beads MUST be filed before the DB-wiring bead is started, with the cleanup bead carrying a `discovered-from` dependency on the DB-wiring bead.

#### Scenario: One-cycle fallback during deploy
- **WHEN** the bead that wires the DB lookup is in its initial deploy
- **THEN** the evaluator MAY fall back to reading `GMAIL_KNOWN_CONTACTS_PATH` if the DB query returns zero rows
- **AND** DB results SHALL take precedence on any conflict

#### Scenario: Env var removed post-cutover by cleanup bead
- **WHEN** the cleanup bead executes (Wave 3, after one deploy cycle has been measured stable)
- **THEN** the env var, its reader, and the flat-file code path are removed from the Gmail module
- **AND** subsequent boots ignore `GMAIL_KNOWN_CONTACTS_PATH` entirely

#### Scenario: Both deprecation beads exist before DB-wiring starts
- **WHEN** the GmailPolicyEvaluator DB-wiring bead is filed
- **THEN** a matching cleanup bead is filed concurrently with a `discovered-from` link to the DB-wiring bead
- **AND** the DB-wiring bead description references the cleanup bead's id

### Requirement: Cascade-delete emits audit entry
When a row in `public.contacts` is deleted and the FK cascade removes one or more `priority_contacts` rows, the system SHALL emit an audit entry per cascaded butler so the priority-contact removal is observable on the audit trail. Implementation MAY use a row-level AFTER DELETE trigger on `priority_contacts` that writes one `audit.append()` per affected row.

#### Scenario: Cascade emits audit entry per butler
- **WHEN** a contact is removed from `public.contacts` that has priority-contact rows for two butlers
- **THEN** two audit entries are written, one per butler
- **AND** each entry uses `action = 'ingestion.priority_contact.cascade_remove'`, `target = '<contact_id>:<butler>'`, `actor = 'system:contact_cascade'`, and a `reason` of `'contact removed from public.contacts'`

### Requirement: No credentials in priority-contact API responses
The priority-contacts API SHALL NOT return any credential, token, secret, or otherwise sensitive value in response bodies. Contact identity fields surfaced via this endpoint SHALL be limited to the canonical contact name and non-sensitive `contact_info` channel identifiers.

#### Scenario: Response body has no secrets
- **WHEN** GET `/api/ingestion/priority-contacts` returns a list
- **THEN** no response field contains a credential, token, OAuth refresh token, or any value where `contact_info.secured = true`
