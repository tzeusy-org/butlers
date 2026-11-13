# Home Butler Role

## Purpose

The Home butler (port 41108) is a home automation orchestrator that uses Home Assistant as a glue layer to control and monitor smart home devices (Zigbee, Wi-Fi, Z-Wave), manage scenes and automations, track energy consumption, and maintain awareness of the physical home environment.

## Requirements

### Requirement: Home Butler Identity and Runtime

The home butler SHALL operate as a dedicated domain butler for smart-home orchestration.

#### Scenario: Identity and port

- **WHEN** the home butler is running
- **THEN** it SHALL operate on port 41108 with description "Home automation orchestrator for smart devices, scenes, energy monitoring, and environmental comfort"
- **AND** it SHALL use the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema SHALL be `home` within the consolidated `butlers` database

#### Scenario: Module profile

- **WHEN** the home butler starts
- **THEN** it SHALL load modules: `home_assistant`, `memory`, `contacts` (Google provider, sync enabled), `approvals`, and `google_drive`

#### Scenario: Proactive event processing

- **WHEN** the home butler receives a routed ingestion event from the Switchboard with `source.channel = "home_assistant"`
- **THEN** it SHALL process the event as a proactive home state change notification
- **AND** it SHALL use its HA tools and memory to determine the appropriate response (store as fact, alert the owner, trigger a scene, or acknowledge silently)
- **AND** the system prompt SHALL include instructions for handling HA-originated events: prioritize safety-critical events (locks, security), store environmental changes as volatile facts, and notify the owner only for events that require attention or action

### Requirement: Home Butler HA Event Response Patterns

The home butler SHALL respond to real-time HA events routed through the Switchboard with context-appropriate actions.

#### Scenario: Safety-critical event response
- **WHEN** the home butler receives an HA event for a `lock` or `cover` entity changing to an unexpected state (e.g., door unlocked at night, garage door opened while away)
- **THEN** it SHALL notify the owner immediately via `notify(channel="telegram", intent="proactive")`
- **AND** it SHALL store the event as a volatile memory fact with importance >= 8.0

#### Scenario: Environmental drift response
- **WHEN** the home butler receives an HA event indicating a sensor reading outside the owner's comfort preferences (stored in memory)
- **THEN** it SHALL evaluate whether corrective action is available (e.g., adjust thermostat, activate scene)
- **AND** it SHALL take corrective action if a matching automation or scene exists, or notify the owner if manual intervention is needed

#### Scenario: Automation failure response
- **WHEN** the home butler receives an HA event indicating an automation triggered with an unexpected outcome or an entity transitioning to `unavailable`
- **THEN** it SHALL store a volatile memory fact with `predicate="device_issue"` and appropriate tags
- **AND** it SHALL include the event in the next device health check report

#### Scenario: Routine state change acknowledgment
- **WHEN** the home butler receives an HA event for a routine state change (e.g., light turned on during normal hours, expected temperature fluctuation)
- **THEN** it SHALL store the event as a volatile memory fact for pattern analysis
- **AND** it SHALL NOT notify the owner (silent acknowledgment)

### Requirement: Home Butler Tool Surface

The home butler SHALL provide smart-home control and monitoring tools via the home_assistant module.

#### Scenario: Tool inventory

- **WHEN** a runtime instance is spawned for the home butler
- **THEN** it SHALL have access to: `ha_get_entity_state`, `ha_list_entities`, `ha_list_areas`, `ha_list_services`, `ha_get_history`, `ha_get_statistics`, `ha_render_template`, `ha_call_service`, `ha_activate_scene`, plus memory tools and contact tools

### Requirement: Home Butler Maintenance Tools

The home butler SHALL provide MCP tools for managing recurring maintenance items.

#### Scenario: Maintenance tool inventory

- **WHEN** a runtime instance is spawned for the home butler
- **THEN** it SHALL have access to: `ha_maintenance_create`, `ha_maintenance_complete`, `ha_maintenance_list`, `ha_maintenance_remove` in addition to existing HA tools, memory tools, and contact tools

### Requirement: Home Butler Schedules

The home butler SHALL run periodic monitoring and reporting jobs. Monitoring tasks use deterministic job-based dispatch to avoid LLM costs for formulaic work.

#### Scenario: Scheduled task inventory

