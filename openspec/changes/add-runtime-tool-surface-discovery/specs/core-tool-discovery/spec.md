## ADDED Requirements

### Requirement: Layered Butler Tool Surface

For every butler, the system SHALL distinguish the complete registered handler
set, the set eligible for LLM presentation, the set discoverable by that LLM,
and the subset initially loaded into model context. Each later set SHALL be a subset
of the preceding set, and no discovery mode SHALL present a tool excluded by the
butler's effective core groups, module groups, module state, type, or name.
Each attempt SHALL materialize the latter sets as an immutable adapter artifact
before the runtime host serializes definitions into model context or native
search. The artifact SHALL distinguish the bounded searchable corpus from the
potentially smaller set of full definitions loaded initially.

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
presentation surface. FastMCP `tools/list` SHALL remain the complete registered
protocol surface over streamable HTTP and SSE. Infrastructure-only handlers
SHALL remain callable through that endpoint while the per-attempt adapter
artifact omits them from model serialization and native search. The artifact
SHALL contain an exact canonical-name allowlist bound to attempt identity,
catalog-generation digest, enabled-module-snapshot digest, exposure policy, and
resolved compatibility-key digest. Visibility classification SHALL not replace
or weaken any handler-specific caller validation, approval, module-state, or
other call-time authority check; model-visible omission SHALL not be
represented as a server security boundary. Host-internal enumeration and
pagination SHALL remain invocation-local and SHALL NOT be represented as a
Butlers-owned presentation cursor.

ID: REQ-core-tool-discovery-002
Source: RFC 0027 §LLM Visibility Classification, §Adapter-Owned Search Corpus and Model Presentation; heart-and-soul/security.md §Session Sandboxing
Scope: v1-mandatory

#### Scenario: Infrastructure handler is hidden from an LLM session

- **WHEN** an adapter prepares model-visible tools for an LLM runtime
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

- **WHEN** a runtime host internally pages a complete canonical list containing hidden sentinels
- **THEN** its model-visible eager input or native search index contains only definitions from the immutable attempt allowlist
- **AND** no page, terminal state, or host artifact reveals an infrastructure-only name, schema, description, parameter, or hidden count to the model

#### Scenario: Projection changes invalidate old cursors

- **WHEN** a host has internal pagination state from an earlier attempt or the catalog generation, module snapshot, policy, or compatibility key changes
- **THEN** the spawner creates a fresh adapter artifact and does not reuse the earlier host state or allowlist
- **AND** the new attempt cannot continue with mixed-plan or complete-surface model input
- **AND** any observed cross-attempt cursor/cache continuation fails conformance and makes the tuple ineligible

#### Scenario: Supported transports expose the same projection

- **WHEN** equivalent runtime sessions connect through streamable HTTP and legacy SSE
- **THEN** both transports return the same complete canonical FastMCP names
- **AND** equivalent adapter plans produce the same model-visible names and schemas regardless of transport
- **AND** neither transport nor adapter changes `tools/call` behavior

#### Scenario: Canonical MCP listing remains complete

- **WHEN** any MCP client calls canonical `tools/list`
- **THEN** it receives the complete registered list allowed by startup registration and transport semantics
- **AND** LLM visibility metadata does not alter that protocol response

#### Scenario: Complete MCP listing is not a presentation fallback

- **WHEN** an adapter cannot prove a supported public host filter for the resolved tuple
- **THEN** the tuple is ineligible for tool-bearing work
- **AND** the complete canonical list is not serialized to the model as a fallback

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
- **THEN** an eager-capable adapter renders the complete LLM-eligible definitions directly into model context
- **AND** no native deferred feature is enabled
- **AND** a tuple whose host mandates native deferral is ineligible under this policy

#### Scenario: Auto policy selects only a verified mode

- **WHEN** the effective exposure policy is `auto`
- **THEN** the system selects the highest-preference discovery mode verified for the resolved runtime, CLI version, and model/provider tuple
- **AND** it records the selected mode in the invocation receipt
- **AND** it selects native deferred mode only when every registered tool has an explicit presentation classification and the profile can represent every allowed tool's load posture
- **AND** otherwise it selects a separately verified eager profile/candidate or treats the tuple as ineligible

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
unsupported, malformed, or stale native evidence SHALL select a separately
verified `eager_filtered` profile/candidate when one is available, otherwise
make the tuple ineligible, rather than silently dropping tools or emitting
unsupported configuration. Unknown or missing model-presentation
filter evidence SHALL make the tuple ineligible rather than expose the complete
MCP list. The compatibility profile
SHALL describe the public allowlist dialect, canonical-to-host name mapping,
whether filtering changes model availability or only permission, eager/native
controllability, native granularity, invocation-local host-pagination behavior,
native search result-limit/ordering behavior, and parser/receipt support. A
tuple without a verified model-presentation boundary SHALL be ineligible for
tool-bearing work.

