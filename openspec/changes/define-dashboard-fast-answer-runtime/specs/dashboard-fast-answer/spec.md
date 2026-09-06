## ADDED Requirements

### Requirement: Side-Effect-Free Dashboard Fast-Answer Admission

The Switchboard SHALL admit direct fast-answer execution only for a dashboard Lane D system-plane
question whose deterministic catalog selection names Concierge and whose complete read plan resolves
to at most three calls to currently registered Concierge `dashboard_read` read-only MCP handlers. Structured admission
SHALL classify lane, scope, selected owner, and read plan without executing any terminal tool or
other effect. Every non-eligible, invalid, unsupported, or pre-effect unavailable result SHALL use
the existing Spawner continuation or its existing fail-closed outcome. Domain questions SHALL retain
the accepted `answer_question(scope="domain")` to `route.execute` path.

ID: REQ-dashboard-fast-answer-001
Source: add-dashboard-question-lane § Dashboard Chat-Widget Classification Lanes and Dashboard
Message Intent Lanes; RFC 0030 § Data Flow; design.md Decisions 1 and 5
Scope: proposed-v1

#### Scenario: Eligible system question enters the fast path without a CLI spawn

- **WHEN** a dashboard turn has an immutable message identity, structured admission returns Lane D
  with `scope="system"`, catalog selection names Concierge, and every planned tool is a currently
  registered Concierge `dashboard_read` read-only handler
- **THEN** the turn MAY execute the dashboard fast-answer phase
- **AND** the admitted plan SHALL contain at most three read calls
- **AND** its successful path uses no CLI subprocess or Spawner classification session
- **AND** admission itself invokes no tool, route, bug report, dead letter, conversation reply, or
  other terminal action

#### Scenario: Domain question keeps the accepted route.execute spine

- **WHEN** structured admission returns Lane D with `scope="domain"`
- **THEN** the fast-answer phase SHALL NOT invoke a Concierge tool or phrase an answer
- **AND** the question SHALL continue through the existing
  `answer_question(scope="domain")` to `route.execute` contract

#### Scenario: Non-answer lanes never enter direct execution

- **WHEN** admission identifies a statement, action request, bug report, or ambiguous turn
- **THEN** no fast-answer read, phrasing call, reply, write-capable tool, or direct terminal tool SHALL
  execute
- **AND** the turn SHALL preserve its existing Spawner routing, QA, clarification, dead-letter, and
  approval-gated behavior as applicable

#### Scenario: Invalid or unsupported admission falls back before effects

- **WHEN** the direct runtime is unsupported, catalog evidence is unavailable, classification fails,
  or structured output is invalid after its existing bounded schema retry
- **THEN** no fast-answer tool or reply SHALL have executed
- **AND** the pipeline SHALL use the existing Spawner classifier or its existing fail-closed outcome
- **AND** catalog unavailability SHALL NOT be converted into no match or `cannot_answer`

#### Scenario: Ineligible read plan is rejected before invocation

- **WHEN** a proposed plan contains a write-capable, unregistered, disabled-module, non-Concierge,
  direct-handler, or otherwise unauthorized tool, or contains more than three calls
- **THEN** admission SHALL reject the whole plan before any planned tool runs
- **AND** tool visibility, catalog provenance, or a model-produced name SHALL NOT grant authority

#### Scenario: Fallback never restarts work after a read begins

- **WHEN** the first registered Concierge MCP read has begun for an admitted fast answer
- **THEN** failure SHALL NOT restart the whole turn through the CLI classifier, repeat the admitted
  read plan, or choose General
- **AND** completion, failure, cancellation, or ambiguity SHALL settle under the same durable
  fast-answer runtime identity

### Requirement: Durable Cross-Process Fast-Answer Stop

One Switchboard-owned durable `fast_answer` runtime identity SHALL span catalog resolution,
structured classification, registered Concierge reads, answer phrasing, and reply settlement. The runtime SHALL be registered
against the immutable dashboard message before catalog resolution or the first provider invocation, SHALL claim the
existing pre-invoke fence once, and SHALL be addressable through Switchboard's registered
`cancel_session` MCP boundary. Confirmed cancellation SHALL require every active invoke/reply claim
to be released and a durable cancellation acknowledgement. Unprovable runtime or reply outcomes
SHALL become durable ambiguity and SHALL NOT be replayed.

