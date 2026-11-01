## MODIFIED Requirements

### Requirement: CRM Backfill Pipeline

`ContactBackfillEngine` SHALL orchestrate identity resolution, table writing, and structured logging for each synced contact. The legacy `activity_feed` table was retired (relationship migration `010_drop_legacy_contact_tables`); the engine SHALL NOT write feed rows. Sync events (created, updated, conflict, source-deleted, ambiguous match) SHALL be emitted as structured log records carrying the entity id (when known), provider, account, and external id. Wherever this spec says an event is "logged" it means such a log record.
- For channel-fact backfill from a hosting butler other than Relationship, the engine SHALL use its configured daemon Switchboard client and the fixed registered Relationship contacts-report operation. Relationship SHALL run the central writer on its own pool and ordinary runtime role. The engine SHALL NOT pass its local pool to the Relationship library, widen grants, switch roles, use privileged connections or SECURITY DEFINER, accept a caller-selected destination, or fall back to foreign SQL. Relationship-local backfill MAY use the same owning operation locally with identical validation and fact policy.
- The target operation SHALL establish server-fixed `src=contacts-backfill` and `third_party` content authority for the report, independent of ambient context. Provider/account/source-link locators, subject UUIDs, copied source capabilities, caller context and confidence SHALL NOT establish an owner, live reporter, SYSTEM exemption, confirmation or approved executor. It SHALL preserve the central writer's candidate, owner-approval, temporal, evidence, lock, supersession and replay rules. Candidate and pending-approval results SHALL remain distinct from committed active outcomes; an acknowledged transport or local source-link commit SHALL NOT certify a channel fact as active. Failure, malformed response and timeout after possible commit SHALL remain bounded failure or uncertainty, with no new automatic retry or distributed-transaction promise. Ordinary exact replay/readback MAY resolve uncertainty under existing policy.
- The complete email, phone, URL and username order/mappings and primary flags SHALL remain unchanged, including Telegram username canonicalization and provider external-id handles; sync SHALL NOT collect Telegram chat ids. Local public-entity/source-link atomics, manually edited fields, tombstones and provider/account isolation SHALL remain intact. Child owner calls SHALL remain outside the local entity/source-link transaction and SHALL restore any temporary fact context on every exit. Safe warning work SHALL serialize with bu-y81k45 without replacing unrelated required structured events or treating privacy as role-repair proof.

ID: REQ-module-contacts-001
Source: bu-ss4o2u adopted full P0-P6 and seven outcomes; RFC0006/RFC0017; existing complete Contacts identity/backfill and Relationship candidate/authority/approval contracts
Scope: v1-mandatory

#### Scenario: New contact backfill

- **WHEN** a canonical contact has no existing match
- **THEN** a new entity is created in `public.entities`
- **AND** emails, phones, urls, and usernames are asserted as `relationship.entity_facts` triples on that entity (`has-email`, `has-phone`, `has-website`, `has-handle`)
- **AND** `addresses` rows are created for postal addresses
- **AND** `important_dates` rows are created for birthdays and anniversaries
- **AND** `labels` + `contact_labels` rows are created for group memberships
- **AND** a `contacts_source_links` provenance row is created
- **AND** a `contact_synced` event is logged

#### Scenario: Existing contact update with provenance

- **WHEN** a canonical contact matches an existing CRM contact
- **THEN** fields are updated only if they were previously source-owned (a provider-keyed provenance marker exists for that field, see "Provenance tracking across providers")
- **AND** a field with no provenance marker whose current value differs from the source is treated as a local edit and preserved (not overwritten); there is no explicit manual marker
- **AND** conflict fields emit a `contact_sync_conflict` log event
- **AND** updated fields emit a `contact_sync_updated` log event

#### Scenario: Tombstone handling (source deleted)

- **WHEN** a canonical contact has `deleted = true`
- **THEN** the `contacts_source_links` row is marked with `deleted_at`
- **AND** the CRM contact record is preserved (no hard deletes)
- **AND** a `contact_sync_deleted_source` event is logged

#### Scenario: Channel backfill uses the owning role without new privileges

- **WHEN** a schema-isolated hosting butler backfills a known contact's channel
- **THEN** its fixed Switchboard-routed Relationship operation SHALL execute the central writer on Relationship's ordinary runtime pool
- **AND** the caller's direct Relationship SQL SHALL remain denied beside a committed owning positive