ID: REQ-core-tool-discovery-004
Source: RFC 0027 §Capability Negotiation and Adapter Contract; craft-and-care/interfaces-and-dependencies.md §Compatibility Rules
Scope: v1-mandatory

#### Scenario: Unknown CLI version falls back safely

- **WHEN** the selected runtime binary version has no verified native-discovery profile
- **THEN** the invocation uses a separately verified `eager_filtered` profile/candidate when one exists, otherwise the tuple is ineligible
- **AND** the receipt identifies unverified runtime capability as the fallback reason

#### Scenario: Model or provider lacks required host capability

- **WHEN** the CLI supports a native discovery mechanism but the resolved model/provider tuple does not
- **THEN** the native mechanism is not enabled
- **AND** a separately verified eager-filtered profile/candidate is used when available, otherwise the tuple is ineligible

#### Scenario: Configuration dialect drift does not reach the subprocess

- **WHEN** runtime capability preparation detects that the installed CLI expects a different configuration dialect
- **THEN** it either renders a verified compatible configuration or selects the eager-filtered profile for that dialect
- **AND** it does not emit fields known only to a different CLI generation

#### Scenario: Tool use itself is unsupported

- **WHEN** a resolved runtime candidate cannot connect to and invoke the butler's MCP tools
- **THEN** the candidate is ineligible for a tool-bearing butler invocation
- **AND** discovery fallback does not misrepresent that candidate as tool-capable

#### Scenario: Adapter without a verified presentation boundary is ineligible

- **WHEN** an adapter/host tuple cannot prove that its public configuration keeps non-plan definitions out of model context and native search
- **THEN** the tuple is ineligible for tool-bearing work
- **AND** the complete canonical MCP list is not used as a presentation fallback

#### Scenario: Eager adapter presentation is structurally verified

- **WHEN** a tuple claims `eager_filtered` compatibility
- **THEN** conformance proves its public host filter serializes every allowed definition eagerly and no hidden definition
- **AND** a host that mandates native deferral does not claim eager compatibility

#### Scenario: Native granularity represents every load posture

- **WHEN** a tuple claims native-deferred compatibility
- **THEN** its exact profile proves the host can preserve every allowed tool's eager or deferred load posture
- **AND** an `all_deferred` host is ineligible when any presentable tool requires eager loading
- **AND** the planner uses a separately verified eager candidate or treats the tuple as ineligible

### Requirement: Native Tool Search Loads Typed MCP Definitions on Demand

When `native_deferred` is selected, the adapter SHALL render a plan-bound host
artifact whose native search index contains only the canonical corpus. The
runtime SHALL initially receive bounded namespace or server summaries instead
of every deferred tool's full schema. For fixed manifest queries, search SHALL
return every intended eligible tool within the profile-declared result limit,
return no tool outside the immutable corpus, exclude infrastructure-only tools,
permit query refinement after a miss without widening the corpus, load the
selected full typed definition on demand, and ultimately invoke the original
MCP handler using its canonical schema and name. Extra eligible matches and
precision SHALL be reported diagnostics unless a separate threshold is
approved. Host-internal MCP pagination SHALL remain invocation-local and outside
the model-visible contract.

ID: REQ-core-tool-discovery-005
Source: RFC 0027 §Native Tool Search and Deferred Loading Contract; RFC 0002 §Tool Call Logging Proxy
Scope: v1-mandatory

#### Scenario: Deferred tool is loaded before direct invocation

- **WHEN** a native-deferred runtime needs a tool whose full definition was not initially loaded
- **THEN** discovery returns an LLM-eligible matching definition before the tool call
- **AND** the subsequent call uses the original typed input schema and canonical handler

#### Scenario: Search returns only LLM-visible matches

- **WHEN** a discovery query would lexically match both LLM-visible and infrastructure-only tools
- **THEN** only LLM-visible matches are returned
- **AND** the response does not reveal hidden tool names, descriptions, parameter names, or counts

#### Scenario: Search miss can be refined without widening the corpus

