# Home Butler Role

## Purpose

The Home butler (port 41108) is a home automation orchestrator that uses Home Assistant as a glue layer to control and monitor smart home devices (Zigbee, Wi-Fi, Z-Wave), manage scenes and automations, track energy consumption, and maintain awareness of the physical home environment.

## ADDED Requirements

### Requirement: Home Butler Identity and Runtime

The home butler operates as a dedicated domain butler for smart-home orchestration.

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

The home butler responds to real-time HA events routed through the Switchboard with context-appropriate actions.

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

The home butler provides smart-home control and monitoring tools via the home_assistant module.

#### Scenario: Tool inventory

- **WHEN** a runtime instance is spawned for the home butler
- **THEN** it SHALL have access to: `ha_get_entity_state`, `ha_list_entities`, `ha_list_areas`, `ha_list_services`, `ha_get_history`, `ha_get_statistics`, `ha_render_template`, `ha_call_service`, `ha_activate_scene`, plus memory tools and contact tools

### Requirement: Home Butler Maintenance Tools

The home butler provides MCP tools for managing recurring maintenance items.

#### Scenario: Maintenance tool inventory

- **WHEN** a runtime instance is spawned for the home butler
- **THEN** it SHALL have access to: `ha_maintenance_create`, `ha_maintenance_complete`, `ha_maintenance_list`, `ha_maintenance_remove` in addition to existing HA tools, memory tools, and contact tools

### Requirement: Home Butler Schedules

The home butler runs periodic monitoring and reporting jobs. Monitoring tasks use deterministic job-based dispatch to avoid LLM costs for formulaic work.

#### Scenario: Scheduled task inventory

- **WHEN** the home butler daemon is running
- **THEN** it SHALL execute:
  - `device-health-check` (0 4 * * *, job-based, job_name=`device_health_check`): read entity states from connector-populated `ha_entity_snapshot`, classify offline status and low battery using configurable thresholds from state store (`home:thresholds:battery`, `home:thresholds:offline_hours`), store findings in memory, and notify the owner via Telegram
  - `environment-report` (0 8 * * *, job-based, job_name=`environment_report`): read environmental sensors per area from `ha_entity_snapshot`, compare against stored comfort preferences with configurable deviation thresholds from state store (`home:thresholds:comfort_defaults`, `home:thresholds:comfort_deviation`), and send a room-by-room report via Telegram
  - `weekly-energy-digest` (0 9 * * 0, job-based, job_name=`energy_digest`): discover energy sensors from `ha_entity_snapshot`, fetch weekly historical statistics via HA REST API (`recorder/get_statistics_during_period`), compute top consumers and trends vs. baselines using configurable anomaly thresholds from state store (`home:thresholds:energy`), and send a structured digest via Telegram
  - `maintenance-schedule-check` (0 10 * * 1, job-based, job_name=`maintenance_schedule_check`): check all maintenance items for due/overdue status and send reminders via Telegram
  - `memory-consolidation` (0 */6 * * *, job-based, job_name=`memory_consolidation`)
  - `memory-episode-cleanup` (5 4 * * *, job-based, job_name=`memory_episode_cleanup`)
  - `memory-purge-superseded` (10 4 * * *, job-based, job_name=`memory_purge_superseded`)

### Requirement: Home Butler Skills

The home butler has workflow skills for common smart-home operations.

#### Scenario: Skill inventory

- **WHEN** the home butler operates
- **THEN** it SHALL have access to:
  - `comfort`: guided workflow for adjusting climate, lighting, and scenes based on time of day, season, and occupant preferences
  - `energy`: monitoring consumption patterns, identifying energy waste, and suggesting optimizations using historical statistics
  - `scenes`: discovering, activating, and composing multi-device scenes for routines (morning, movie night, bedtime, away)
  - `troubleshooting`: diagnosing unavailable devices, connectivity issues, and suggesting remediation steps
  - plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Home Memory Taxonomy

The home butler uses a home-automation memory taxonomy for learning owner preferences and patterns.

#### Scenario: Memory classification

- **WHEN** the home butler extracts facts
- **THEN** it SHALL use subjects like area names (e.g. "kitchen", "bedroom"), device names, or "owner"; predicates like `comfort_preference`, `scene_preference`, `schedule_pattern`, `device_issue`, `energy_baseline`; permanence `stable` for long-term preferences (e.g. "owner prefers 21°C at bedtime"), `standard` for seasonal patterns and device configurations, `volatile` for transient issues and one-off adjustments

### Requirement: HA entity live-state cache (ha_entity_snapshot)
The home butler keeps the current state of every Home Assistant entity in the `ha_entity_snapshot` table. The `home_assistant` module is the sole writer: a periodic snapshot task persists its in-memory entity cache to the table, and the dashboard API plus the home scheduled jobs read live entity state from it. An earlier attempt to migrate this state to temporal `ha_state` SPO facts was reverted because it produced unbounded superseded-fact growth; any residual `ha_state` facts are purged by memory maintenance.

#### Scenario: Module persists the entity cache to ha_entity_snapshot
- **WHEN** the home `home_assistant` module has a populated in-memory entity cache (seeded over REST and updated by WebSocket `state_changed` events)
- **THEN** it MUST UPSERT one row per entity into `ha_entity_snapshot` keyed on `entity_id`, setting `state`, `attributes` (JSONB, with registry-derived `area_id` and `area_name` merged in), `last_updated`, and `captured_at`
- **AND** a later state update for the same entity MUST overwrite its row in place so the table holds exactly one row per entity
- **AND** persistence runs on a fixed cadence (`snapshot_interval_seconds`), once immediately after the initial cache seed, and once more on shutdown

#### Scenario: Live-state reads query ha_entity_snapshot
- **WHEN** the home butler jobs (`device-health-check`, `environment-report`, `weekly-energy-digest`) or the dashboard API need current HA entity state
- **THEN** they MUST read from `ha_entity_snapshot` (for example `SELECT entity_id, state, attributes, last_updated FROM ha_entity_snapshot`), optionally filtered by entity-ID domain prefix or `attributes->>'area_id'`
- **AND** an empty result signals that the module has not yet populated state, not a permanent condition

### Requirement: Switchboard Registration

The home butler registers with the Switchboard for cross-butler accessibility.

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

The home butler's system prompt establishes its domain expertise and interaction patterns.

#### Scenario: Interactive Response Mode

- **WHEN** the home butler receives a request
- **THEN** it SHALL respond with awareness of the physical home context (current entity states, areas, time of day)
- **AND** proactively suggest related actions (e.g. "lights are on in the bedroom — shall I turn them off too?")
- **AND** confirm destructive or area-wide actions before executing

#### Scenario: Safety-first approach

- **WHEN** the home butler is asked to perform safety-critical actions (unlock doors, disable security automations, area-wide power-off)
- **THEN** it SHALL explicitly confirm with the owner before executing
- **AND** log the action with full context to the command audit trail
