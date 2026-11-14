## ADDED Requirements

### Requirement: Owner-only critical-entity registry

This requirement takes effect only when the owner adopts RFC 0028 Amendment 1; until then it is proposed and binds nothing.

The Home module SHALL keep a registry of critical entities in `home.critical_entities`, one row per explicit entity id with an owner label and the adding actor and time. The registry SHALL be written only through owner-authenticated dashboard routes. No MCP tool, model session, goal, capability grant, standing rule or job SHALL add or remove an entry, and membership SHALL NOT be inferred from a Home Assistant label, area, device class, attribute or `friendly_name`. The owner label SHALL be display text only. A refused write SHALL store nothing and answer with one fixed content-blind code. Every add and remove SHALL be audited by entity id and actor. Removing an entry SHALL NOT release an action already parked as critical.

ID: REQ-module-home-assistant-001
Source: RFC 0028 Amendment 1 (proposed, owner adoption required); bu-jv1moa.6; bu-jv1moa.33
Scope: v1-mandatory

#### Scenario: Only the owner changes the registry

- **WHEN** a model session, MCP tool call, goal or unauthenticated caller tries to add or remove a critical entity
- **THEN** the registry SHALL be unchanged and the refusal SHALL carry no echoed value

#### Scenario: Labels and names grant nothing

- **WHEN** a Home Assistant entity carries a label, area, device class or name that suggests it is critical
- **THEN** it SHALL NOT be treated as critical unless the owner added its entity id

#### Scenario: Every change is audited without its label

- **WHEN** the owner adds or removes a critical entity
- **THEN** one audit record SHALL carry the entity id, the actor and the time, and SHALL NOT carry the owner label

#### Scenario: Removal does not release a parked call

- **WHEN** the owner removes an entity from the registry while an approval parked as critical for it is pending
- **THEN** that approval SHALL stay pending and SHALL still need the owner's approval to run

### Requirement: Calls on critical entities always park and fail closed

This requirement takes effect only when the owner adopts RFC 0028 Amendment 1; until then it is proposed and binds nothing.

`ha_call_service` SHALL read the critical registry during each evaluation, without a cache that outlives the call. A call that names a critical entity SHALL have an effective risk of at least `consequential` and SHALL park before any Home Assistant request, whatever its base class. A call names a critical entity when its explicit target includes one. While the registry is non-empty, a call SHALL also be treated as naming a critical entity when: its target is not an explicit entity list; it is an indirect actuator (`scene.turn_on`, `script.turn_on`, any `script.<name>` service, `automation.trigger`, or any `homeassistant` service that is not `safe`); or a target entity is a group or helper whose member list, read from the entity's `entity_id` attribute and followed through nested groups, contains a critical entity. A group or helper whose membership cannot be read (absent from the module's entity cache, a malformed member list, a cycle, or nesting deeper than 8) SHALL be treated as naming a critical entity. If the registry cannot be read, every call SHALL be treated as naming a critical entity. A critical park SHALL NOT be approved by a standing rule, decision-memory or autonomy suggestion, or RFC 0038 capability grant; each SHALL need its own owner approval of the stored call. The capability broker SHALL classify a goal call naming a critical entity as `consequential` and SHALL classify it as `unknown` when the registry cannot be read. The park reason SHALL carry the fixed category `critical_entity` and no label or value. `safe` read services SHALL NOT be promoted. Home Assistant automations that fire on their own triggers do not pass through `ha_call_service` and SHALL NOT be described as covered; a helper that wraps an entity without exposing its membership is covered only when the owner marks the helper itself critical.

ID: REQ-module-home-assistant-002
Source: RFC 0028 Amendment 1 (proposed, owner adoption required); bu-svqpzo.12; RFC 0038; bu-jv1moa.33
Scope: v1-mandatory

#### Scenario: A reversible call on a critical entity parks

- **WHEN** an LLM session calls `light.turn_on` with an explicit target that the owner marked critical
- **THEN** the call SHALL park with reason category `critical_entity` and no Home Assistant request SHALL be sent
- **AND** the same call on a non-critical light SHALL still run under RFC 0028's automatic subset

#### Scenario: An unreadable registry parks everything

- **WHEN** the registry table is missing or its read fails
- **THEN** a call that would otherwise run unprompted SHALL park and no Home Assistant request SHALL be sent
- **AND** the capability broker SHALL classify any Home call as `unknown`

#### Scenario: No shortcut approves a critical call

- **WHEN** a standing rule, suggestion or capability grant matches a parked critical call exactly
- **THEN** the call SHALL remain pending until the owner approves it

#### Scenario: A broad target is treated as critical

- **WHEN** the registry is non-empty and a call targets an area, device, label or `all`
- **THEN** it SHALL park as critical and no shortcut SHALL approve it

#### Scenario: A group containing a critical member is critical

- **WHEN** the registry is non-empty and `switch.turn_off` targets a switch group whose member list, directly or through a nested group, contains a critical entity
- **THEN** the call SHALL park as critical instead of running as `reversible`