- **WHEN** the home butler daemon is running
- **THEN** it SHALL execute:
  - `device-health-check` (0 */4 * * *, job-based, job_name=`device_health_check`): read entity states from connector-populated `ha_entity_snapshot`, classify offline status and low battery using configurable thresholds from state store (`home:thresholds:battery`, `home:thresholds:offline_hours`), store findings in memory, and notify the owner via Telegram — offline classification excludes HA domains whose steady state is normally "unknown" (buttons, the Assist conversation agent, Broadlink IR/RF blasters, TTS engines, Zigbee2MQTT dimmer "action" sensors), and repeat notifications for an already-known unresolved issue are throttled by a re-alert window from state store (`home:thresholds:realert`, default 24h)
  - `environment-report` (0 8 * * *, job-based, job_name=`environment_report`): read environmental sensors per area from `ha_entity_snapshot`, compare against stored comfort preferences with configurable deviation thresholds from state store (`home:thresholds:comfort_defaults`, `home:thresholds:comfort_deviation`), and send a room-by-room report via Telegram
  - `weekly-energy-digest` (0 9 * * 0, job-based, job_name=`energy_digest`): discover energy sensors from `ha_entity_snapshot`, fetch weekly historical statistics via the HA WebSocket API (`recorder/statistics_during_period`), compute top consumers and trends vs. baselines using configurable anomaly thresholds from state store (`home:thresholds:energy`), and send a structured digest via Telegram
  - `maintenance-schedule-check` (0 10 * * 1, job-based, job_name=`maintenance_schedule_check`): check all maintenance items for due/overdue status and send reminders via Telegram
  - `memory-consolidation` (0 */6 * * *, job-based, job_name=`memory_consolidation`)
  - `memory-episode-cleanup` (5 4 * * *, job-based, job_name=`memory_episode_cleanup`)
  - `memory-purge-superseded` (10 4 * * *, job-based, job_name=`memory_purge_superseded`)

### Requirement: Home Butler Skills

The home butler SHALL have workflow skills for common smart-home operations.

#### Scenario: Skill inventory

- **WHEN** the home butler operates
- **THEN** it SHALL have access to:
  - `comfort`: guided workflow for adjusting climate, lighting, and scenes based on time of day, season, and occupant preferences
  - `energy`: monitoring consumption patterns, identifying energy waste, and suggesting optimizations using historical statistics
  - `scenes`: discovering, activating, and composing multi-device scenes for routines (morning, movie night, bedtime, away)
  - `troubleshooting`: diagnosing unavailable devices, connectivity issues, and suggesting remediation steps
  - plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Home Memory Taxonomy

The home butler SHALL use a home-automation memory taxonomy for learning owner preferences and patterns.

#### Scenario: Memory classification

- **WHEN** the home butler extracts facts
- **THEN** it SHALL use subjects like area names (e.g. "kitchen", "bedroom"), device names, or "owner"; predicates like `comfort_preference`, `scene_preference`, `schedule_pattern`, `device_issue`, `energy_baseline`; permanence `stable` for long-term preferences (e.g. "owner prefers 21°C at bedtime"), `standard` for seasonal patterns and device configurations, `volatile` for transient issues and one-off adjustments

### Requirement: HA entity live-state cache (ha_entity_snapshot)

The home butler SHALL keep the current state of every Home Assistant entity in the `ha_entity_snapshot` table. The `home_assistant` module is the sole writer: a periodic snapshot task persists its in-memory entity cache to the table, and the dashboard API plus the home scheduled jobs read live entity state from it. An earlier attempt to migrate this state to temporal `ha_state` SPO facts was reverted because it produced unbounded superseded-fact growth; any residual `ha_state` facts are purged by memory maintenance.

#### Scenario: Module persists the entity cache to ha_entity_snapshot
- **WHEN** the home `home_assistant` module has a populated in-memory entity cache (seeded over REST and updated by WebSocket `state_changed` events)
- **THEN** it MUST UPSERT one row per entity into `ha_entity_snapshot` keyed on `entity_id`, setting `state`, `attributes` (JSONB, with registry-derived `area_id` and `area_name` merged in), `last_updated`, and `captured_at`
- **AND** a later state update for the same entity MUST overwrite its row in place so the table holds exactly one row per entity
- **AND** persistence runs on a fixed cadence (`snapshot_interval_seconds`), once immediately after the initial cache seed, and once more on shutdown

#### Scenario: Live-state reads query ha_entity_snapshot
- **WHEN** the home butler jobs (`device-health-check`, `environment-report`, `weekly-energy-digest`) or the dashboard API need current HA entity state
- **THEN** they MUST read from `ha_entity_snapshot` (for example `SELECT entity_id, state, attributes, last_updated FROM ha_entity_snapshot`), optionally filtered by entity-ID domain prefix or `attributes->>'area_id'`
- **AND** an empty result signals that the module has not yet populated state, not a permanent condition