ID: REQ-dashboard-fast-answer-002
Source: dashboard-conversations § Durable Dashboard Turn Control; dashboard-chat-ui § SSE Client
Integration; durable-dashboard-terminal-action-recovery REQ-dashboard-conversations-004 and
REQ-dashboard-conversations-005; design.md Decisions 2, 3, and 6
Scope: proposed-v1

#### Scenario: Runtime registration precedes catalog and provider work

- **WHEN** a dashboard turn attempts structured fast-answer admission
- **THEN** the Switchboard SHALL create one session UUID, durably register it for the exact message
  as phase `fast_answer`, register its live cancellable task, and claim the existing invoke fence
  before catalog resolution or the first provider call
- **AND** registration or claim observing prior Stop SHALL prevent every provider, MCP read,
  phrasing, and reply call

#### Scenario: Stop during classification cancels the shared runtime

- **WHEN** message-scoped Stop is persisted while structured classification is active
- **THEN** the dashboard API SHALL address the durable session through Switchboard's registered
  `cancel_session` MCP tool
- **AND** the classification task SHALL unwind without beginning a Concierge read or reply
- **AND** cancellation SHALL not be confirmed until its invoke claim is released and the durable
  acknowledgement is recorded

#### Scenario: Stop during or between reads fences remaining work

- **WHEN** Stop arrives during a Concierge MCP read or after a read settles but before phrasing
- **THEN** cancellation SHALL propagate through the active MCP request when one exists
- **AND** every later read, phrasing call, and reply claim SHALL observe the durable Stop and remain
  unstarted
- **AND** an uncertain read transport outcome SHALL not be called cancelled until it settles or is
  classified as ambiguous

#### Scenario: Stop fences phrasing and reply persistence

- **WHEN** Stop arrives during phrasing or after phrasing completes but before reply persistence wins
  its durable claim
- **THEN** the active phrasing task SHALL be cancelled or the next fence SHALL suppress it
- **AND** no assistant reply SHALL be persisted after Stop wins the reply claim
- **AND** a late provider completion SHALL be unable to cross the lost fence

#### Scenario: Repeated and concurrent Stop are idempotent

- **WHEN** two or more Stop requests address the same fast-answer message before, during, or after
  cancellation settlement
- **THEN** they SHALL converge on one durable cancellation intent and one terminal outcome
- **AND** they SHALL create no second runtime cancellation, reply, route, or replay

#### Scenario: Stop after completed reply is already finished

- **WHEN** the idempotent reply receipt and durable runtime completion won before Stop linearized
- **THEN** Stop SHALL preserve the completed answer and return the canonical already-finished meaning
- **AND** it SHALL NOT rewrite the turn as cancelled

#### Scenario: Crash or transport uncertainty is never replayed

- **WHEN** Switchboard or an MCP transport dies while the fast runtime has an active invoke or reply
  claim and durable evidence cannot prove whether it completed
- **THEN** recovery SHALL preserve any proven receipt or durably classify the turn as ambiguous
- **AND** it SHALL NOT reconstruct prompts, rerun classification, repeat reads, rephrase, or reissue
  reply persistence from process-local absence

#### Scenario: Every settled path releases the runtime

- **WHEN** fast execution completes, fails deterministically, or confirms cancellation
- **THEN** it SHALL release every invoke/reply claim, complete the durable runtime outcome, and remove
  the process-local cancellable registration
- **AND** an incomplete cleanup caused by process death SHALL be reconciled under the no-replay crash
  scenario rather than silently stamped complete

### Requirement: Typed Catalog Evidence and Selected-Owner Rule

Catalog-assisted dashboard ownership SHALL return `matched`, `no_match`, or `unavailable` rather
than one nullable tuple. A match SHALL contain at most three ordered candidates after server-held
sensitivity filtering. Each candidate SHALL carry bounded provenance and RRF ranking evidence. The
selected owner SHALL follow the deterministic same-owner rule below; raw RRF score SHALL NOT be
named, normalized, or interpreted as calibrated confidence.

ID: REQ-dashboard-fast-answer-003
Source: memory-discovery-catalog § Cross-butler search via catalog and Sensitivity filtering;
cross-butler-delegation § Domain Resolution Via Shared Catalog; design.md Decision 4
Scope: proposed-v1

#### Scenario: Matched outcome carries bounded provenance

