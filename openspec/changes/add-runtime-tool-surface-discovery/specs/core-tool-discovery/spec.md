## Purpose

Defines how each ephemeral runtime receives an LLM-eligible, cross-CLI MCP
tool surface without loading every registered tool schema into model context or
weakening existing butler, module, approval, and infrastructure boundaries.

## ADDED Requirements

### Requirement: Layered Butler Tool Surface

For every butler, the system SHALL distinguish the complete registered handler
set, the set eligible for LLM presentation, the set discoverable by that LLM,
and the subset initially loaded into model context. Each later set SHALL be a subset
of the preceding set, and no discovery mode SHALL present a tool excluded by the
butler's effective core groups, module groups, module state, type, or name.

ID: REQ-core-tool-discovery-001
Source: RFC 0027 §Layered Surface Model; heart-and-soul/architecture.md §Why Tool Surface Discipline Matters
Scope: v1-mandatory

#### Scenario: Tool discovery cannot broaden configured ownership

- **WHEN** a butler's effective configuration excludes a core or module tool group
- **THEN** tools belonging only to that group are absent from the LLM-eligible tool set
- **AND** no eager or deferred presentation can discover or invoke the unregistered handler

#### Scenario: Every exposed tool resolves to one registered handler

- **WHEN** a runtime discovers or loads a tool
- **THEN** that tool resolves to exactly one handler already registered by the owning butler
- **AND** the exposure layer does not create a second implementation of the tool

#### Scenario: Mandatory LLM capability remains discoverable

- **WHEN** an existing capability contract requires an LLM-facing tool such as `notify` to be available
- **THEN** the tool remains in the LLM-eligible and discoverable sets when its owning configuration admits it
- **AND** deferred presentation may omit its full schema initially but cannot make the capability unreachable

#### Scenario: Unclassified metadata preserves the safe baseline

- **WHEN** a registered LLM-facing tool lacks discovery metadata during migration
- **THEN** the session uses the eager-filtered compatibility behavior for that tool
- **AND** the missing classification is reported without removing the registered handler

### Requirement: LLM Visibility Preserves Existing Handler Authority

The system SHALL classify registered tools by whether they belong in an LLM's
presentation surface. Infrastructure-only handlers SHALL remain callable
through their existing MCP endpoints while remaining absent from LLM discovery.
Visibility classification SHALL not replace or weaken any handler-specific
caller validation, approval, module-state, or other call-time authority check;
omission from a list SHALL not be represented as a server security boundary.
Projection filtering SHALL occur before pagination, and every opaque cursor
SHALL be bound to the attempt plan's catalog-generation digest,
enabled-module-snapshot digest, exposure policy, and resolved compatibility-key
digest.

ID: REQ-core-tool-discovery-002
Source: RFC 0027 §LLM Visibility Classification; heart-and-soul/security.md §Session Sandboxing
Scope: v1-mandatory

#### Scenario: Infrastructure handler is hidden from an LLM session

- **WHEN** an LLM runtime requests its tool list
- **THEN** infrastructure-only handlers such as routed-request admission are absent
- **AND** the discovery receipt treats the omission as presentation filtering rather than new invocation authority

#### Scenario: Infrastructure caller retains its endpoint

- **WHEN** the Switchboard or another infrastructure caller invokes its existing MCP handler
- **THEN** the handler remains callable under the existing wire contract
- **AND** the LLM visibility policy does not remove or rename it

#### Scenario: Disabled module is unavailable to new runtime work

- **WHEN** a module is disabled before a session's tool-surface plan is created
- **THEN** that module's tools are absent from the session's discoverable set
- **AND** the call-time module-state guard still rejects a stale or forged invocation

#### Scenario: Visibility metadata cannot grant invocation authority

- **WHEN** an LLM names a tool that its presentation plan omitted
- **THEN** visibility metadata alone neither authorizes nor dispatches the call
- **AND** any direct protocol attempt remains governed by the registered handler's pre-existing call-time checks

#### Scenario: Multi-page projection reveals no hidden metadata

