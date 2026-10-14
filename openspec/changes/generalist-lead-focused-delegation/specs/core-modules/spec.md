## MODIFIED Requirements

### Requirement: Tool Metadata for Approvals
Every module SHALL declare, for each LLM-presentable tool it registers, the authorization metadata that `capability-authorization` requires: owner domain, effect class, output trust, output sensitivity, resource parameters, safety arguments, party arguments (or an undeterminable declaration) for every tool whose call can deliver content to or invite a party, whatever its effect class, approval policy, idempotency mode and owner-authority flag. Modules MAY override `tool_metadata()` to return `dict[str, ToolMeta]` mapping tool names to `ToolMeta(arg_sensitivities)` instances; the returned mapping SHALL declare which tool arguments are safety-critical for the approvals subsystem, and those arguments SHALL be the tool's declared safety arguments. A tool without complete metadata SHALL NOT be grantable to any goal invocation.

Source: RFC 0038 D4; RFC 0021; RFC 0027
Scope: v1-mandatory

#### Scenario: Module declares sensitive arguments
- **WHEN** a module returns `{"my_tool": ToolMeta(arg_sensitivities={"password": True})}` from `tool_metadata()`
- **THEN** the approvals subsystem uses these declarations for sensitivity classification
- **AND** `password` is among the safety arguments an approval of `my_tool` pins

#### Scenario: No metadata declared
- **WHEN** a module registers an LLM-presentable tool without complete authorization metadata
- **THEN** the catalog check fails in CI and the broker never includes the tool in a grant
- **AND** the approvals subsystem's heuristic sensitivity fallback, which still applies when `tool_metadata()` returns an empty dict, does not make the tool grantable

### Requirement: MCP Tools Raise on Invalid Input
MCP tools registered by modules SHALL raise an exception on invalid input rather than returning a success-shaped empty payload. "Invalid input" includes (but is not limited to): required arguments missing or `null`, arguments of the wrong type, and values that fail schema validation (e.g. an empty string where a non-empty name is required). The exception SHALL be one that the broker returns to the calling goal invocation as a tool-call error, not a silent empty result.

The motivating failure mode: a looping agent that invokes a tool with `null` arguments must see a typed error, not an empty list. Returning an empty list makes the failing call indistinguishable from a well-formed call that simply matched nothing, and rewards the agent for retrying.

This rule is cross-cutting: it applies to every MCP tool in every module. It establishes the normative contract so new tools comply by construction; existing tools are brought into compliance incrementally as they are touched (the `memory_entity_resolve` tool that triggered the motivating incident already complies — see `module-memory`).

Source: RFC 0038 D7 and D10
Scope: v1-mandatory

#### Scenario: Missing required argument raises
- **WHEN** a module's MCP tool is invoked with a required argument missing or set to `null`
- **THEN** the tool SHALL raise an exception (e.g. `ValueError`, `TypeError`, or a module-specific `InvalidInputError`)
- **AND** SHALL NOT return an empty-success payload such as `[]`, `{}`, or `None`

#### Scenario: Empty-success payload is reserved for well-formed no-match
- **WHEN** a search-style MCP tool is invoked with valid, well-formed arguments that simply do not match any record
- **THEN** the tool MAY return an empty collection (e.g. `[]`) to indicate "no results for a valid query"
- **AND** this is the ONLY condition under which an empty-success payload is permitted

#### Scenario: Raised exception surfaces as a tool-call error to the agent
- **WHEN** a tool raises per this requirement during a goal invocation's call
- **THEN** the broker SHALL return the exception to the invocation as a failed tool call and record the failure against the invocation, not as a silent empty result
- **AND** the failed call SHALL still count toward the goal's tool-call budget, so identical failing retries exhaust the budget rather than loop indefinitely

## REMOVED Requirements

### Requirement: QA Error Relay Module
**Reason**: RFC 0038 D7 deletes the generic `route` tool, the only transport this requirement allows the relay to use, and its "Switchboard self-report uses its existing MCP route tools" scenario SHALL no longer hold; the requirement is restated over a service-plane relay.

**Migration**: See "QA Error Relay Over the Service Plane" (`REQ-core-modules-005`) in this spec. Tests citing `REQ-core-modules-003` move to `REQ-core-modules-005`.

## ADDED Requirements

