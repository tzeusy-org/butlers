# Home Butler — Connector Awareness

## MODIFIED Requirements

### Requirement: Home Butler Identity and Runtime

The home butler operates as a dedicated domain butler for smart-home orchestration.

#### Scenario: Identity and port

- **WHEN** the home butler is running
- **THEN** it SHALL operate on port 41108 with description "Home automation orchestrator for smart devices, scenes, energy monitoring, and environmental comfort"
- **AND** it SHALL use the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema SHALL be `home` within the consolidated `butlers` database

#### Scenario: Module profile

- **WHEN** the home butler starts
- **THEN** it SHALL load modules: `home_assistant`, `memory`, `contacts` (Google provider, sync enabled), and `approvals`

#### Scenario: Proactive event processing

- **WHEN** the home butler receives a routed ingestion event from the Switchboard with `source.channel = "home_assistant"`
- **THEN** it SHALL process the event as a proactive home state change notification
- **AND** it SHALL use its HA tools and memory to determine the appropriate response (store as fact, alert the owner, trigger a scene, or acknowledge silently)
- **AND** the system prompt SHALL include instructions for handling HA-originated events: prioritize safety-critical events (locks, security), store environmental changes as volatile facts, and notify the owner only for events that require attention or action

## ADDED Requirements

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