- **WHEN** the LLM-presentable tool list spans multiple pages
- **THEN** every page contains only definitions from the same attempt projection
- **AND** page totals, cursors, and terminal pagination state reveal no infrastructure-only name, schema, or hidden count

#### Scenario: Projection changes invalidate old cursors

- **WHEN** a cursor's catalog-generation digest, enabled-module-snapshot digest, exposure policy, or resolved compatibility-key digest differs from the active plan digest
- **THEN** the list request rejects the cursor as invalid instead of continuing with mixed or complete-surface results
- **AND** the client can restart listing from the first page of its current projection

#### Scenario: Supported transports expose the same projection

- **WHEN** equivalent runtime sessions list tools through streamable HTTP and legacy SSE
- **THEN** both transports return the same canonical LLM-presentable names for the same plan digest
- **AND** neither transport changes `tools/call` behavior

### Requirement: Per-Invocation Exposure Policy

Every tool-capable runtime invocation SHALL receive a tool-surface plan selected
after the runtime and model/provider have been resolved. The selected mode SHALL
be one of `none`, `eager_filtered`, or `native_deferred`. Operational
policy SHALL support `eager_filtered` and `auto`; an absent policy on an existing
deployment SHALL preserve `eager_filtered` behavior until explicitly migrated.

ID: REQ-core-tool-discovery-003
Source: RFC 0027 §Exposure Planning State Machine; heart-and-soul/vision.md #5
Scope: v1-mandatory

#### Scenario: Conservative policy preserves eager behavior

- **WHEN** the effective exposure policy is `eager_filtered`
- **THEN** the runtime receives the LLM-eligible tool definitions directly
- **AND** no native deferred feature is enabled

#### Scenario: Auto policy selects only a verified mode

- **WHEN** the effective exposure policy is `auto`
- **THEN** the system selects the highest-preference discovery mode verified for the resolved runtime, CLI version, and model/provider tuple
- **AND** it records the selected mode in the invocation receipt
- **AND** it selects native deferred mode only when every registered tool has an explicit presentation classification for that butler

#### Scenario: QA and healing retain no live MCP surface

- **WHEN** the trigger source is QA or healing
- **THEN** the selected mode is `none`
- **AND** the invocation receives no live butler MCP server regardless of operational exposure policy

#### Scenario: Failover recomputes the exposure plan

- **WHEN** one logical session moves to a different runtime or model/provider candidate before any side-effect-capable work
- **THEN** the system creates a new plan for the new tuple
- **AND** it does not reuse a native mode merely because the prior candidate supported it

### Requirement: Capability Negotiation and Conservative Fallback

The system SHALL treat native discovery support as proven only for a specific
runtime, CLI version, configuration dialect, and model/provider tuple. Unknown,
unsupported, malformed, or stale capability evidence SHALL select
`eager_filtered` rather than silently dropping tools, emitting unsupported
configuration, or blocking an otherwise compatible invocation.

ID: REQ-core-tool-discovery-004
Source: RFC 0027 §Capability Negotiation and Adapter Contract; craft-and-care/interfaces-and-dependencies.md §Compatibility Rules
Scope: v1-mandatory

#### Scenario: Unknown CLI version falls back safely

- **WHEN** the selected runtime binary version has no verified native-discovery profile
- **THEN** the invocation uses `eager_filtered`
- **AND** the receipt identifies unverified runtime capability as the fallback reason

#### Scenario: Model or provider lacks required host capability

- **WHEN** the CLI supports a native discovery mechanism but the resolved model/provider tuple does not
- **THEN** the native mechanism is not enabled
- **AND** the tool-capable eager-filtered path remains available

#### Scenario: Configuration dialect drift does not reach the subprocess

- **WHEN** runtime capability preparation detects that the installed CLI expects a different configuration dialect
- **THEN** it either renders a verified compatible configuration or selects the eager-filtered profile for that dialect
- **AND** it does not emit fields known only to a different CLI generation

