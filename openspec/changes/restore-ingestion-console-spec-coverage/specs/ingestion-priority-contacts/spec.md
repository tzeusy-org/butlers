# Ingestion Priority Contacts

## Purpose

Defines the cross-butler priority-contact store: the `public.priority_contacts`
table, its REST surface under `/api/ingestion/priority-contacts`, the audit
trail over its mutations, and its retention. A priority contact is an identity
whose inbound traffic must never be filtered out by an ingestion policy. The
store is deliberately global rather than per-butler: priority is a property of
the person, not of which butler happens to receive them.

The store is data only. Its sole consumer, the Gmail policy evaluator, and the
cache contract that governs how it reads this table, are specified in
`connector-gmail` (`Policy Tier Assignment`) rather than here, so the evaluator
contract lives beside the evaluator.

## ADDED Requirements

### Requirement: Priority contacts data model

The system SHALL maintain a cross-butler table `public.priority_contacts` with
columns:

- `contact_id UUID PRIMARY KEY` — the identity's entity UUID for rows written
  since the entity migration
- `entity_id UUID REFERENCES public.entities(id) ON DELETE SET NULL` — the
  canonical entity anchor, indexed by a partial index over non-NULL values
- `added_at TIMESTAMPTZ NOT NULL DEFAULT now()`
- `added_by TEXT NOT NULL`

The table SHALL be butler-agnostic: there SHALL be no `butler` column and no
per-butler dimension in the primary key, so a contact is either a priority
contact or it is not. It SHALL NOT carry a foreign key to `public.contacts`;
that table has been dropped and identity resolution is anchored on
`public.entities`. New rows SHALL be written with `contact_id = entity_id` so
the display-name join resolves. Legacy rows MAY carry a `contact_id` that is
not an entity UUID with `entity_id` backfilled separately, and every read path
SHALL tolerate `entity_id IS NULL`.

Channel identifiers (email, phone, handles) SHALL NOT be stored on this table.
They SHALL be read from `relationship.entity_facts` through the row's
`entity_id`, restricted to `predicate LIKE 'has-%'`, `validity = 'active'`, and
`object_kind = 'literal'`.

#### Scenario: Priority contact insert

- **WHEN** a caller adds an entity that exists in `public.entities` and is not
  already a priority contact
- **THEN** one row is inserted with `contact_id` and `entity_id` both set to
  that entity's UUID and `added_by` recorded
- **AND** `added_at` defaults to the insertion time

#### Scenario: Duplicate insert rejected

- **WHEN** a caller adds an entity that already has a priority-contact row,
  whether matched on the primary key or on `entity_id`
- **THEN** the insert is rejected and no second row is created
- **AND** the pre-existing row is left unmodified

#### Scenario: Table carries no butler dimension

- **WHEN** the table definition is inspected
- **THEN** it has no `butler` column and its primary key is `contact_id` alone

#### Scenario: Entity deletion clears the anchor without deleting the row

- **WHEN** the entity referenced by `entity_id` is deleted
- **THEN** `entity_id` is set to NULL by the foreign key
- **AND** the priority-contact row survives

### Requirement: Priority contacts REST API

The system SHALL expose a REST surface under
`/api/ingestion/priority-contacts`:

- `GET ""` — a paginated listing accepting `limit` (1–1000, default 100) and
  `offset` (≥ 0, default 0), returning the standard paginated envelope with
  `total`, `offset`, and `limit`. Each entry SHALL carry `contact_id`,
  `added_at`, `added_by`, the entity's canonical name, the contact's active
  channel values, and an `is_inert` flag. `is_inert` SHALL be true when the
  contact has no active `has-email` fact, because such a contact would silently
  match nothing at runtime — the flag exists so a misconfigured contact is
  visible rather than quietly ineffective. Entries SHALL be ordered by
  `added_at` descending.
- `POST ""` — adds a priority contact, returning HTTP 201 with `contact_id`,
  `added_at`, and `added_by`. It SHALL return HTTP 400 when the submitted id
  does not exist in `public.entities`, and HTTP 409 when the contact is already
  a priority contact.
- `DELETE "/{contact_id}"` — removes a priority contact, returning HTTP 204 on
  success and HTTP 404 when no such row exists.

The surface SHALL be butler-agnostic: no route segment or query parameter
SHALL take a butler name. Every endpoint SHALL return HTTP 503 when the shared
database pool is unavailable. A failure to read `relationship.entity_facts`
SHALL degrade the listing to empty channel values rather than failing the
request.

#### Scenario: List priority contacts

- **WHEN** a caller lists priority contacts
- **THEN** the response is the paginated envelope with `total` reflecting the
  whole table and `data` holding at most `limit` entries from `offset`
- **AND** each entry carries its canonical name and active channel values

#### Scenario: Contact with no active email is marked inert

- **WHEN** a listed contact has no active `has-email` fact
- **THEN** its `is_inert` flag is true

#### Scenario: Add priority contact

- **WHEN** a caller posts an entity UUID that exists and is not yet a priority
  contact
- **THEN** the response is HTTP 201 carrying `contact_id`, `added_at`, and
  `added_by`

#### Scenario: Add rejects an unknown entity