#### Scenario: Scenes, scripts and automations are critical

- **WHEN** the registry is non-empty and a call is `scene.turn_on`, `script.turn_on`, a `script.<name>` service, `automation.trigger` or a non-`safe` `homeassistant` service
- **THEN** it SHALL park as critical and no standing rule, suggestion or grant SHALL approve it

#### Scenario: Unreadable membership fails closed

- **WHEN** the registry is non-empty and a target entity is a group or helper that is missing from the cache, has a malformed member list, a cycle or nesting deeper than 8
- **THEN** the call SHALL park as critical

#### Scenario: Safe services are not promoted

- **WHEN** the registry is non-empty, or cannot be read, and a call is `homeassistant.update_entity`
- **THEN** it SHALL keep its `safe` class and SHALL NOT park

### Requirement: Pre-call state and target binding for critical calls and mitigations

This requirement takes effect only when the owner adopts RFC 0028 Amendment 1; until then it is proposed and binds nothing.

Before the receipt claim of an approved replay that names a critical entity under REQ-module-home-assistant-002, of any approved replay while the registry cannot be read, and of every safety mitigation, Home SHALL require an explicit entity target with no data-carried selector, SHALL read every target entity live from Home Assistant rather than from its cache, and SHALL require the read set to equal the stored target set. A failed read, a missing entity, a state of `unavailable` or `unknown`, a partial read set, or a read that finished more than 10 seconds before the receipt claim SHALL send no request and write no receipt; the approval SHALL stay `approved` and one approval event SHALL record a content-blind reason (`pre_state_unavailable`, `pre_state_stale` or `target_unbound`). Otherwise the pre-call state SHALL be stored on the receipt as `pre_state`.

ID: REQ-module-home-assistant-003
Source: RFC 0028 Amendment 1 (proposed, owner adoption required); RFC 0028 receipts; bu-jv1moa.2 (stored-call digest); bu-jv1moa.33
Scope: v1-mandatory

#### Scenario: Stale or missing state sends nothing

- **WHEN** an approved critical call replays and the live read fails, reads `unavailable`, misses an entity or is older than 10 seconds
- **THEN** no Home Assistant service request and no receipt SHALL be made
- **AND** the action SHALL read `approved_not_run` and one approval event SHALL carry the reason

#### Scenario: A refused replay may run later

- **WHEN** the same approval replays after a refusal and the live read is now fresh and complete
- **THEN** exactly one request SHALL be sent under one new receipt carrying `pre_state`

#### Scenario: An unreadable registry still requires the pre-read

- **WHEN** an approved call naming no registered entity replays while the registry cannot be read
- **THEN** the live pre-call read SHALL be required, and a failed or stale read SHALL send nothing

#### Scenario: The read is bound to the stored target

- **WHEN** the stored call's target cannot be resolved to its explicit entities, or carries a data selector
- **THEN** the replay SHALL be refused as `target_unbound` and no request SHALL be sent

### Requirement: Already-satisfied calls send nothing and claim no effect

This requirement takes effect only when the owner adopts RFC 0028 Amendment 1; until then it is proposed and binds nothing.

When the pre-call state of a call under REQ-module-home-assistant-003 already meets the call's declared post-condition, Home SHALL send no request and SHALL write a receipt that settles directly to `already_satisfied` with `pre_state` and without `observed_state`. `already_satisfied` SHALL NOT be `succeeded` and SHALL NOT be rendered as an effect Home produced; no resolver or card SHALL report it as success. A service with no declared post-condition verifier SHALL NOT settle `already_satisfied`. `already_satisfied` SHALL be terminal for its approval: a later replay of the same approval SHALL return that receipt's outcome without a request, the same way a `succeeded` receipt is replayed, and SHALL NOT be treated as a possible effect that needs attention. After a request is sent, `succeeded` SHALL still require a matching live read-back, and a mismatched or unreadable read-back SHALL settle `unverified`.

ID: REQ-module-home-assistant-004
Source: RFC 0028 Amendment 1 (proposed, owner adoption required); RFC 0028 receipts; bu-jv1moa.33
Scope: v1-mandatory

#### Scenario: A closed valve is not closed again

- **WHEN** an approved mitigation to close a valve replays and the fresh pre-call read shows it closed
- **THEN** no request SHALL be sent and the receipt SHALL settle `already_satisfied`

#### Scenario: A replay after a no-op stays a no-op

- **WHEN** an approval whose receipt settled `already_satisfied` is replayed
- **THEN** no request SHALL be sent, the existing receipt's outcome SHALL be returned and no attention SHALL be raised

#### Scenario: No verifier means no no-op

- **WHEN** an approved critical call has no declared post-condition verifier
- **THEN** it SHALL NOT settle `already_satisfied`, it SHALL proceed to the normal attempt and settle `unverified`, and it SHALL NOT be shown as success

#### Scenario: A failed post-condition is never success

- **WHEN** Home Assistant accepts the request and the live read-back does not match the post-condition
- **THEN** the receipt SHALL settle `unverified` and SHALL require attention