### Requirement: Owner place registry and dashboard editor

The system SHALL keep the owner's named places in one trusted registry,
`public.owner_places` (core migration `core_303`), and Home SHALL own its only editor.
Each place has a stable UUID `id` (never reused, so later perception records can carry
it), a trimmed `label` of 1-80 characters, a `kind` of `home`, `work`, `airport` or
`other`, `lat`/`lon` within range at no more than five decimals, an integer `radius_m`
of 50-2000, up to 10 `aliases`, up to 20 exact case-sensitive `ssids` of 1-32 bytes, an
optional `ha_zone` (`zone.<id>`), `paused`, `version` and `deleted_at`. Among live
(not deleted) places a label is unique case-insensitively, at most one place is `home`,
and an SSID belongs to at most one place.

The editor is `GET/POST /api/home/places` and `PUT/DELETE /api/home/places/{id}`. Each
route passes `require_dashboard_owner_control` before it reads any body octet, decodes,
or acquires a pool; the handlers read the body themselves (at most 16 KiB) and the
generic `DashboardAuditMiddleware` skips the whole `/api/home/places` subtree by path
before touching the body. `PUT` replaces the whole row under compare-and-swap: an edit
identical to the stored place is a no-op success even from a stale `expected_version`;
a changed edit from a stale version is `409 VERSION_CONFLICT`; pausing is an edit of
`paused`. `DELETE ?expected_version=N` soft-deletes. Refusals are fixed and
content-blind (`422 INVALID_REQUEST`, `404 PLACE_NOT_FOUND`, `409 PLACE_CONFLICT`,
`413`, `503 PLACES_UNAVAILABLE`) and never echo input. Every write runs in one
transaction under a fixed advisory lock and appends one audit row carrying only the
operation, outcome and prior/new version. No response, log, audit row, prompt, model
session or MCP tool receives a coordinate, SSID, alias or zone id from this capability.

Runtime access is a row-security fence that survives `init-db.sql` regrants: only
`butler_travel_rw` and `butler_chronicler_rw` may read, and no runtime role may write.
`butlers.owner_places.load_active_owner_places` is the shared deterministic read
(live and unpaused places, stable order). The one-time import of
`OWNTRACKS_PLACE_REFERENCES` and `chronicler/owntracks/ssid_places` and the cutover of
the commuting-ETA, place-cluster and SSID readers are a later slice of the same
capability; until it lands those legacy sources remain the live configuration.

ID: REQ-butler-home-owner-places-001
Source: bu-jv1moa.7 (Dots superset run 20 move 7, S1)
Scope: v1-mandatory

#### Scenario: Denied owner authority touches nothing

- **WHEN** owner authentication is unavailable or the credential is missing or wrong
- **THEN** every places route returns `503` or `401` before reading the body or
  acquiring a pool, and no audit row is written

#### Scenario: Malformed place is refused without echo

- **WHEN** a write carries an out-of-range or over-precise coordinate, a radius outside
  50-2000, an unknown field, or a repeated SSID or alias
- **THEN** the API returns one fixed `422 INVALID_REQUEST` with no field detail and
  performs no database access

#### Scenario: Stale edit conflicts and writes nothing

- **WHEN** a changed edit carries an `expected_version` that is not the stored version
- **THEN** the API returns `409 VERSION_CONFLICT` and the row is unchanged

#### Scenario: Paused or deleted places leave resolution

- **WHEN** a place is paused or soft-deleted
- **THEN** `load_active_owner_places` no longer returns it, a paused place stays listed
  in the editor, and a deleted place's id is never reused

#### Scenario: A failed write leaves no partial state

- **WHEN** the audit append or any statement fails before commit
- **THEN** the API returns `503 PLACES_UNAVAILABLE` and the place and version are unchanged

### Requirement: Owner presence reducer

Home SHALL own one deterministic reducer that turns the owner's typed, coordinate-free
presence observations into confirmed leave and enter transitions on the canonical
`owner.place` slot and timeline (source `home.dots`, writer role
`perception_dots_writer`), through `PerceptionStore.write` only. It SHALL NOT create
`presence_observations`, `presence_edges` or `travel_legs`, keep a second owner clock,
notify, or actuate. Raw locations stay in connector-owned stores; Home never reads them.