- **WHEN** an authoritative held-sensitivity-filtered catalog search returns eligible candidates
- **THEN** `matched` SHALL return at most the first three valid ordered candidates
- **AND** each candidate SHALL contain only `catalog_id`, resolved owner, `source_schema`,
  `source_table`, `source_id`, `ranking_method="rrf"`, `rrf_score`, `semantic_rank`, and
  `keyword_rank`
- **AND** canonical memory content and stored sensitivity SHALL not be included

#### Scenario: One candidate selects its valid owner

- **WHEN** `matched` contains exactly one well-formed finite-score candidate whose resolved owner is
  currently eligible
- **THEN** `selected_owner` SHALL equal that candidate's owner
- **AND** its score SHALL remain ranking evidence rather than a confidence percentage

#### Scenario: Multiple candidates require same-owner agreement

- **WHEN** `matched` contains two or three valid candidates
- **THEN** `selected_owner` SHALL equal the first candidate's owner only when the first two candidates
  name the same owner and every candidate tied for highest score names that owner
- **AND** a cross-owner top-two result, cross-owner top-score tie, missing/non-finite score, malformed
  provenance, or ineligible owner SHALL leave `selected_owner` null
- **AND** numeric score margin SHALL never override owner disagreement

#### Scenario: Successful empty differs from unavailable

- **WHEN** the hook, server-held authority source, embedding/search dependencies, and catalog query
  all succeed and no eligible candidate remains
- **THEN** the outcome SHALL be `no_match`
- **WHEN** the hook is absent, held authority cannot be loaded, a dependency/query fails, or returned
  evidence is malformed
- **THEN** the outcome SHALL be `unavailable` with a bounded categorical reason
- **AND** neither outcome SHALL expose raw errors or withheld metadata

#### Scenario: No match declines only after question classification

- **WHEN** catalog outcome is `no_match` and side-effect-free admission later proves Lane D
- **THEN** the existing `cannot_answer` path MAY persist its honest decline and dead-letter evidence
- **AND** zero `invoke_structured` calls SHALL be made for ownership or another target
- **WHEN** the lane is not yet proven to be Lane D
- **THEN** `no_match` SHALL NOT terminate, dead-letter, or reroute the turn

#### Scenario: Selection rule is verified before enablement

- **WHEN** implementation proposes to enable selected-owner pre-resolution
- **THEN** a fixed labeled seeded catalog corpus SHALL record its digest, result limit, rule version,
  selected coverage, wrong-owner count, per-owner breakdown, and confusion matrix
- **AND** any wrong-owner selected hit SHALL block enablement
- **AND** broadening the same-owner rule SHALL require a new approved proposal rather than a threshold
  change hidden in implementation

### Requirement: Target-Owned Registered Concierge Reads

Every fast-answer data access SHALL invoke an already registered Concierge `dashboard_read`
read-only handler through the registered Switchboard-to-Concierge MCP boundary. The eligible set
SHALL be derived from current target registration, enabled module/group state, existing read-only
metadata, and a bounded fast-answer allowlist. Existing schema validation, module checks, database
role, call-time authorization, middleware, RFC 0030 views, and source-envelope rules SHALL remain in
force. Switchboard SHALL NOT import or call Concierge handlers directly, query another schema, or
use catalog provenance as canonical read authority.

ID: REQ-dashboard-fast-answer-004
Source: Non-Negotiable Rule 3; RFC 0030 §§ Exception Scope, Data Flow, and Guardrails;
butler-concierge § Read-Only Tool Surface, System-Plane Scope Boundary, Every Result Carries a Source
Envelope, and Cross-Schema Reads Only Through Sanctioned Views; design.md Decision 5
Scope: proposed-v1

#### Scenario: Registered Concierge read executes through MCP

- **WHEN** an admitted plan names a currently registered, enabled, allowlisted Concierge
  `dashboard_read` read-only tool
- **THEN** Switchboard SHALL invoke it through the registered target MCP boundary
- **AND** the target's normal input validation, module-state, schema-role, call-time, transport, and
  middleware checks SHALL run

#### Scenario: Read plan has an exact maximum

- **WHEN** structured admission proposes more than three read calls
- **THEN** the entire plan SHALL be rejected before any read starts
- **AND** an admitted plan SHALL execute each listed call at most once under the shared runtime

#### Scenario: Presentation metadata cannot grant a call

- **WHEN** discovery, visibility, catalog, or model output names a tool that is not currently
  registered and allowed by Concierge
