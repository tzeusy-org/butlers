# Relationship Butler Role

## Purpose
The Relationship butler (port 41102) is a personal CRM that manages contacts, relationships, important dates, interactions, gifts, reminders, and loans.

## Requirements

### Requirement: Relationship Butler Identity and Runtime

The relationship butler SHALL maintain personal CRM context with 40+ domain tools.

#### Scenario: Identity and port
- **WHEN** the relationship butler is running
- **THEN** it operates on port 41102 with description "Personal CRM. Manages contacts, relationships, important dates, interactions, gifts, and reminders."
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `relationship` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the relationship butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), and `memory`

### Requirement: Relationship Butler Tool Surface — Dunbar Tier
The relationship butler SHALL expose Dunbar tier management and group interaction tools.

#### Scenario: Dunbar tier tool in tool inventory
- **WHEN** a runtime instance is spawned for the relationship butler
- **THEN** it MUST have access to `dunbar_tier_set(contact_id, tier)` for setting or clearing manual Dunbar tier overrides
- **AND** `contact_get` and `contact_search` responses MUST include `dunbar_tier` and `dunbar_score` fields
- **AND** it MUST have access to `interaction_log_group(group_id, direction, occurred_at, summary)` for logging interactions with all members of a contact group in a single call

### Requirement: Entity Resolution Pipeline

The relationship butler SHALL follow a 7-step entity resolution pipeline for person mentions.

#### Scenario: Entity resolution flow
- **WHEN** the relationship butler processes a message mentioning a person AND the separately adopted read-only Relationship entity group is implemented and active
- **THEN** it follows a 7-step pipeline: (1) identify person mentions, (2) resolve each via `entity_resolve` with context hints, (3) apply disambiguation policy (zero candidates: create entity; single candidate or top leads by 30+ points: use entity_id; multiple candidates with gap less than 30 points: ask user), (4) handle new people, (5) store facts with entity_id, (6) log interactions, (7) update domain records
- **AND** before that activation the runtime MUST NOT claim `entity_resolve` as a registered callable tool

### Requirement: Relationship Butler Schedules

The relationship butler SHALL run date checks, maintenance sweeps, and memory jobs.

The registered upcoming_dates management tool and the scheduled upcoming-dates-check birthday/anniversary push SHALL consider only canonical people whose current posture is active at the date query; an unreadable posture SHALL raise rather than fabricate active or empty results. This contract SHALL preserve historical dates and the separate calendar overlay's existing remembrance behavior.

#### Scenario: Scheduled task inventory
- **WHEN** the relationship butler daemon is running
- **THEN** it executes: `upcoming-dates-check` (0 8 * * *, prompt-based: check birthdays/anniversaries in the next 7 days), `relationship-maintenance` (0 9 * * 1, prompt-based: rank overdue contacts by Dunbar tier-weighted urgency and suggest top 3 reconnections), `memory-consolidation` (0 */6 * * *, job), `memory-episode-cleanup` (0 4 * * *, job), and `insight-scan` (0 7 * * *, job: evaluate relationship domain data and generate insight candidates)

#### Scenario: Both upcoming date anchor paths enforce active posture
- **WHEN** listed contact-anchored or contactless local-entity-anchored people have dates inside the lookahead window
- **THEN** upcoming_dates SHALL return active people under the existing listed/date guards
- **AND** it SHALL omit memorial, quiet and no_contact people in both UNION arms while retaining an unrelated active positive row

#### Scenario: Memorial date reappears after reactivation
- **WHEN** a memorial person's birthday is in 3 days and the same person is later restored to active through the existing posture writer
- **THEN** upcoming_dates SHALL initially omit the date and then return the unchanged date after reactivation
- **AND** no historic date, memory or posture audit history SHALL be deleted

#### Scenario: Posture read failure cannot produce a reminder
- **WHEN** the real upcoming date query cannot read posture or its database query fails
- **THEN** the tool SHALL propagate the error rather than return an active fallback or a fabricated empty list
- **AND** the existing scheduled prompt SHALL instruct no reminder or all-clear on that failed read

#### Scenario: Daily reminder uses only the successful active date projection
- **WHEN** the 08:00 upcoming-dates-check runs
- **THEN** its prompt SHALL call the registered upcoming_dates(days_ahead=7), stay silent for a successful empty result, and draft an ordinary owner reminder only from a successful active-date result
- **AND** this tool/push SHALL NOT expose memorial remembrance without a separately adopted opt-in

### Requirement: Relationship Butler Skills

The relationship butler SHALL have gift brainstorming and reconnection planning skills.

#### Scenario: Skill inventory
- **WHEN** the relationship butler operates
- **THEN** it has access to `gift-brainstorm` (personalized gift idea generation with budget tiers and gift pipeline integration) and `reconnect-planner` (Dunbar tier-aware stale contact identification and reconnection outreach planning using tier-weighted urgency ranking), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Relationship Memory Taxonomy

The relationship butler SHALL use a person-centric memory taxonomy.

#### Scenario: Memory classification
- **WHEN** the relationship butler extracts facts
- **THEN** it uses the person's human-readable name as subject (with entity_id as anchor); predicates like `relationship_to_user`, `birthday`, `preference`, `current_interest`, `workplace`, `lives_in`, `dunbar_tier_override`; permanence `permanent` for identity facts and tier overrides, `stable` for workplace/location, `standard` for interests, `volatile` for temporary states

### Requirement: Relationship data stored as temporal facts

The relationship butler SHALL store contact interactions, notes, gifts, loans, tasks, reminders, life events, and quick facts as SPO facts rather than dedicated CRUD tables. All facts use `scope='relationship'` and `entity_id = contact_entity_id` (the contact's canonical `public.entities` id), and their predicates are registered in the memory predicate registry.

The relationship butler maintains TWO temporal fact stores that the CRUD-to-SPO tools route between by predicate kind. Narrative triples (interactions, notes, gifts, loans, tasks, reminders, life events, quick facts) are written to the `memory.facts` store (snake_case predicates, `scope='relationship'`) via `memory_store_fact` and the CRUD wrappers. Registry-relational edges and identity-contact predicates are written to the `relationship.entity_facts` store (kebab-case RDF-style predicates) via the central writer `relationship_assert_fact`; this store powers the relational columns and Dunbar concentration views. After the separately adopted read-only entity group is implemented and active, `relationship_lookup` SHALL expose the corresponding read surface. Before that activation, the runtime MUST NOT claim `relationship_lookup` as a registered callable tool.

#### Scenario: Contact entity resolution before fact storage
- **WHEN** any relationship CRUD-migrated tool stores a fact for a contact
- **THEN** the tool MUST resolve the target to its canonical `public.entities` id: a contact UUID through its contact-to-entity link, an entity UUID as-is
- **AND** the resolved `entity_id` MUST be used as `entity_id` for the fact, and `subject` MUST be a stable identifier key (for example `entity:{entity_id}`), never a free-text name

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
Source: RFC 0011

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
