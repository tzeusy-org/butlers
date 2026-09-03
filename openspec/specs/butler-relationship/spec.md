# Relationship Butler Role

## Purpose
The Relationship butler (port 41102) is a personal CRM that manages contacts, relationships, important dates, interactions, gifts, reminders, and loans.

## Requirements

### Requirement: Relationship Butler Identity and Runtime

The implementation SHALL provide the behavior described by this requirement.
The relationship butler maintains personal CRM context with 40+ domain tools.

#### Scenario: Identity and port
- **WHEN** the relationship butler is running
- **THEN** it operates on port 41102 with description "Personal CRM. Manages contacts, relationships, important dates, interactions, gifts, and reminders."
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `relationship` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the relationship butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), and `memory`

### Requirement: Relationship Butler Tool Surface

The implementation SHALL provide the behavior described by this requirement.
The relationship butler exposes a comprehensive personal CRM tool set.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** it has access to 40+ tools including: contact CRUD (`contact_create`, `contact_update`, `contact_get`, `contact_search`, `contact_archive`, `contact_resolve`), relationship management (`relationship_add`, `relationship_list`, `relationship_remove`), date tracking (`date_add`, `date_list`, `upcoming_dates`), notes (`note_create`, `note_list`, `note_search`), interactions (`interaction_log`, `interaction_list`), reminders (`reminder_create`, `reminder_list`, `reminder_dismiss`), gifts (`gift_add`, `gift_update_status`, `gift_list`), loans (`loan_create`, `loan_settle`, `loan_list`), groups (`group_create`, `group_add_member`, `group_list`, `group_members`), labels (`label_create`, `label_assign`, `contact_search_by_label`), facts (`fact_set`, `fact_list`), the registry-relational edge writer/reader (`relationship_assert_fact`, `relationship_lookup`), feed (`feed_get`), entity resolution (`entity_resolve`, `entity_create`), memory (`memory_store_fact`), and calendar tools

> NOTE: `feed_get` is specified but not yet implemented in the relationship module (no `feed_get` tool or library function exists as of this audit). It remains in scope as intent; a remediation issue tracks building it.

### Requirement: Relationship Butler Tool Surface — Dunbar Tier
The relationship butler SHALL expose Dunbar tier management and group interaction tools.

#### Scenario: Dunbar tier tool in tool inventory
- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** it MUST have access to `dunbar_tier_set(contact_id, tier)` for setting or clearing manual Dunbar tier overrides
- **AND** `contact_get` and `contact_search` responses MUST include `dunbar_tier` and `dunbar_score` fields
- **AND** it MUST have access to `interaction_log_group(group_id, direction, occurred_at, summary)` for logging interactions with all members of a contact group in a single call

### Requirement: Entity Resolution Pipeline

The implementation SHALL provide the behavior described by this requirement.
The relationship butler follows a 7-step entity resolution pipeline for person mentions.

#### Scenario: Entity resolution flow
- **WHEN** the relationship butler processes a message mentioning a person
- **THEN** it follows a 7-step pipeline: (1) identify person mentions, (2) resolve each via `entity_resolve` with context hints, (3) apply disambiguation policy (zero candidates: create entity; single candidate or top leads by 30+ points: use entity_id; multiple candidates with gap less than 30 points: ask user), (4) handle new people, (5) store facts with entity_id, (6) log interactions, (7) update domain records

### Requirement: Relationship Butler Schedules

The implementation SHALL provide the behavior described by this requirement.
The relationship butler runs date checks, maintenance sweeps, and memory jobs.

#### Scenario: Scheduled task inventory
- **WHEN** the relationship butler daemon is running
- **THEN** it executes: `upcoming-dates-check` (0 8 * * *, prompt-based: check birthdays/anniversaries in the next 7 days), `relationship-maintenance` (0 9 * * 1, prompt-based: rank overdue contacts by Dunbar tier-weighted urgency and suggest top 3 reconnections), `memory-consolidation` (0 */6 * * *, job), `memory-episode-cleanup` (0 4 * * *, job), and `insight-scan` (0 7 * * *, job: evaluate relationship domain data and generate insight candidates)

### Requirement: Relationship Butler Skills

The implementation SHALL provide the behavior described by this requirement.
The relationship butler has gift brainstorming and reconnection planning skills.