- **WHEN** a caller posts a UUID absent from `public.entities`
- **THEN** the response is HTTP 400 and no row is inserted

#### Scenario: Remove priority contact

- **WHEN** a caller deletes an existing priority contact
- **THEN** the response is HTTP 204 and the row is gone

#### Scenario: Remove of an absent contact is 404

- **WHEN** a caller deletes a contact id with no priority-contact row
- **THEN** the response is HTTP 404

#### Scenario: Roles writes are prohibited

- **WHEN** a caller posts a payload containing a `roles` field
- **THEN** the response is HTTP 400 directing the caller to the contacts
  endpoint
- **AND** no priority-contact row is inserted

#### Scenario: Entity-facts failure degrades rather than fails

- **WHEN** the `relationship.entity_facts` lookup raises
- **THEN** the listing still returns HTTP 200 with empty channel values

### Requirement: Audit emission for priority contact mutations

Every priority-contact mutation SHALL be recorded in `public.audit_log`. An add
SHALL write `action = 'ingestion.priority_contact.add'` and a remove SHALL
write `action = 'ingestion.priority_contact.remove'`, each with the actor, the
contact id as target, and the originating client address. Audit writes SHALL be
best-effort: a failure SHALL be logged as a warning and SHALL NOT fail the
mutation or roll back the row change, because losing the audit line is
preferable to leaving the caller unable to manage priority contacts at all.

#### Scenario: Add emits audit entry

- **WHEN** a priority contact is added
- **THEN** an audit entry is written with
  `action = 'ingestion.priority_contact.add'` and the contact id as target

#### Scenario: Remove emits audit entry

- **WHEN** a priority contact is removed
- **THEN** an audit entry is written with
  `action = 'ingestion.priority_contact.remove'` and the contact id as target

#### Scenario: Audit failure does not fail the mutation

- **WHEN** the audit append raises after the row change has committed
- **THEN** the failure is logged and the caller still receives the success
  status

### Requirement: Indefinite retention

Priority-contact rows SHALL be retained until explicitly removed. No TTL,
expiry column, or scheduled pruning job SHALL apply to
`public.priority_contacts`, and the audit entries recording its mutations SHALL
likewise be retained indefinitely — `public.audit_log` has no pruner. A contact
stops being a priority contact only by an explicit `DELETE`.

#### Scenario: No TTL job exists

- **WHEN** the retention jobs are reviewed
- **THEN** none of them targets `public.priority_contacts` or `public.audit_log`

#### Scenario: Rows survive indefinitely

- **WHEN** a priority contact is added and never removed
- **THEN** it remains a priority contact regardless of elapsed time

### Requirement: Cascade-delete emits audit entry

A row-level `AFTER DELETE` trigger on `public.priority_contacts` SHALL write an
audit entry with `actor = 'system:contact_cascade'` and
`action = 'ingestion.priority_contact.cascade_remove'`, whose target is
`contact_id` alone. The trigger function SHALL be `SECURITY DEFINER` so the
audit line is written regardless of the deleting role's grants, and its target
SHALL carry no butler suffix.

The trigger is unconditional. It therefore fires on every delete, not only on a
cascade, which means an API removal writes both
`ingestion.priority_contact.remove` and
`ingestion.priority_contact.cascade_remove` for the same row, and the
`cascade_remove` note still reads "contact removed from public.contacts" even
though `public.contacts` has since been dropped and no cascade path from it
remains. This is recorded as the shipped behaviour so a reader auditing the log
is not misled into treating a `cascade_remove` entry as evidence of a cascade;
correcting it is deliberately out of scope for this restoration.

#### Scenario: Cascade audit entry has no butler suffix

- **WHEN** a priority-contact row is deleted
- **THEN** the trigger writes an audit entry whose target is the `contact_id`
  with no `:butler` suffix

#### Scenario: API removal produces both audit actions

- **WHEN** a priority contact is removed through the REST API
- **THEN** `public.audit_log` receives both
  `ingestion.priority_contact.remove` and
  `ingestion.priority_contact.cascade_remove` for that contact id

### Requirement: No credentials in priority-contact API responses

No priority-contact endpoint SHALL return a credential, token, secret, or OAuth
value in any response body. Response bodies SHALL be limited to the contact and
entity identifiers, the canonical name, the contact's own channel values
(email, phone, handles), the audit provenance fields `added_at` and `added_by`,
and the `is_inert` flag. Channel values are the contact's own identifiers, not
authentication material, and SHALL NOT be treated as a licence to project any
other column.

#### Scenario: Response body has no secrets

- **WHEN** any priority-contact endpoint returns, whether success or error
- **THEN** the response body contains no credential, token, secret, or OAuth
  value

#### Scenario: Projection is limited to the declared fields

- **WHEN** the listing query is reviewed
- **THEN** it projects only the declared fields and joins only
  `public.entities` and `relationship.entity_facts`

## Source References

- Non-Negotiable Rule 1 (user-federated sovereignty — priority is the owner's
  declaration about their own people, held in the owner's database)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0004 (Identity and contact resolution — `public.entities` is the anchor)
- RFC 0007 (Dashboard and API surface)
