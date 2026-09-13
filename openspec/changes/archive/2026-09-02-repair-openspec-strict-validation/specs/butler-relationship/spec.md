## MODIFIED Requirements

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