#### Scenario: Skill inventory
- **WHEN** the relationship butler operates
- **THEN** it has access to `gift-brainstorm` (personalized gift idea generation with budget tiers and gift pipeline integration) and `reconnect-planner` (Dunbar tier-aware stale contact identification and reconnection outreach planning using tier-weighted urgency ranking), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Relationship Memory Taxonomy

The implementation SHALL provide the behavior described by this requirement.
The relationship butler uses a person-centric memory taxonomy.

#### Scenario: Memory classification
- **WHEN** the relationship butler extracts facts
- **THEN** it uses the person's human-readable name as subject (with entity_id as anchor); predicates like `relationship_to_user`, `birthday`, `preference`, `current_interest`, `workplace`, `lives_in`, `dunbar_tier_override`; permanence `permanent` for identity facts and tier overrides, `stable` for workplace/location, `standard` for interests, `volatile` for temporary states

### Requirement: CRUD-to-SPO migration — relationship domain (bu-ddb.3)

The implementation SHALL provide the behavior described by this requirement.
The relationship butler migrates 9 dedicated CRUD tables to temporal SPO facts. All facts use `scope='relationship'` and `entity_id = contact_entity_id` (resolved from `public.contacts.entity_id` for each contact). Full predicate taxonomy and metadata schemas are in `openspec/changes/crud-to-spo-migration/specs/predicate-taxonomy.md`.

The relationship butler maintains TWO temporal fact stores that the CRUD-to-SPO tools route between by predicate kind. Narrative triples (interactions, notes, gifts, loans, tasks, reminders, life events, quick facts) are written to the `memory.facts` store (snake_case predicates, `scope='relationship'`) via `memory_store_fact` and the CRUD wrappers. Registry-relational edges and identity-contact predicates are written to the `relationship.entity_facts` store (kebab-case RDF-style predicates) via the central writer `relationship_assert_fact` and read via `relationship_lookup`; this store powers the relational columns and Dunbar concentration views.

#### Scenario: Contact entity resolution before fact storage
- **WHEN** any relationship CRUD-migrated tool stores a fact for a contact
- **THEN** the tool MUST resolve `contact_id → public.contacts.entity_id`
- **AND** if `public.contacts.entity_id` is NULL, it MUST call `memory_entity_create(entity_type='person', name=contact.name)` and update `public.contacts.entity_id`
- **AND** the resolved `entity_id` MUST be used as `entity_id` for the fact; the contact's canonical name MUST be used as `subject`

#### Scenario: Interaction tools as temporal fact wrappers
- **WHEN** `interaction_log` is called
- **THEN** it MUST store a fact with `predicate='interaction_{type}'`, `valid_at=occurred_at`, `entity_id=contact_entity_id`, `scope='relationship'`, `content=summary`, and `metadata={type, notes}`
- **AND** `interaction_list` MUST query facts with predicate matching `interaction_%` for the contact's entity

#### Scenario: Note tools as temporal fact wrappers (append-only)
- **WHEN** `note_create` is called
- **THEN** it MUST store a fact with `predicate='contact_note'`, `valid_at=created_at`, `entity_id=contact_entity_id`, `scope='relationship'`, and `content=note_content`
- **AND** notes MUST NOT supersede each other (append-only temporal stream)
- **AND** `note_list` and `note_search` MUST query facts with `predicate='contact_note'` for the contact entity

#### Scenario: quick_facts as dynamic-predicate property facts
- **WHEN** `fact_set` is called with a key-value pair for a contact
- **THEN** it MUST store a fact with `predicate=key`, `content=value`, `valid_at=NULL`, `entity_id=contact_entity_id`, `scope='relationship'`
- **AND** `fact_list` MUST query all active facts for the contact entity in scope `relationship` excluding interaction, note, gift, loan, task, and reminder predicates
- **AND** supersession MUST apply so that re-setting the same key replaces the previous value