- **THEN** the call SHALL be rejected before invocation
- **AND** no generic invoke gateway, direct `.fn()` call, imported handler, or copied schema SHALL be
  used as a fallback

#### Scenario: Disabled or write-capable tool is rejected

- **WHEN** the owning module is disabled, read-only classification is missing/uncertain, or any
  proposed tool can perform a write or external effect
- **THEN** the whole fast read plan SHALL be rejected before that tool runs
- **AND** no approval policy or write-tool wrapper SHALL be used to make it eligible

#### Scenario: Source attribution is server-derived

- **WHEN** one or more Concierge reads succeed and an answer is phrased
- **THEN** every result SHALL retain its target-produced `source.kind`, `source.ref`, and
  `source.as_of` envelope
- **AND** the persisted reply's source list SHALL be derived by server code from those validated
  envelopes rather than model prose
- **AND** missing or invalid source provenance SHALL prevent a grounded success claim

#### Scenario: Domain data cannot use the system-plane exception

- **WHEN** a question concerns the owner's finances, health, relationships, calendar, or another
  domain butler's canonical data
- **THEN** Concierge and the fast-answer path SHALL decline eligibility regardless of wording or
  catalog similarity
- **AND** the question SHALL remain on its target-owned domain `route.execute` path

### Requirement: Lane-Aware Dashboard Reply Observation

Each immutable dashboard turn SHALL have a monotonic durable `intent_lane` independent of
`target_kind`, with values `answer` and `non_answer` and nullable legacy/unknown state. A validated
answer classification SHALL persist `answer` before its first read or domain-answer dispatch; a
definitive statement, action, or bug classification SHALL persist `non_answer` before dispatch.
The SSE reply observer SHALL use 45 seconds for `answer` and 300 seconds for `non_answer`, unknown,
and legacy/null, measured from the existing observation start. Observation timeout SHALL close only
the SSE stream and SHALL NOT cancel or fail execution, suppress a late reply, or grant replay.

ID: REQ-dashboard-fast-answer-005
Source: dashboard-conversations § SSE Response Streaming and Durable Dashboard Turn Control;
durable-dashboard-terminal-action-recovery REQ-dashboard-conversations-005; design.md Decision 7
Scope: proposed-v1

#### Scenario: Answer lane uses a 45-second observation window

- **WHEN** a safe durable observation shows `intent_lane="answer"`
- **THEN** the SSE reply deadline SHALL be 45 seconds from the original reply-observation start
- **AND** learning the lane later SHALL not restart or extend that deadline

#### Scenario: Non-answer and legacy turns retain 300 seconds

- **WHEN** a safe durable observation shows `intent_lane="non_answer"`, or the lane is null, unknown,
  or from a legacy row
- **THEN** the SSE reply deadline SHALL remain 300 seconds from the original observation start
- **AND** `target_kind` SHALL not be reinterpreted as an intent lane

#### Scenario: Intent lane is immutable once known

- **WHEN** the definitive classification for an immutable dashboard message persists `answer` or
  `non_answer`
- **THEN** later routing, reply, Stop, retry observation, reconnect, or recovery SHALL not change it
- **AND** conflicting persistence SHALL fail closed rather than shorten or extend the observation
  window unpredictably

#### Scenario: Timeout text names the observation

- **WHEN** an answer or non-answer/unknown SSE observer reaches its applicable deadline without a
  persisted in-thread reply
- **THEN** `SESSION_TIMEOUT` SHALL name the durable lane and state that the stream was waiting for an
  in-thread reply
- **AND** it SHALL not claim that the model, provider, runtime, route, or tool timed out or was
  cancelled

#### Scenario: Observation timeout preserves runtime and late reply

- **WHEN** SSE emits its lane-aware timeout and `done`
- **THEN** it SHALL close only that observer, leave the conversation open, and perform no Stop,
  terminal-state, retry, replay, or failure mutation
- **AND** a reply persisted after the stream closes SHALL remain visible through normal history and
  unread refresh
- **AND** message-scoped Stop SHALL remain available while the runtime is still active

### Requirement: Fast-Answer Reply, Model Attribution, and Latency Evidence