### Requirement: QA Error Relay Over the Service Plane
Every roster butler SHALL declare the self_healing module and make its shared self-healing guidance reachable as domain guidance. The module SHALL register report_error and read-only get_healing_status, relay error reports to QA only through a Switchboard service-plane relay endpoint that is not LLM-presentable, and perform no local investigation dispatch, redispatch, worktree or shared recovery operation. When a goal invocation calls report_error, the reporting goal and invocation SHALL come from the call's verified broker assertion. Relay acceptance SHALL mean actual target reception rather than investigation or publication. Existing canonical identity, sensitive metadata, configuration and independent QA ownership SHALL remain authoritative.

ID: REQ-core-modules-005
Source: RFC 0038 D7 and D18; accepted RFC 0015 centralized pipeline; roster/qa/MANIFESTO.md; archived qa-staffer D3 non-blocking relay contract
Scope: v1-mandatory

#### Scenario: Every roster exposes declared relay tools and guidance
- **WHEN** any current roster butler starts with healthy declared self_healing configuration
- **THEN** report_error and get_healing_status are registered under its canonical daemon identity and its shared skill is reachable
- **AND** retry_healing is not registered by this relay module
- **AND** report_error error_message, traceback and context keep their sensitive metadata

#### Scenario: Accepted QA reception is relayed truthfully
- **WHEN** registered report_error receives valid structured input and QA accepts the report_finding call relayed through Switchboard's service plane
- **THEN** the reporter returns accepted=true with its fingerprint and a relay-reception message
- **AND** the existing fingerprint/severity computation, allow_stale=true and optional context omission are preserved
- **AND** no local investigation or claim of a dispatched agent, PR or deployed fix is produced

#### Scenario: Wrapped target rejection is not acceptance
- **WHEN** the service-plane relay returns a tool error, rejected target acceptance, empty or malformed target payload, or relay error
- **THEN** report_error returns accepted=false with an explicit unavailable or rejected outcome
- **AND** it creates no local investigation or automatic ambiguous retry

#### Scenario: Missing QA or Switchboard remains bounded and visible
- **WHEN** the Switchboard client, QA registration or registry query is unavailable, or a relay exceeds its deadline
- **THEN** report_error returns accepted=false within the overall two-second relay budget
- **AND** ordinary caller work and error evidence remain available without per-butler fallback

#### Scenario: Local active history cannot suppress centralized relay
- **WHEN** a valid report matches an existing legacy active healing attempt and QA can receive it
- **THEN** report_error still relays the finding to QA's authoritative triage boundary
- **AND** it does not locally create, mutate or redispatch a healing attempt

#### Scenario: Disabled and legacy configuration remain compatible
- **WHEN** relay admission is disabled or legacy threshold keys are supplied
- **THEN** disabled admission gives an explicit rejection without relaying
- **AND** accepted legacy dispatch threshold keys cannot alter QA policy or enable a local dispatcher

#### Scenario: Relay startup and shutdown do not run shared investigation cleanup
- **WHEN** a declared relay module starts or shuts down with an available pool
- **THEN** it performs no shared attempt recovery, worktree reaping or watchdog dispatch
- **AND** the central QA lifecycle and shared utility interfaces remain unchanged

#### Scenario: Switchboard self-report uses the same relay in-process
- **WHEN** Switchboard's declared relay reports an error
- **THEN** it hands the finding to the same QA relay path in-process, without an external self-client and without the deleted generic `route` tool
- **AND** the QA call follows the same normalization, identity and non-dispatch rules

#### Scenario: QA self-report preserves the recursion barrier
- **WHEN** QA's relay reports a QA-origin finding whose source provenance is unknown, a `qa` or `healing` Spawner session, or a goal of QA staffer origin
- **THEN** the existing QA meta/self-recursion handling remains authoritative
- **AND** the relay creates no local recursive investigation

#### Scenario: A goal's report carries verified lineage
- **WHEN** a goal invocation calls a domain's report_error through the broker
- **THEN** the report names that domain's canonical identity as reporter and the `goal_id` and `invocation_id` from the verified assertion as its source
- **AND** no session identifier asserted in the call's arguments or URL is trusted as provenance

#### Scenario: Read-only status is history rather than deployment proof
- **WHEN** get_healing_status queries a fingerprint or the reporter's recent attempts
- **THEN** it preserves the existing read-only status query behavior
- **AND** relay guidance distinguishes reception, investigation, PR merge and deployment rather than treating a merged PR as a deployed fix