#### Scenario: Tool use itself is unsupported

- **WHEN** a resolved runtime candidate cannot connect to and invoke the butler's MCP tools
- **THEN** the candidate is ineligible for a tool-bearing butler invocation
- **AND** discovery fallback does not misrepresent that candidate as tool-capable

### Requirement: Deferred Discovery Preserves Typed MCP Execution

When `native_deferred` is selected, the runtime SHALL initially receive bounded
namespace or server summaries instead of every deferred tool's full schema,
SHALL load only LLM-eligible matches, and SHALL ultimately invoke the original
typed MCP handler using its canonical schema and name.

ID: REQ-core-tool-discovery-005
Source: RFC 0027 §Native Deferred Contract; RFC 0002 §Tool Call Logging Proxy
Scope: v1-mandatory

#### Scenario: Deferred tool is loaded before direct invocation

- **WHEN** a native-deferred runtime needs a tool whose full definition was not initially loaded
- **THEN** discovery returns an LLM-eligible matching definition before the tool call
- **AND** the subsequent call uses the original typed input schema and canonical handler

#### Scenario: Search returns only LLM-visible matches

- **WHEN** a discovery query would lexically match both LLM-visible and infrastructure-only tools
- **THEN** only LLM-visible matches are returned
- **AND** the response does not reveal hidden tool names, descriptions, parameter names, or counts

#### Scenario: Approval-sensitive actions keep their normal path

- **WHEN** a deferred tool can require human approval or create an externally consequential side effect
- **THEN** loading the tool still yields its original direct typed MCP call
- **AND** invocation remains subject to the normal approval path

#### Scenario: Discovery does not bypass execution wrappers

- **WHEN** a loaded tool is invoked
- **THEN** existing handler authorization, module-state checks, approval gates, schema validation, tracing, and tool-call capture execute as they do for eager invocation
- **AND** discovery success alone grants no authority

### Requirement: Replay-Safe Discovery Failure Handling

The system SHALL fall back or retry a failed native-discovery invocation only
when a closed adapter failure category explicitly identifies native transport
or native discovery protocol failure and complete effect evidence proves that
no MCP tool or non-MCP side-effect-capable host action occurred. Missing,
unknown, or parser-ambiguous effect evidence SHALL block replay. Once any effect
is observed, the logical session SHALL not be automatically replayed through an
eager or alternate discovery mode.

ID: REQ-core-tool-discovery-006
Source: RFC 0027 §Failure and Replay Safety; core-spawner Runtime Failure Classification
Scope: v1-mandatory

#### Scenario: Pre-tool discovery initialization failure may fall back

- **WHEN** native discovery fails before any MCP or other side-effect-capable call is observed
- **THEN** the system can retry once with the verified eager-filtered plan
- **AND** both attempts remain part of one logical session receipt

#### Scenario: Partial tool execution blocks automatic replay

- **WHEN** deferred discovery execution fails after any MCP tool call is captured
- **THEN** the session records the failure and retained call evidence
- **AND** it does not replay the prompt through another exposure mode

#### Scenario: Plain-text response is not a discovery failure

- **WHEN** a configured tool surface produces a valid response without a tool call and no transport failure is reported
- **THEN** the response is accepted without discovery fallback
- **AND** absence of a tool call alone is not recorded as an MCP connection failure

#### Scenario: Non-MCP effect blocks automatic replay

- **WHEN** native discovery fails after a shell, file-edit, apply-patch, or other host action capable of side effects is observed
- **THEN** the session records the post-effect failure without an eager retry
- **AND** a read-looking command is still treated as effect-capable unless the host contract proves otherwise

#### Scenario: Ambiguous effect evidence fails closed

- **WHEN** the adapter cannot completely parse or reconcile MCP and non-MCP effect evidence for a failed native attempt
- **THEN** the attempt is not automatically replayed
- **AND** the receipt retains the original native failure category, sets `effect_evidence_complete=false`, and records outcome `failed_effect_unknown` without raw parser or provider text