#### Scenario: Gift, loan, task, reminder as property fact wrappers
- **WHEN** `gift_add`, `loan_create`, `reminder_create`, or task tools are called
- **THEN** each MUST store a fact with the corresponding predicate (`gift`, `loan`, `contact_task`, `reminder`), `valid_at=NULL`, `entity_id=contact_entity_id`, `scope='relationship'`, and the appropriate metadata
- **AND** update operations (e.g. `gift_update_status`, `loan_settle`, `reminder_dismiss`) MUST supersede the existing active fact with a new fact carrying updated metadata
- **AND** list tools MUST query facts with the corresponding predicate for the contact entity

#### Scenario: Life events and activity as temporal fact wrappers
- **WHEN** a life event is recorded
- **THEN** it MUST store a fact with `predicate='life_event'`, `valid_at=happened_at`, and `metadata={life_event_type, description}`
- **AND** `feed_get` MUST query all temporal facts (`interaction_%`, `life_event`, `contact_note`, `activity`) for a contact entity ordered by `valid_at DESC`

### Requirement: Relationship Insight Scan Job
The relationship butler's `insight-scan` job SHALL evaluate relationship domain data and produce insight candidates covering upcoming dates, stale contacts, pending gifts, and interaction milestones. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool — the butler does not write to `public.insight_candidates` directly.

