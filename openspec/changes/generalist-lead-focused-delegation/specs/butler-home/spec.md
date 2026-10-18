## MODIFIED Requirements

### Requirement: Home Butler Identity and Runtime

The home butler SHALL operate as a dedicated domain butler for smart-home orchestration. The home daemon SHALL NOT spawn reasoning sessions; reasoning about home state SHALL run as invocations of admitted goals that call Home capabilities within their grants.

Source: RFC 0038 D2, D5, D16
Scope: v1-mandatory

#### Scenario: Identity and port

- **WHEN** the home butler is running
- **THEN** it SHALL operate on port 41108 with description "Home automation orchestrator for smart devices, scenes, energy monitoring, and environmental comfort"
- **AND** it SHALL spawn no reasoning session and SHALL have no per-butler runtime adapter or session concurrency cap; models are chosen per goal invocation
- **AND** its database schema SHALL be `home` within the consolidated `butlers` database

#### Scenario: Module profile

- **WHEN** the home butler starts
- **THEN** it SHALL load modules: `home_assistant`, `memory`, `contacts` (Google provider, sync enabled), `approvals`, and `google_drive`

#### Scenario: Proactive event processing

- **WHEN** Switchboard accepts an ingestion event with `source.channel = "home_assistant"` that matches a Home subscription declared in git
- **THEN** it SHALL be admitted as an `obligation`-origin goal with that subscription's capability set, with no classification or route-dispatch step, and processed as a proactive home state change notification
- **AND** an event that matches no declared subscription SHALL be recorded into Home state without admitting a goal
- **AND** the goal SHALL use Home capabilities and memory within its ceiling to determine the appropriate response (store as fact, alert the owner, trigger a scene, or acknowledge silently)
- **AND** triggering a scene or service SHALL remain subject to the goal's ceiling, its exposure, and the RFC 0028 risk map
- **AND** the Home domain guidance SHALL include instructions for handling HA-originated events: prioritize safety-critical events (locks, security), store environmental changes as volatile facts, and notify the owner only for events that require attention or action

### Requirement: Home Butler HA Event Response Patterns

Goals admitted from real-time HA events through declared subscriptions (`REQ-butler-switchboard-007`) SHALL take context-appropriate actions using Home capabilities, within the goal's ceiling and exposure and under the RFC 0028 risk map. An action outside the goal's grant SHALL NOT be taken; the goal SHALL notify the owner instead.

Source: RFC 0038 D1, D5, D6
Scope: v1-mandatory

#### Scenario: Safety-critical event response
- **WHEN** the home butler receives an HA event for a `lock` or `cover` entity changing to an unexpected state (e.g., door unlocked at night, garage door opened while away)
- **THEN** it SHALL notify the owner immediately via `notify(channel="telegram", intent="proactive")`
- **AND** it SHALL store the event as a volatile memory fact with importance >= 8.0

#### Scenario: Environmental drift response
- **WHEN** the home butler receives an HA event indicating a sensor reading outside the owner's comfort preferences (stored in memory)
- **THEN** it SHALL evaluate whether corrective action is available (e.g., adjust thermostat, activate scene)
- **AND** it SHALL take corrective action only if a matching automation or scene exists and the goal's grant and the RFC 0028 risk map allow it, and otherwise notify the owner

#### Scenario: Automation failure response
- **WHEN** the home butler receives an HA event indicating an automation triggered with an unexpected outcome or an entity transitioning to `unavailable`
- **THEN** it SHALL store a volatile memory fact with `predicate="device_issue"` and appropriate tags
- **AND** it SHALL include the event in the next device health check report

#### Scenario: Routine state change acknowledgment
- **WHEN** the home butler receives an HA event for a routine state change (e.g., light turned on during normal hours, expected temperature fluctuation)
- **THEN** it SHALL store the event as a volatile memory fact for pattern analysis
- **AND** it SHALL NOT notify the owner (silent acknowledgment)

### Requirement: Home Butler Tool Surface

The home butler SHALL provide smart-home control and monitoring tools via the home_assistant module, registered as capabilities in the broker catalog. A goal invocation SHALL reach a Home capability only when its grant contains it.

Source: RFC 0038 D2, D4, D7
Scope: v1-mandatory

#### Scenario: Tool inventory

- **WHEN** the home butler registers its capabilities in the catalog
- **THEN** its catalog SHALL include: `ha_get_entity_state`, `ha_list_entities`, `ha_list_areas`, `ha_list_services`, `ha_get_history`, `ha_get_statistics`, `ha_render_template`, `ha_call_service`, `ha_activate_scene`, plus memory tools and contact tools

### Requirement: Home Butler Maintenance Tools

The home butler SHALL provide MCP tools for managing recurring maintenance items, registered as capabilities in the broker catalog and reachable only within a goal invocation's grant.

Source: RFC 0038 D2, D4
Scope: v1-mandatory

#### Scenario: Maintenance tool inventory

- **WHEN** the home butler registers its capabilities in the catalog
- **THEN** its catalog SHALL include: `ha_maintenance_create`, `ha_maintenance_complete`, `ha_maintenance_list`, `ha_maintenance_remove` in addition to existing HA tools, memory tools, and contact tools

### Requirement: Switchboard Registration

The home butler SHALL register with the Switchboard so its capabilities are published to the broker catalog. No butler SHALL route a request to the home butler; goals reach Home capabilities directly through the broker within their grants.

Source: RFC 0038 D1, D2, D7
Scope: v1-mandatory

#### Scenario: Switchboard advertisement

- **WHEN** the home butler starts
- **THEN** it SHALL register with the Switchboard's service listener in `butlers-control`, at the address its deployment configuration names, with `advertise = true`
- **AND** liveness TTL of 300 seconds
- **AND** registration contract version `route.v1`, which governs registration only now that route dispatch is deleted

#### Scenario: Cross-butler home control

- **WHEN** a goal needs to interact with smart-home devices
- **THEN** its lead, or a depth-1 child it briefs, SHALL call Home capabilities through the broker within its grant
- **AND** no butler SHALL route the request to the home butler via the Switchboard
- **AND** each call SHALL execute in the home butler's `home_assistant` module under the RFC 0028 risk map

### Requirement: Home Butler Personality

The home butler's domain guidance SHALL establish its domain expertise and interaction patterns for goal invocations that use Home capabilities. Guidance SHALL NOT be the only safeguard: consequential and protected actuation SHALL be enforced by the broker and the approvals module.

Source: RFC 0038 D2, D4
Scope: v1-mandatory

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
- **AND** consequential or protected calls SHALL require per-event owner approval through the approvals module
- **AND** a durable receipt and observed post-condition SHALL be the only authority for reporting success
