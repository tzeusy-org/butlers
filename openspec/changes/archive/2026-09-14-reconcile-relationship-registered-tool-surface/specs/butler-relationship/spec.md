## ADDED Requirements

### Requirement: Relationship Butler Registered Tool Surface

The relationship butler SHALL expose its currently approved manifesto-owned
personal CRM tool set. Its eight configured relationship-module groups
(`contacts`, `contacts_extended`, `interactions`, `relationships`, `social`,
`notes`, `tracking`, and `management`) own 58 tools. The mandatory
`relationship_assert_fact` approval-dispatch handler registers unconditionally,
for 59 relationship-module handlers in total. The mixed `entity` group SHALL
remain disabled until the adopted six-read/two-write split is implemented; it
MUST NOT be activated while it still exposes both reads and writes.

#### Scenario: Exact current registered inventory

- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** all 58 tools owned by the eight configured groups SHALL be registered
- **AND** the inventory SHALL include `contact_create`, `contact_update`,
  `contact_get`, `contact_search`, `contact_archive`, `contact_resolve`,
  `relationship_add`, `relationship_list`, `relationship_remove`, `date_add`,
  `date_list`, `upcoming_dates`, `note_create`, `note_list`, `note_search`,
  `interaction_log`, `interaction_list`, `fact_set`, `fact_list`, and `feed_get`
- **AND** `relationship_assert_fact` SHALL be the additional mandatory
  unconditional handler, making 59 relationship-module handlers total
- **AND** `entity_resolve`, `entity_get`, `entity_neighbors`,
  `relationship_fact_evidence`, `relationship_predicate_coverage`,
  `relationship_lookup`, `entity_update`, and `relationship_record_coverage`
  SHALL all be absent
- **AND** no bare `entity_create` MCP tool or alias SHALL be registered
- **AND** the separately configured memory module MAY expose
  `memory_entity_create` under that canonical prefixed name

## REMOVED Requirements

### Requirement: Relationship Butler Tool Surface

**Reason**: The historical broad capability inventory conflates conceptual
capabilities with literal MCP names and requires entity tools both present and
absent after the owner kept the mixed entity group pruned.

**Migration**: Use `Relationship Butler Registered Tool Surface` for the exact
current callable contract. The separately adopted read-only entity-group change
will modify that requirement only when its implementation is authorized.

## MODIFIED Requirements

### Requirement: Entity Resolution Pipeline

The implementation SHALL provide the behavior described by this requirement.
The relationship butler follows a 7-step entity resolution pipeline for person mentions.

#### Scenario: Entity resolution flow
- **WHEN** the relationship butler processes a message mentioning a person AND the separately adopted read-only Relationship entity group is implemented and active
- **THEN** it follows a 7-step pipeline: (1) identify person mentions, (2) resolve each via `entity_resolve` with context hints, (3) apply disambiguation policy (zero candidates: create entity; single candidate or top leads by 30+ points: use entity_id; multiple candidates with gap less than 30 points: ask user), (4) handle new people, (5) store facts with entity_id, (6) log interactions, (7) update domain records
- **AND** before that activation the runtime MUST NOT claim `entity_resolve` as a registered callable tool

### Requirement: CRUD-to-SPO migration — relationship domain (bu-ddb.3)

The implementation SHALL provide the behavior described by this requirement.
The relationship butler migrates 9 dedicated CRUD tables to temporal SPO facts. All facts use `scope='relationship'` and `entity_id = contact_entity_id` (resolved from `public.contacts.entity_id` for each contact). Full predicate taxonomy and metadata schemas are in `openspec/changes/crud-to-spo-migration/specs/predicate-taxonomy.md`.

The relationship butler maintains TWO temporal fact stores that the CRUD-to-SPO tools route between by predicate kind. Narrative triples (interactions, notes, gifts, loans, tasks, reminders, life events, quick facts) are written to the `memory.facts` store (snake_case predicates, `scope='relationship'`) via `memory_store_fact` and the CRUD wrappers. Registry-relational edges and identity-contact predicates are written to the `relationship.entity_facts` store (kebab-case RDF-style predicates) via the central writer `relationship_assert_fact`; this store powers the relational columns and Dunbar concentration views. After the separately adopted read-only entity group is implemented and active, `relationship_lookup` SHALL expose the corresponding read surface. Before that activation, the runtime MUST NOT claim `relationship_lookup` as a registered callable tool.

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
