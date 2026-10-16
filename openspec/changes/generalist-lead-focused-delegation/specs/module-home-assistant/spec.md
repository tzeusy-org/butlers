## MODIFIED Requirements

### Requirement: Physical Actuation Risk Gate

Every `ha_call_service` invocation MUST be classified by the repository-owned
`(domain, service)` physical-risk map as `safe`, `reversible`, `consequential`,
or `protected`. Unknown pairs MUST fail closed as `protected`. Consequential and
protected calls MUST be durably parked, carrying goal lineage (goal, invocation,
depth, exposure), before any Home Assistant request, and MUST execute only
through owner per-event approval and the approvals executor's lineage read from
the verified call assertion. No standing rule SHALL approve a consequential or
protected call.

A call whose service can deliver content to a person (the `notify`, `tts`,
`persistent_notification` and other messaging or announcement service domains)
SHALL additionally be `communicate`: `ha_call_service` SHALL declare its target
(entity, device or notify service) as `recipient_params` and the message,
title and other recipient-visible service data as content-bearing arguments,
so the template-pinned test can apply; such a call is owner communication
only when every target resolves uniquely to an owner device or the owner's
notification service; any other target, including a shared speaker, a guest
device or a target it cannot resolve, is non-owner communication and SHALL
follow the non-owner rule (`REQ-capability-authorization-016`) as well as the
risk map.

Source: RFC 0038 D4, D7, D13 and owner decision 9
Scope: v1-mandatory

#### Scenario: Consequential call is parked without physical execution

- **WHEN** a goal invocation requests `lock.unlock` with no owner approval
- **THEN** the call SHALL be parked as a pending `ha_call_service` approval carrying the goal id, invocation id, depth and exposure
- **AND** it SHALL return `pending_approval` without sending a request to Home Assistant
- **AND** if the module receives such a call whose verified assertion carries no `approval_ref`, it SHALL NOT send a request to Home Assistant

#### Scenario: Approval grant permits one execution attempt

