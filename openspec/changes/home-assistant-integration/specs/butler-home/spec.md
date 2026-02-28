# Home Butler Role

## Purpose

The Home butler (port 40108) is a home automation orchestrator that uses Home Assistant as a glue layer to control and monitor smart home devices (Zigbee, Wi-Fi, Z-Wave), manage scenes and automations, track energy consumption, and maintain awareness of the physical home environment.

## ADDED Requirements

### Requirement: Home Butler Identity and Runtime

The home butler operates as a dedicated domain butler for smart-home orchestration.

#### Scenario: Identity and port

- **WHEN** the home butler is running
- **THEN** it SHALL operate on port 40108 with description "Home automation orchestrator for smart devices, scenes, energy monitoring, and environmental comfort"
- **AND** it SHALL use the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema SHALL be `home` within the consolidated `butlers` database

#### Scenario: Module profile

- **WHEN** the home butler starts
- **THEN** it SHALL load modules: `home_assistant`, `memory`, `contacts` (Google provider, sync enabled), and `approvals`

### Requirement: Home Butler Tool Surface

The home butler provides smart-home control and monitoring tools via the home_assistant module.

#### Scenario: Tool inventory

- **WHEN** a runtime instance is spawned for the home butler
- **THEN** it SHALL have access to: `ha_get_entity_state`, `ha_list_entities`, `ha_list_areas`, `ha_list_services`, `ha_get_history`, `ha_get_statistics`, `ha_render_template`, `ha_call_service`, `ha_activate_scene`, plus memory tools and contact tools

### Requirement: Home Butler Schedules

The home butler runs periodic monitoring and reporting jobs.

#### Scenario: Scheduled task inventory

- **WHEN** the home butler daemon is running
- **THEN** it SHALL execute:
  - `weekly-energy-digest` (0 9 * * 0, prompt-based): summarize weekly energy consumption trends using `ha_get_statistics`, identify anomalies, compare to previous weeks, and notify the owner via `notify(channel="telegram", intent="send")`
  - `daily-environment-report` (0 8 * * *, prompt-based): snapshot temperature, humidity, and air quality across all areas using `ha_list_entities` and `ha_get_entity_state`, flag any readings outside comfortable ranges, and notify the owner
  - `device-health-check` (0 4 * * *, prompt-based): detect entities in `unavailable` or `unknown` state using `ha_list_entities`, check for low battery sensors, and notify the owner of any issues found
  - `memory-consolidation` (0 */6 * * *, job-based)
  - `memory-episode-cleanup` (0 4 * * *, job-based)

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

### Requirement: Switchboard Registration

The home butler registers with the Switchboard for cross-butler accessibility.

#### Scenario: Switchboard advertisement

- **WHEN** the home butler starts
- **THEN** it SHALL register with the Switchboard at `http://localhost:40100/mcp` with `advertise = true`
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