#### Scenario: Insight-scan job handler registration
- **WHEN** the relationship butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates of the same category (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Upcoming date insights
- **WHEN** the insight-scan job evaluates upcoming dates
- **THEN** it SHALL generate candidates for birthdays and anniversaries occurring in the next 7 days
- **AND** dates within 1 day SHALL have priority 95 (time-critical)
- **AND** dates within 3 days SHALL have priority 80
- **AND** dates within 7 days SHALL have priority 70
- **AND** the `dedup_key` SHALL be `birthday:{contact-entity-id}:{year}` or `anniversary:{contact-entity-id}:{year}` (shared namespace for cross-butler dedup with Calendar)
- **AND** `expires_at` SHALL be the date of the event
- **AND** `cooldown_days` SHALL be 1 for dates within 1 day, 3 for dates within 3 days, 7 for dates within 7 days

#### Scenario: Stale contact insights
- **WHEN** the insight-scan job evaluates contact staleness
- **THEN** it SHALL generate candidates for contacts whose last interaction exceeds their tier-aware cadence threshold (or `stay_in_touch_days` if set)
- **AND** contacts overdue by more than 2x their cadence SHALL have priority 45
- **AND** contacts overdue by 1-2x their cadence SHALL have priority 35
- **AND** the `dedup_key` SHALL be `relationship:stale-contact:{contact-id}:{year-week}` (butler-scoped, weekly granularity)
- **AND** `expires_at` SHALL be 7 days from generation
- **AND** tier 1500 contacts without `stay_in_touch_days` SHALL be excluded

#### Scenario: Pending gift insights
- **WHEN** the insight-scan job evaluates pending gifts
- **THEN** it SHALL generate candidates for gifts with status `idea` or `purchased` that have an associated date within 14 days
- **AND** priority SHALL be 60 (informational)
- **AND** the `dedup_key` SHALL be `relationship:pending-gift:{gift-id}`
- **AND** `expires_at` SHALL be the associated date

#### Scenario: Interaction milestone insights
- **WHEN** the insight-scan job detects notable interaction milestones
- **THEN** it SHALL generate candidates for milestones such as "100th interaction with {contact}" or "1-year anniversary of first interaction with {contact}"
- **AND** priority SHALL be 30 (low-urgency nudge)
- **AND** the `dedup_key` SHALL be `relationship:milestone:{contact-id}:{milestone-type}`
- **AND** `cooldown_days` SHALL be 30
- **AND** `expires_at` SHALL be 7 days from generation

### Requirement: Stale-contact claims require one authoritative producer

Before classifying a contact as overdue, the Relationship butler MUST resolve exactly one
server-attested expected-signal producer and, for connector producers, its exact endpoint identity
for that contact. A missing, unsupported, mixed, conflicting, caller-asserted, or otherwise
unprovable source/endpoint MUST be `unmeasurable` and MUST NOT produce a stale-contact candidate,
overdue-contact result, reconnect suggestion, or scheduled relationship-maintenance nudge.

For connector producers, continued adoption SHALL persist a non-empty
`producer_endpoint_identity` on the shared expected signal and SHALL evaluate liveness by the exact
`(connector_type, endpoint_identity)` pair. Owner-produced signals SHALL keep that field null.

ID: REQ-butler-relationship-001
Source: relationship-stale-contact-producer-mapping design §§1-6; RFC 0011
Scope: v1-mandatory

#### Scenario: Gmail interaction source maps to Gmail liveness

- **WHEN** an email interaction is server-attested by the passive interaction writer and the
  contact has corroborating active email identity evidence
- **THEN** its sole expected-signal producer MUST be `connector:gmail` bound to the exact
  server-derived Gmail endpoint identity that received the interaction
- **AND** no Telegram, WhatsApp, Discord, calendar, or generic connector health may authorize it
- **AND** another healthy Gmail endpoint MUST NOT authorize it

#### Scenario: Telegram user-client remains distinct from Telegram bot

- **WHEN** a Telegram user-client interaction is server-attested and the contact has a
  corroborating active `telegram:<id>` identity
- **THEN** its sole expected-signal producer MUST be `connector:telegram_user_client`
- **AND** it MUST be bound to the exact server-derived Telegram user-client endpoint identity
- **AND** `connector:telegram_bot` MUST NOT authorize the signal, even when that bot is healthy
- **AND** another healthy Telegram user-client endpoint MUST NOT authorize it

#### Scenario: WhatsApp user-client uses canonical identity corroboration

- **WHEN** a WhatsApp user-client interaction is server-attested and the contact resolved through
  an exact WhatsApp JID identity or the canonical E.164 phone fallback
- **THEN** its sole expected-signal producer MUST be `connector:whatsapp_user_client` bound to the
  exact server-derived WhatsApp endpoint identity
- **AND** another healthy WhatsApp user-client endpoint MUST NOT authorize it

#### Scenario: Owner-entered manual source requires server attestation

- **WHEN** an interaction is entered through a server-authenticated owner path and its origin is
  attested by the server rather than request metadata
- **THEN** its expected-signal producer MUST be `owner`

#### Scenario: Unattested manual source is unmeasurable

- **WHEN** a manual interaction's owner origin is absent or only caller-asserted
- **THEN** the interaction source MUST be `unmeasurable`

#### Scenario: Unsupported and legacy writers fail closed

- **WHEN** a stale-contact input comes from Telegram bot, Discord, a calendar-derived interaction,
  a legacy/backfilled row, an unknown writer, or an interaction with no authoritative attestation
- **THEN** the input MUST be `unmeasurable`
- **AND** the system MUST NOT infer a producer from its predicate, contact handle, row order, or any
  currently healthy connector

#### Scenario: Mixed ownership or endpoint identity is unmeasurable

- **WHEN** the participating contact identities or latest authoritative observations resolve to
  more than one expected-signal producer or endpoint identity
- **THEN** the contact's stale-contact signal MUST be `unmeasurable`
- **AND** the evaluator MUST NOT choose one source by recency, primary flag, row order, or health

#### Scenario: Live elapsed source follows the existing policy

- **WHEN** exactly one mapped producer is healthy and current and the contact's effective cadence
  has elapsed
- **THEN** the signal MAY be `absent` and the existing stale-contact policy MAY emit its existing
  candidate or overdue result
- **AND** the existing cadence, tier-1500 exclusion, priority, ranking, deduplication, and delivery
  rules MUST remain unchanged

#### Scenario: Dead or unreadable producer suppresses every stale-contact output

- **WHEN** a mapped connector is stale, dead/offline, unhealthy, missing, or unreadable after the
  contact's cadence has elapsed
- **THEN** the signal MUST be `unmeasurable`, never `absent`
- **AND** a healthy sibling endpoint of the same connector type MUST NOT substitute for the exact
  attested endpoint
- **AND** `insight-scan`, `contacts_overdue`, scheduled relationship maintenance, and on-demand
  reconnect planning MUST emit no owner-facing stale-contact candidate or nudge for that contact

#### Scenario: Legacy observation waits for a trustworthy baseline

- **WHEN** the latest interaction used by the cadence calculation predates server-attested producer
  provenance
- **THEN** the stale-contact signal MUST remain `unmeasurable`
- **AND** current contact identity alone MUST NOT backfill the legacy observation's producer
- **AND** a later server-attested observation MAY establish a new trustworthy baseline