Named constants, not runtime configuration: a side change opens a candidate on its first
qualifying observation and is confirmed after 3 minutes with no fresh observation of the
confirmed side, or on 2 distinct qualifying observations, whichever is first. Distinct means observed at
least 30 seconds apart, so two entities mirroring one signal count once. A
qualifying Home Assistant observation is a fresh owner-entity `home` or `not_home`
state; a qualifying OwnTracks outside fix has accuracy of at most 100 m and a distance
beyond the radius plus the larger of the accuracy and 50 m. A fresh home SSID match
counts as an inside observation and so vetoes a leave. Enter is symmetric. Stale,
unconfigured or unreadable input yields unknown and never an edge: a side established
from an unknown prior is preceded by an explicit `unknown` assertion, so the database
cannot classify it as a leave or enter. An enter confirmed within 10 minutes of the
leave it follows retracts that leave in the same compare-and-swap, keeping the leave in
history. `confirmed_at - at` above 15 minutes (exactly 15 is not) marks a transition
late; consumers skip late and retracted transitions. Stable event keys plus the slot
compare-and-swap make replay and concurrent delivery one transition. A confirmed state
is republished every 5 minutes while fresh evidence holds so its slot stays fresh.

`GET /api/home/presence/transitions` is the owner-only, read-only projection: confirmed
leave/enter edges newest first (`limit` counts edges: the server scans past heartbeats, resets
and retractions, up to a bounded number of timeline rows) with opaque place references, `late` and `retracted`
flags in the standard `Page` envelope (`items`, `next_cursor`, `limit`; an empty page with a cursor means the scan cap was hit before an edge was found). It answers `503 PRESENCE_TIMELINE_UNAVAILABLE` until the private
perception reader login is activated. The reducer is released dark: no schedule, no
`home.dots` profile and no login exist until the owner-authorized activation (and
bu-dhloi1.21). Rollback disables the reducer and consumer and retains canonical history.

ID: REQ-butler-home-presence-reducer-001
Source: bu-jv1moa.17
Scope: v1-mandatory

#### Scenario: Unknown prior never becomes a leave

- **WHEN** the reducer has no confirmed state and a confirmed outside signal arrives
- **THEN** it writes `unknown` and then the outside assertion, and the timeline row
  records an unknown prior state, not a leave

#### Scenario: A stale carried state is never a prior

- **WHEN** the carried side is older than the observation freshness window, or input was
  unreadable in between
- **THEN** the reducer forgets it, and the next confirmed side is an establish over an
  explicit unknown, not a leave or enter

#### Scenario: Quick return retracts the leave

- **WHEN** an enter is confirmed within 10 minutes of the leave it follows
- **THEN** one write records the enter and a bound retraction, and the leave stays in
  history marked retracted

#### Scenario: Late confirmation is flagged

- **WHEN** a transition is confirmed more than 15 minutes after its `at`
- **THEN** the timeline marks it late and live reads skip it

#### Scenario: Duplicate delivery is one transition

- **WHEN** the same transition is delivered twice, or by two runners
- **THEN** the slot advances one generation and the timeline holds one assertion

### Requirement: Switchboard Registration

The home butler SHALL register with the Switchboard for cross-butler accessibility.

#### Scenario: Switchboard advertisement

- **WHEN** the home butler starts
- **THEN** it SHALL register with the Switchboard at `http://localhost:41100/mcp` with `advertise = true`
- **AND** liveness TTL of 300 seconds
- **AND** route contract `route.v1`

#### Scenario: Cross-butler home control

- **WHEN** another butler (e.g. health, general) needs to interact with smart-home devices
- **THEN** it SHALL route the request to the home butler via the Switchboard
- **AND** the home butler SHALL process the request using its `home_assistant` module tools

### Requirement: Home Butler Personality

The home butler's system prompt SHALL establish its domain expertise and interaction patterns.

#### Scenario: Interactive Response Mode

- **WHEN** the home butler receives a request
- **THEN** it SHALL respond with awareness of the physical home context (current entity states, areas, time of day)
- **AND** proactively suggest related actions (e.g. "lights are on in the bedroom — shall I turn them off too?")
- **AND** confirm destructive or area-wide actions before executing

#### Scenario: Safety-first approach

- **WHEN** the home butler is asked to perform safety-critical actions (unlock doors, disable security automations, area-wide power-off)
- **THEN** it SHALL explicitly confirm with the owner before executing
- **AND** log the action with full context to the command audit trail

#### Scenario: Physical consequence boundary

- **WHEN** any Home Assistant service call can change physical state
- **THEN** the Home butler SHALL apply the declared safe/reversible/consequential/protected risk map
- **AND** consequential or protected calls SHALL require an approvals-module grant
- **AND** a durable receipt and observed post-condition SHALL be the only authority for reporting success