An admitted fast answer SHALL use one shared durable runtime, one normal classification provider
call (plus only the existing pre-read schema-invalid retry), bounded registered Concierge MCP reads,
exactly one answer-phrasing provider call, and one deterministic message-derived idempotent reply
claim. Classification and phrasing SHALL resolve through the existing model catalog at the cheap tier
with distinct purpose attribution and their catalog execution timeouts. Required performance
evidence SHALL be the original hermetic fixed-latency-stub benchmark; live-provider evidence SHALL be
optional and separately authorized.

ID: REQ-dashboard-fast-answer-006
Source: dashboard-conversations § Message Data Model and Conversation Reply Channel;
docs/runtime/model-routing.md § Resolution Flow in the Spawner; about/craft-and-care/performance-
discipline.md; design.md Decisions 6, 8, and 9
Scope: proposed-v1

#### Scenario: Successful fast answer has bounded calls and one reply

- **WHEN** an eligible fast answer succeeds without a schema retry
- **THEN** it SHALL make one structured classification provider call, zero CLI spawns, only the
  zero-to-three registered reads in its admitted plan, one phrasing provider call, and one reply attempt
- **AND** exactly one assistant reply SHALL complete under the deterministic reply identity

#### Scenario: Reply identity makes repeats idempotent

- **WHEN** the same immutable message and identical reply payload reach reply persistence again
- **THEN** the existing reply receipt SHALL be returned without inserting a second message
- **WHEN** the same reply identity carries changed conversation, text, or sources
- **THEN** persistence SHALL conflict and preserve the original reply

#### Scenario: Uncertain reply is reconciled without replay

- **WHEN** reply transport or process failure leaves its write outcome unknown
- **THEN** the runtime SHALL use durable receipt evidence to preserve a proven reply or mark the turn
  pending reconciliation/ambiguous
- **AND** absence of process-local state SHALL never trigger a second reply attempt

#### Scenario: Provider phases carry distinct model purposes

- **WHEN** the classification and phrasing provider calls are resolved and recorded
- **THEN** both SHALL use the existing model catalog's cheap tier and retain their resolved runtime,
  model, catalog entry, effective tier, resolution source, and provider execution timeout
- **AND** classification SHALL record purpose `dashboard_fast_answer_classification`
- **AND** phrasing SHALL record purpose `dashboard_fast_answer_phrasing`
- **AND** both SHALL correlate to the shared runtime and dashboard request without recording prompt,
  tool argument/result, or answer content in attribution telemetry

#### Scenario: SSE observation does not replace provider timeout

- **WHEN** a fast answer has a 45-second SSE observation deadline
- **THEN** each provider call SHALL retain the execution budget resolved from the model catalog
- **AND** neither deadline SHALL be reported or persisted as the other

#### Scenario: Hermetic benchmark preserves the original latency gate

- **WHEN** the slow fast-answer benchmark runs 20 representative system-plane questions against a
  seeded database and a fixed-latency stub adapter
- **THEN** it SHALL record declared stub latency, sample count, per-run wall time, p50, p95, provider
  calls, registered tool calls, and CLI spawns
- **AND** p95 SHALL be less than 3 seconds and CLI spawns SHALL be zero
- **AND** the benchmark SHALL remain marked slow, MAY be skipped in normal CI, and SHALL record its
  result in the future implementation PR body
- **AND** the result SHALL be labeled hermetic orchestration evidence, not live-provider latency

#### Scenario: Live latency evidence is optional and separate

- **WHEN** a live-provider benchmark is separately authorized and run
- **THEN** its evidence SHALL separately name date, sample size, runtime/model/catalog/timeout tuple,
  p50, p95, and failures
- **AND** no live run is required by this capability
- **AND** no stub result SHALL be presented as live proof

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (deterministic infrastructure; reasoning in ephemeral LLM work)
- RFC 0001 (daemon lifecycle and runtime sessions)
- RFC 0002 (MCP tool surface and modules)
- RFC 0003 (Switchboard routing and ingestion)
- RFC 0007 (dashboard and API surface)
- RFC 0030 (system-plane read exception)
- `openspec/specs/dashboard-conversations/spec.md`
- `openspec/specs/dashboard-chat-ui/spec.md`
- `openspec/specs/butler-concierge/spec.md`
- `openspec/specs/memory-discovery-catalog/spec.md`
- `openspec/specs/cross-butler-delegation/spec.md`
- `openspec/changes/add-dashboard-question-lane/`
- `openspec/changes/durable-dashboard-terminal-action-recovery/`