### Requirement: Skill Loading Cannot Expand Tool Authority

The system SHALL keep runtime skills as guidance artifacts separate from the
session's LLM visibility and exposure plan. Adapter-specific skill
projection or on-demand skill search SHALL preserve the canonical skill
identity and SHALL not add, unhide, or reconfigure MCP tools.

ID: REQ-core-tool-discovery-007
Source: RFC 0027 §Skills and Tools; RFC 0002 §Skills Infrastructure
Scope: v1-mandatory

#### Scenario: Skill references a tool outside the visible set

- **WHEN** a loaded skill names a tool that is not LLM-visible for the session
- **THEN** the tool remains absent from eager and deferred discovery
- **AND** the skill text does not modify the session's tool-surface plan

#### Scenario: Multiple CLIs receive one canonical skill identity

- **WHEN** different supported runtime CLIs load the same butler skill through their native filesystem convention
- **THEN** each resolves the same canonical `.agents/skills` source
- **AND** adapter projection does not fork the skill's behavior or authority

### Requirement: Content-Blind Discovery Evidence

Every tool-bearing candidate attempt SHALL persist a bounded discovery receipt
with one or two ordered presentation subattempts. Each presentation subattempt
SHALL contain: mode and policy strings; candidate and presentation indexes;
runtime type/version/artifact digest; adapter-profile revision;
configuration dialect/digest; transport/protocol version; exact provider/model IDs;
compatibility-record digest; non-negative registered, LLM-presentable,
initial-full-definition, initial-schema-byte, initial-summary,
initial-summary-byte, loaded-definition, discovery-call, canonical-MCP-call,
and non-MCP-effect-call counts; an effect-evidence-complete boolean; plus closed
fallback and outcome enums. The receipt SHALL exclude prompts, search queries,
tool arguments, tool results, credentials, raw exception text, and full tool
schemas.
Definition counts (`registered`, LLM-presentable, initially exposed, and
loaded) SHALL count distinct canonical tool definitions. Summary counts SHALL
count distinct namespace/server summary entries. Discovery, MCP-call, and
non-MCP-effect-call counts SHALL count operation occurrences. Byte fields SHALL
count canonical compact sorted-key UTF-8 bytes. Initially exposed bytes cover
full definitions serialized before model execution, loaded counts cover full
deferred definitions imported afterward, and summaries are recorded separately.
Fallback category and outcome SHALL use the closed vocabularies in RFC 0027;
absent numeric values are zero rather than null. Fallback category SHALL be
one of `none`, `policy_forced_eager`, `classification_incomplete`,
`tuple_unverified`, `tuple_unsupported`, `profile_mismatch`,
`preparation_failed`, `native_transport_failed`, or `native_protocol_failed`.
Outcome SHALL be one of `prepared`, `completed`, `failed_pre_effect`,
`failed_post_effect`, `failed_effect_unknown`, `fallback_succeeded`, or
`fallback_failed`.

ID: REQ-core-tool-discovery-008
Source: RFC 0027 §Observability and Privacy; craft-and-care/observability-and-operations.md
Scope: v1-mandatory

#### Scenario: Successful deferred session records bounded evidence

- **WHEN** a deferred-discovery session completes
- **THEN** its process evidence identifies the selected mode and aggregate discovery counts
- **AND** an operator can distinguish eager, deferred, and no-tool attempts without reading session content

#### Scenario: Fallback reason is durable

- **WHEN** `auto` policy selects `eager_filtered` because native support is unavailable or failed before execution
- **THEN** the receipt records a closed-category fallback reason
- **AND** it does not persist provider error text or discovery search text

#### Scenario: Receipt retention follows process-log policy

- **WHEN** discovery evidence reaches its configured process-log retention limit
- **THEN** it is deleted with the owning process receipt
- **AND** the feature creates no separate unbounded discovery-history store

#### Scenario: Candidate and presentation attempts remain distinguishable

- **WHEN** one model candidate performs native presentation and one eager fallback before a later model failover
- **THEN** the receipt preserves ordered candidate-attempt and presentation-subattempt identities
- **AND** no later upsert overwrites an earlier presentation outcome