- **WHEN** an authenticated owner approves the parked action
- **THEN** the approved stored call (or the owner's edited version) SHALL be executed, never a call the model re-issues
- **AND** the approvals executor SHALL bind the action id, originating goal lineage from the verified assertion, and decided-by actor while invoking the original Home Assistant handler
- **AND** the handler SHALL perform one new, independently receipted actuation attempt

#### Scenario: Standing rule cannot approve a consequential call

- **WHEN** an owner standing rule matches a consequential or protected `ha_call_service` call
- **THEN** the call SHALL still be parked for per-event owner approval
- **AND** no Home Assistant request SHALL be sent before that approval

#### Scenario: A notification to a guest's device is non-owner communication

- **WHEN** a goal invocation calls `ha_call_service` for `notify.mobile_app_<guest_phone>` or a `tts` announcement on a shared speaker
- **THEN** the call SHALL be parked for per-event owner approval unless an owner-created standing rule matches it
- **AND** no Home Assistant request SHALL be sent before that

#### Scenario: Argument-aware execution uses the complete target and data contract

- **WHEN** Home evaluates a service invocation for physical execution
- **THEN** the following automatic-risk and exact-command conditions SHALL apply before that request can leave the process

Automatic execution of a base `reversible` call additionally MUST require a nonempty explicit `entity_id` string or list, no other target key, and complete canonical same-domain entity tokens. Area, device, label, all, malformed, mixed-domain, absent and empty targets MUST instead be treated as `consequential`. The base service map MUST remain unchanged; no protected or unknown pair may be downgraded. Explicit read-back extraction MUST accept only entity-only targets whose entire nonempty string/list consists of canonical entity tokens; bulk, extra-selector, empty or malformed forms MUST yield no inferred or partial entity set, including the pseudo-id all.

Data for automatic reversible execution MUST satisfy the repository-owned per-service key/type/bounds subset: `light.turn_on` permits brightness(integer0..255), brightness_pct(finite number0..100), color_temp_kelvin(integer1000..10000), rgb_color(list of3 integers0..255), and transition(finite number0..60seconds); `light.turn_off` permits transition only; switch/fan/humidifier turn_on/turn_off permit no data keys. Booleans SHALL NOT count as numbers. Simultaneous brightness+brightness_pct or rgb_color+color_temp_kelvin SHALL require approval. Unknown keys, typed/range violations and selector keys entity_id/area_id/device_id/label_id/target in data MUST promote to consequential rather than be dropped or normalized.

`climate.set_temperature` automatic execution MUST contain temperature only, a finite non-boolean number within inclusive16..28C or equivalent60.8..82.4F. Every target MUST have a consistent recognized existing module-cache attributes.temperature_unit of C/°C or F/°F. Missing, mixed or unknown unit context, missing temperature, out-of-range values and range/mode keys require approval. The handler MUST NOT guess Celsius, convert the request or contact HA to discover units before this gate. These constants define the conservative automatic subset, not a claim about HA's complete schema or an owner preference profile.

One detached strict JSON argument snapshot MUST bind classification, approval digest, parked arguments, outgoing payload and receipt. Non-object target/data, non-string JSON object keys, nonfinite/cyclic/non-JSON values and string leaves or object keys containing NUL or surrogate code points MUST be refused before persistence or physical dispatch without string/NULL coercion. Representable strings MUST remain exact, including Unicode scalar values and ordinary JSON control characters. Valid-JSON unsafe calls MUST park with exact original arguments and bounded owner-visible categorical reasons, including canonical unknown key names without values. No new target/data value may appear in generic logs or shared events. At physical dispatch, simultaneous explicit target and any data-carried selector MUST be refused as ambiguous rather than silently merged or dropped, even under approval; the owner may create a new unambiguous action. Exact approved data-only selector calls remain supported when target is absent and preserve honest unverified semantics.

#### Scenario: Broad reversible target is parked

- **WHEN** a base reversible call names an area, device, label, all, empty, malformed, mixed-domain or non-explicit target
- **THEN** effective risk SHALL be consequential and a pending approval SHALL retain its exact original arguments and categorical reason
- **AND** no HA request SHALL be sent before exact owner-approved execution

#### Scenario: Data cannot bypass explicit target and data-key conditions

- **WHEN** a base reversible call carries a selector, unlisted data key, wrong typed/ranged value or conflicting automatic parameters
- **THEN** it SHALL park before any HA request without dropping or coercing the data
- **AND** approved execution with both explicit target and data selector SHALL refuse an ambiguous payload without issuing a physical attempt

#### Scenario: Allowed explicit reversible call remains automatic

- **WHEN** light.turn_on names light.kitchen with brightness_pct80 and no extra selector or invalid parameter
- **THEN** it SHALL retain reversible risk and the existing independently receipted live-verification path without approval
- **AND** all other automatic reversible calls SHALL satisfy the same complete target and their service-specific data constraints

#### Scenario: Climate comfort checks retain unit meaning

- **WHEN** a climate temperature is outside the inclusive known-unit comfort range, has boolean/string type, or its target unit is missing, inconsistent or unknown
- **THEN** it SHALL park with the categorical reason without assuming Celsius or converting the payload
- **AND** exact known-unit inclusive-boundary temperature-only requests MAY remain reversible subject to the existing receipt verifier

#### Scenario: Promoted approved bulk replay remains exact and once-only

- **WHEN** the authenticated owner approves an unambiguous parked area/device/all call
- **THEN** the original handler SHALL send its exact stored command once through the existing executor task/tool/argument binding and receipt fence
- **AND** repeat/concurrent dispatch under the same approval SHALL NOT resend after a possible effect
- **AND** lack of explicit observable targets SHALL settle unverified and require reconciliation rather than claim success
- **AND** receipt/approval unavailability or failure SHALL never authorize a physical request

### Requirement: Honest Actuation Receipts

Before a Home Assistant service request leaves the process, the module MUST
insert a uniquely identified `attempting` receipt. It MUST then settle that same
receipt to `succeeded`, `failed`, or `unverified`, recording actor, goal lineage
(goal id, invocation id, depth, exposure) and approval lineage read from the
verified call assertion, requested and observed state, and any rollback hint. A
receipt write failure MUST refuse the physical call rather than act without
evidence.

Source: RFC 0038 D7, D12
Scope: v1-mandatory

#### Scenario: Verified post-condition is required for success

- **WHEN** Home Assistant accepts a service call
- **THEN** the module SHALL read every explicit entity target back from Home Assistant
- **AND** it SHALL record `succeeded` only when the declared post-condition matches
- **AND** a missing verifier, unreadable observation, or mismatch SHALL record `unverified`, require attention, and never claim success

#### Scenario: Definitive Home Assistant rejection is a failed attempt

- **WHEN** connection establishment definitively fails or Home Assistant returns an HTTP error
- **THEN** the module SHALL settle the pre-existing receipt to `failed`
- **AND** it SHALL re-raise the error after preserving the receipt

#### Scenario: Post-dispatch uncertainty is unverified and fenced from retry

- **WHEN** a request may have left the process but its response times out, resets, or cannot be parsed
- **THEN** the module SHALL attempt the declared live state read-back
- **AND** it SHALL settle the receipt to `unverified` even when the read-back matches
- **AND** the same approval id SHALL NOT issue another physical request while a prior receipt remains `attempting` or `unverified`; the owner must reconcile it and create a new approved action
- **AND** a prior `succeeded` receipt MAY be replayed to finalize approval bookkeeping without another physical request

#### Scenario: Reversible action records a rollback hint

- **WHEN** the risk map classifies an action as `reversible`
- **THEN** its receipt SHALL contain an explicit inverse service or a restore-previous-state instruction
- **AND** each retry SHALL create a distinct attempt id rather than deduplicating physical execution

### Requirement: Home Assistant Database Schema

The module SHALL provide Alembic migrations for its home-domain tables. Actuation
receipts SHALL be able to record goal lineage.

Source: RFC 0038 D7, D12
Scope: v1-mandatory

#### Scenario: Migration creates tables

- **WHEN** the Alembic migrations run
- **THEN** `ha_entity_snapshot` (entity_id TEXT PK, state TEXT, attributes JSONB, last_updated TIMESTAMPTZ, captured_at TIMESTAMPTZ) SHALL be created as the active bounded live-state cache written by the Home Assistant module
- **AND** `ha_source_health` (source TEXT PK, status TEXT, last_success_at TIMESTAMPTZ, last_error_at TIMESTAMPTZ, last_error TEXT, updated_at TIMESTAMPTZ) SHALL be created for the module's source-health lease
- **AND** `ha_command_log` (id BIGSERIAL PK, domain TEXT, service TEXT, target JSONB, data JSONB, result JSONB, context_id TEXT, issued_at TIMESTAMPTZ) SHALL be created
- **AND** `maintenance_items` SHALL be created (backing the maintenance tool suite)
- **AND** the `ha_state` predicate SHALL be seeded into `predicate_registry`
- **AND** index `ix_ha_command_log_issued_at` on `ha_command_log(issued_at)` SHALL be created
- **AND** the command log SHALL carry nullable legacy-compatible actuation receipt columns for attempt id, risk, actor, goal id, invocation id, depth, exposure, approval id, requested/observed state, status, rollback hint, failure reason, and completion time, with session id retained as a nullable legacy column

#### Scenario: Migration branch label

- **WHEN** `migration_revisions()` is called
- **THEN** it SHALL return `"home"` as the Alembic branch label