#### Scenario: Candidate and parked channel reports remain incomplete

- **WHEN** the owning operation returns candidate or pending_approval
- **THEN** backfill SHALL retain that outcome distinctly and SHALL NOT count it as an active routing fact
- **AND** genuine owner adoption or approved execution SHALL retain the existing exact-row and decision policy

#### Scenario: Uncertain channel delivery does not create a fallback write

- **WHEN** a target response is unavailable, malformed, refused or times out after a possible commit
- **THEN** the engine SHALL retain bounded failure or uncertainty rather than active success or proven absence
- **AND** it SHALL issue no foreign SQL fallback or new automatic retry

#### Scenario: Channel mapping and local provenance survive owner routing

- **WHEN** Google and Telegram contacts are backfilled through the owner operation
- **THEN** all existing entry order, mappings, primary flags, username canonicalization, context reset and provider/account isolation SHALL survive
- **AND** local source-link atomics, manual edits and tombstones SHALL remain unchanged without a distributed atomicity claim

### Requirement: Identity Resolution Pipeline

`ContactBackfillResolver` SHALL resolve canonical contacts to local CRM records in priority order.
- Source-link lookup SHALL remain local and first. Foreign email and phone lookup SHALL execute through a fixed Switchboard-routed Relationship owning read, never through the hosting butler's foreign schema access. The existing registered `identity_resolve_channels` MAY be reused only where its complete normalization, eligibility, ambiguity and response semantics meet this contract; a bounded contacts-specific owning read representation SHALL cover any remaining mismatch without changing existing generic resolver consumers.
- The owning read SHALL preserve normalized primary-email lookup, followed by phones in their existing order with exact stripped and digits/plus alternatives, then conservative local name matching. It SHALL inspect the complete relevant eligible active literal-fact matches for live, non-merged, non-deleted persons; candidate reports SHALL NOT resolve. Multiple distinct eligible subjects SHALL be ambiguous, never first-match identity or owner authority. Its per-request typed response SHALL distinguish a unique eligible match, a successfully completed empty lookup, ambiguity and unavailability. This empty-lookup result SHALL NOT rewrite the separate predicate coverage contract or claim global absence. Missing target, query denial/error, incomplete, stale or malformed response SHALL be unavailable rather than a certified empty lookup; the caller SHALL NOT use it to proceed to name fallback or new-entity creation. Successful empty lookups SHALL retain the ordinary fallback/new-contact path. Returned identity, roles or copied UUID SHALL NOT authenticate a source or grant an owner bypass.

ID: REQ-module-contacts-002
Source: bu-ss4o2u adopted full P0-P6 and seven outcomes; RFC0006/RFC0017; existing complete Contacts identity/backfill and Relationship candidate/authority/approval contracts
Scope: v1-mandatory

#### Scenario: Resolution order

- **WHEN** a canonical contact is resolved
- **THEN** the following strategies are tried in order:
  1. Source link match (`provider + account_id + external_contact_id` in `contacts_source_links`)
  2. Primary email exact match on a `has-email` fact in `relationship.entity_facts`
  3. Phone exact/e164 match on a `has-phone` fact in `relationship.entity_facts`
  4. Conservative name match (ILIKE against `public.entities.canonical_name` and aliases)

#### Scenario: Owning email and phone lookup preserves resolution priority

- **WHEN** a canonical contact has no source link and a matching eligible active channel fact exists
- **THEN** the fixed owning read SHALL preserve email-before-phone order, normalized email and exact/digits phone alternatives
- **AND** a source-link match SHALL still take priority without an owner-read call

#### Scenario: Unavailable lookup cannot manufacture a new person

- **WHEN** a necessary owner lookup is denied, unavailable, incomplete or malformed
- **THEN** resolution SHALL retain unavailable rather than an empty match
- **AND** it SHALL NOT fall through to name matching or new-person creation as if lookup completed

#### Scenario: Empty and ambiguous lookups remain distinct

- **WHEN** a complete owner lookup finds zero or multiple distinct eligible subjects
- **THEN** zero SHALL preserve the ordinary fallback path while multiple subjects SHALL refuse auto-merge as ambiguous
- **AND** candidate, merged, deleted and non-person rows SHALL not become eligible matches or authentication