### Requirement: Native Mode Verification Gate

The system SHALL keep a runtime tuple on `eager_filtered` until both a
credential-free structural suite and an authorized representative-runtime
evaluation have passed a versioned, repo-owned conformance manifest covering
discovery protocol, canonical invocation, infrastructure-tool omission from
model-visible schemas, approval preservation, receipt parsing, and fallback
behavior. Enabling native
mode SHALL require an immutable compatibility record tied to the tested CLI
runtime type, executable artifact digest/identity/exact version, adapter-profile
revision, configuration dialect and normalized digest, transport/protocol
version, and exact provider/model IDs. The record SHALL add
conformance-manifest, fixture, and result digests plus verification time as
evidence fields outside that compatibility key.

ID: REQ-core-tool-discovery-009
Source: RFC 0027 §Conformance and Rollout Gate; craft-and-care/performance-discipline.md §Core Rules
Scope: v1-mandatory

#### Scenario: Unverified runtimes use the compatible fallback

- **WHEN** no passing compatibility record exists for the resolved runtime tuple
- **THEN** `auto` policy selects `eager_filtered`
- **AND** experimental feature presence or a matching version prefix is insufficient by itself

#### Scenario: Infrastructure definitions remain absent from LLM presentation

- **WHEN** a runtime tuple is evaluated against the synthetic large-tool server
- **THEN** the evaluation proves an LLM-visible tool can be found and invoked
- **AND** it proves infrastructure-only names and schemas remain absent from deferred discovery without claiming direct-call denial

#### Scenario: Deferred presentation materially reduces initial schema load

- **WHEN** a runtime tuple is proposed for native-mode enablement
- **THEN** its synthetic large-surface evaluation shows at least a 50 percent reduction in initially serialized tool-definition bytes relative to its eager-filtered baseline
- **AND** every required behavioral and safety scenario remains passing

#### Scenario: Native presentation introduces no behavioral regression

- **WHEN** native and eager modes run the same required deterministic and representative manifest cases
- **THEN** native mode produces no new task, approval, attribution, replay, or final-outcome failure
- **AND** any such difference blocks admission while latency, cache, and total-token changes remain reported diagnostic evidence rather than hidden averages

#### Scenario: Admission calculation is reproducible

- **WHEN** a reviewer repeats the compatibility evaluation from the checked-in manifest
- **THEN** canonical compact sorted-key UTF-8 serialization produces the same eager and deferred byte totals
- **AND** the report names every required scenario, expected outcome, executed sample, retry, cache condition, and malformed-schema disposition

#### Scenario: Runtime changes invalidate prior compatibility proof

- **WHEN** the runtime binary or configuration dialect changes from the tested compatibility record
- **THEN** `auto` policy returns that tuple to `eager_filtered`
- **AND** native mode remains disabled until the new tuple passes the verification gate

#### Scenario: Every compatibility key field is binding

- **WHEN** any runtime type, artifact, identity, version, adapter revision, configuration, transport, protocol, provider, or model key field differs from the verified record
- **THEN** the record does not authorize native presentation for that tuple
- **AND** manifest, fixture, result, and verification-time evidence remain provenance rather than substitute key fields

#### Scenario: Implementation completion is canary-ready

- **WHEN** the implementation and one native compatibility record satisfy this changeset
- **THEN** the system is ready for a separately authorized live canary but does not change a live butler's policy automatically
- **AND** canary execution, rollback ownership, and production expansion remain operator-controlled actions

## Source References

- Non-Negotiable Rule 2 (modules only add tools)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (deterministic daemon and ephemeral intelligence)
- Non-Negotiable Rule 5 (operational tuning is DB-backed)
- Non-Negotiable Rule 6 (manifesto-governed scope)
- RFC 0002 (MCP tool surface and modules)
- RFC 0005 (observability and telemetry)
- RFC 0027 (runtime tool surface discovery and exposure)