- **WHEN** an initial discovery query does not return the required eligible tool
- **THEN** the runtime may refine the query within the same immutable corpus
- **AND** refinement does not expose the complete MCP list or any tool outside that corpus

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
or native discovery protocol failure, the same candidate has a separately
verified eager-capable profile, and complete effect evidence proves that no MCP
tool or non-MCP side-effect-capable host action occurred. `preparation_failed`
MAY render eager assets without process replay only when that same profile
exists. Missing, unknown, or parser-ambiguous effect evidence SHALL block
replay. Without the verified eager-capable profile, no presentation fallback
occurs. Once any effect is observed, the logical session SHALL not be
automatically replayed through an eager or alternate discovery mode.

ID: REQ-core-tool-discovery-006
Source: RFC 0027 §Failure and Replay Safety; core-spawner Runtime Failure Classification
Scope: v1-mandatory

#### Scenario: Pre-tool discovery initialization failure may fall back

- **WHEN** native discovery fails before any MCP or other side-effect-capable call is observed
- **THEN** the system can retry at most once with the same candidate's
  separately verified eager-filtered plan only when complete zero-effect
  evidence exists, otherwise no presentation replay occurs
- **AND** if a retry occurs, both attempts remain part of one logical session
  receipt

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

- **WHEN** `auto` policy selects a verified `eager_filtered` profile/candidate because native support is unavailable or failed before execution
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

### Requirement: Native Tool Search Verification Gate

The system SHALL keep a runtime tuple out of `native_deferred` until both a
credential-free structural suite and an authorized representative-runtime
evaluation have passed a versioned, repo-owned conformance manifest covering
discovery protocol, canonical invocation, infrastructure-tool omission from
model-visible schemas, complete canonical HTTP/SSE listing, public host-filter
behavior, invocation-local host pagination, approval preservation, receipt
parsing, and fallback behavior. Under `auto`, an unadmitted native tuple SHALL
select a separately verified eager-filtered profile/candidate when one is
available, otherwise it is ineligible. Admitting native Tool Search SHALL
require an immutable compatibility record tied to the tested CLI
runtime type, executable artifact digest/identity/exact version, adapter-profile
revision, configuration dialect and normalized digest, transport/protocol
version, and exact provider/model IDs. The record SHALL add
conformance-manifest, fixture, and result digests plus verification time as
evidence fields outside that compatibility key.

ID: REQ-core-tool-discovery-009
Source: RFC 0027 §Tool Search Conformance and Rollout Gate; craft-and-care/performance-discipline.md §Core Rules
Scope: v1-mandatory

#### Scenario: Unverified runtimes use the compatible fallback

- **WHEN** no passing compatibility record exists for the resolved runtime tuple
- **THEN** `auto` selects a separately verified eager profile/candidate when one exists, otherwise the tuple is ineligible
- **AND** experimental feature presence or a matching version prefix is insufficient by itself

#### Scenario: Infrastructure definitions remain absent from LLM presentation

- **WHEN** a runtime tuple is evaluated against the synthetic large-tool server
- **THEN** canonical HTTP/SSE listing remains complete while the adapter's eager/native host artifact omits every infrastructure-only hidden sentinel
- **AND** the evaluation proves an LLM-visible tool can be found and invoked
- **AND** it proves infrastructure-only names and schemas remain absent from deferred discovery without claiming direct-call denial

#### Scenario: Native search materially reduces initial schema load

- **WHEN** a runtime tuple is proposed for native Tool Search admission
- **THEN** its synthetic large-surface evaluation shows at least a 50 percent reduction in initially serialized tool-definition bytes relative to its eager-filtered baseline
- **AND** every required behavioral and safety scenario remains passing

#### Scenario: Native search finds intended tools within its bounded result set

- **WHEN** a runtime tuple runs the manifest's representative discovery queries and miss-refinement cases
- **THEN** every required intended tool appears within the profile-declared result limit and is loadable through its typed definition
- **AND** every result belongs to the immutable corpus and infrastructure-only matches remain absent
- **AND** extra eligible matches and precision are reported diagnostics unless a separate threshold is approved

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
- **THEN** `auto` invalidates native and selects a separately verified eager profile/candidate when one exists, otherwise the tuple is ineligible
- **AND** native mode remains disabled until the new tuple passes the verification gate

#### Scenario: Every compatibility key field is binding

- **WHEN** any runtime type, artifact, identity, version, adapter revision, configuration, transport, protocol, provider, or model key field differs from the verified record
- **THEN** the record does not authorize native presentation for that tuple
- **AND** manifest, fixture, result, and verification-time evidence remain provenance rather than substitute key fields

#### Scenario: Implementation completion is canary-ready

- **WHEN** the implementation and one native compatibility record satisfy this changeset
- **THEN** the system is ready for a separately authorized live canary but does not change a live butler's policy automatically
- **AND** canary execution, rollback ownership, and production expansion remain operator-controlled actions
