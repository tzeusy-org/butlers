## MODIFIED Requirements

### Requirement: Relationship Butler Identity and Runtime

The relationship butler SHALL maintain personal CRM context with 40+ domain tools, registered with the control plane as relationship capabilities. It SHALL NOT spawn reasoning sessions or hold a runtime adapter or session pool; the model for any goal invocation that uses its capabilities is resolved per invocation by role and tier.

Source: RFC 0038 D2 and D16
Scope: v1-mandatory

#### Scenario: Identity and port
- **WHEN** the relationship butler is running
- **THEN** it operates on port 41102 with description "Personal CRM. Manages contacts, relationships, important dates, interactions, gifts, and reminders."
- **AND** it runs no runtime adapter and holds no concurrent-session limit, because it spawns no reasoning session
- **AND** its database schema is `relationship` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the relationship butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), and `memory`

### Requirement: Relationship Butler Tool Surface — Dunbar Tier
The relationship butler SHALL expose Dunbar tier management and group interaction tools as relationship capabilities registered with the control plane.

Source: RFC 0038 D2 and D7
Scope: v1-mandatory

#### Scenario: Dunbar tier tool in tool inventory
- **WHEN** the relationship butler registers its capabilities with the control plane
- **THEN** it MUST register `dunbar_tier_set(contact_id, tier)` for setting or clearing manual Dunbar tier overrides
- **AND** `contact_get` and `contact_search` responses MUST include `dunbar_tier` and `dunbar_score` fields
- **AND** it MUST register `interaction_log_group(group_id, direction, occurred_at, summary)` for logging interactions with all members of a contact group in a single call

### Requirement: Relationship Butler Schedules

The relationship butler SHALL run date checks, maintenance sweeps, and memory jobs. `upcoming-dates-check` and `relationship-maintenance` SHALL be deterministic jobs: each renders a fixed template from an existing read and sends it to the owner through the owner-notify boundary, spawning no session and calling no model. Memory episode cleanup and the insight scan SHALL remain deterministic jobs. The `memory-consolidation` job SHALL only submit consolidation obligation admissions (`REQ-module-memory-005`), because consolidation reasons with a model.

The registered upcoming_dates management tool and the scheduled upcoming-dates-check birthday/anniversary push SHALL consider only canonical people whose current posture is active at the date query; an unreadable posture SHALL raise rather than fabricate active or empty results. This contract SHALL preserve historical dates and the separate calendar overlay's existing remembrance behavior.

ID: REQ-butler-relationship-005
Source: RFC 0038 D2, D5 and D18
Scope: v1-mandatory

#### Scenario: Scheduled task inventory
- **WHEN** the relationship butler daemon is running
- **THEN** it executes: `upcoming-dates-check` (0 8 * * *, job: check birthdays/anniversaries in the next 7 days), `relationship-maintenance` (0 9 * * 1, job: rank overdue contacts by Dunbar tier-weighted urgency and suggest top 3 reconnections), `memory-consolidation` (0 */6 * * *, job that submits consolidation obligation admissions), `memory-episode-cleanup` (0 4 * * *, job), and `insight-scan` (0 7 * * *, job: evaluate relationship domain data and generate insight candidates)

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
- **AND** the `upcoming-dates-check` job SHALL send no reminder or all-clear on that failed read, and the scheduler records the job failure

#### Scenario: Daily reminder uses only the successful active date projection
- **WHEN** the 08:00 upcoming-dates-check runs
- **THEN** its job SHALL call the registered upcoming_dates(days_ahead=7), stay silent for a successful empty result, and send one templated owner reminder only from a successful active-date result
- **AND** this tool/push SHALL NOT expose memorial remembrance without a separately adopted opt-in

#### Scenario: Maintenance job sends no all-clear when cadence is unmeasurable
- **WHEN** the weekly `relationship-maintenance` job runs and the overdue evaluation reports `cadence_available` false
- **THEN** it SHALL send no reconnect suggestion and no all-clear
- **AND** when cadence is available and no contact is overdue it SHALL send exactly "All your key relationships are up to date — no overdue check-ins this week."

#### Scenario: Maintenance ranks only eligible overdue contacts by unified urgency
- **WHEN** the weekly `relationship-maintenance` job finds overdue, measurable contacts
- **THEN** it SHALL rank only those contacts by the dunbar-tier-scoring unified urgency score and suggest at most 3, fewer when fewer qualify
- **AND** a contact that is not overdue SHALL NOT be suggested, whatever its context bonus
- **AND** an overdue contact missing from the tier ranking SHALL be counted and logged, not ranked from raw interaction rows

#### Scenario: Scheduled reminders spawn no session
- **WHEN** the `upcoming-dates-check` or `relationship-maintenance` job runs
- **THEN** it SHALL spawn no session and call no model, and its message text SHALL come from a fixed template
- **AND** it SHALL send through the owner-notify boundary: quiet-hours and context-bus suppression, the owner's resolved Telegram recipient, a `notify.v1` send through the Switchboard `deliver` tool, and one attention-ledger row per outcome (delivered, suppressed or failed)
- **AND** a suppressed send SHALL be dropped and recorded rather than deferred, and a failed send SHALL NOT be retried by the job

#### Scenario: Date-check occurrence runs as a deterministic job
- **WHEN** the 08:00 `upcoming-dates-check` occurrence fires
- **THEN** the relationship daemon runs the deterministic job and submits no obligation admission, and no goal is admitted for it

### Requirement: Relationship Butler Registered Tool Surface

The relationship butler SHALL expose its currently approved manifesto-owned
personal CRM tool set. Its eight configured relationship-module groups
(`contacts`, `contacts_extended`, `interactions`, `relationships`, `social`,
`notes`, `tracking`, and `management`) own 58 tools. The mandatory
`relationship_assert_fact` approval-dispatch handler registers unconditionally,
for 59 relationship-module handlers in total. The mixed `entity` group SHALL
remain disabled until the adopted six-read/two-write split is implemented; it
MUST NOT be activated while it still exposes both reads and writes. These tools SHALL be registered with the control plane as relationship capabilities rather than presented to a relationship-owned session.

Source: RFC 0038 D2 and D7
Scope: v1-mandatory

#### Scenario: Exact current registered inventory

- **WHEN** the relationship butler registers its capabilities with the control plane
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
