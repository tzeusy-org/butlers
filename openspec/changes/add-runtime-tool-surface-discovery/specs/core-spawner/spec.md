## MODIFIED Requirements

### Requirement: Ephemeral MCP Config Generation
Each invocation SHALL generate a locked-down MCP configuration pointing exclusively at this butler's MCP server URL. The runtime session ID SHALL be appended as a query parameter to the MCP URL for tool-call-to-session correlation.
The canonical MCP endpoint SHALL retain its complete `tools/list` surface. The
runtime session and trigger query values are untrusted correlation inputs, not
caller authentication. After resolving each runtime/model candidate, the
spawner SHALL build an immutable adapter presentation asset containing the
attempt plan digest and exact canonical-name search corpus. The adapter SHALL
render that asset through supported public host configuration. When the selected
mode is `native_deferred`, it SHALL prepare the verified native search/load path
before model execution; when the mode is `eager_filtered`, it SHALL render every
allowed full definition and SHALL NOT prepare native search. A failover attempt
SHALL rebuild its plan and assets rather than reuse the preceding attempt's filter
or native state.

ID: REQ-core-spawner-001
Source: RFC 0001 §Trigger Dispatch; RFC 0002 §Ephemeral MCP Config Generation; RFC 0027 §Adapter-Owned Search Corpus and Model Presentation
Scope: v1-mandatory

#### Scenario: MCP config includes only butler's server
- **WHEN** the spawner prepares an invocation
- **THEN** the `mcp_servers` dict contains exactly one entry keyed by the butler's name
- **AND** the entry's URL points to `http://localhost:<port>/sse` (or `/mcp`) with the runtime session ID as a query parameter
- **AND** the URL exposes the complete canonical list without adding any other MCP server
- **AND** the separately rendered adapter asset restricts the searchable corpus to the attempt allowlist

#### Scenario: Every candidate uses matching presentation capabilities

- **WHEN** a logical session starts a new attempt with a different runtime, CLI profile, or model/provider tuple
- **THEN** the attempt receives freshly prepared MCP and discovery assets for that tuple
- **AND** no native presentation mode or loaded-tool state is reused from the prior attempt

#### Scenario: Infrastructure client remains on the complete surface

- **WHEN** an existing Switchboard, connector, scheduler, dashboard, recovery, or runtime host connects to the canonical endpoint
- **THEN** it receives the complete registered MCP list governed by its existing endpoint contract
- **AND** adapter presentation does not remove or rename canonical handlers

#### Scenario: Adapter assets carry the immutable presentation plan

- **WHEN** the spawner prepares a tool-bearing runtime/model candidate
- **THEN** its adapter asset binds attempt identity, catalog generation, enabled-module snapshot, policy, compatibility key, and canonical-name search corpus
- **AND** conflicting runtime arguments cannot replace the generated MCP or host-filter configuration
- **AND** the asset and any host-internal pagination state are never reused by another plan

### Requirement: Runtime Failure Classification
The spawner SHALL classify runtime failures before deciding whether automatic model
failover is safe.

ID: REQ-core-spawner-002
Source: [Observed] core-spawner Runtime Failure Classification; RFC 0027 §Failure and Replay Safety
Scope: v1-mandatory

#### Scenario: Systemic runtime failure is eligible
- **WHEN** a runtime adapter fails before any side-effect-capable work is observed
- **AND** the failure is classified as systemic infrastructure or provider failure
- **THEN** the spawner MAY attempt same-tier model failover if another eligible
  candidate exists

#### Scenario: Empty normal return is classified after merging tool-call evidence
- **WHEN** a runtime adapter returns normally without result text
- **THEN** the spawner SHALL merge adapter-reported tool calls with daemon-captured
  runtime-session tool calls before classifying the attempt
- **AND** when the merged records contain no non-command MCP tool call, the spawner
  SHALL treat the attempt as an empty-response failure even if token usage was reported
- **AND** when the merged records contain a confirmed non-command MCP tool call, the
  tool-only attempt SHALL remain successful and SHALL NOT trigger model failover

#### Scenario: Captured tool calls make failure ineligible
- **WHEN** captured tool calls for the failed attempt are non-empty
- **THEN** the spawner SHALL classify the failure as not failover-eligible
- **AND** it SHALL NOT start a second model attempt for the same logical session

#### Scenario: Classifier defaults closed
- **WHEN** the classifier receives an unknown exception type, ambiguous adapter error,
  or incomplete process metadata
- **THEN** it SHALL classify the failure as not failover-eligible

#### Scenario: Explicit native failure uses merged effect evidence

- **WHEN** the adapter reports a closed native transport or discovery-protocol failure
- **THEN** the classifier merges daemon MCP capture with every parsed non-MCP effect-capable host action
- **AND** presentation fallback remains ineligible unless the same candidate has a separately verified eager-capable profile, effect evidence is complete, and both effect counts are zero
- **AND** without that eager profile no presentation replay occurs; ordinary candidate failover remains governed by its separate eligibility rules

#### Scenario: Shell activity makes presentation replay ineligible

- **WHEN** a failed native presentation subattempt emitted any shell or command-execution action
- **THEN** presentation fallback is ineligible even when no MCP call was captured
- **AND** the logical session retains the failed subattempt evidence without replay

### Requirement: Logical Session Attempt Orchestration
The spawner SHALL keep automatic model failover attempts bounded and auditable.

ID: REQ-core-spawner-003
Source: [Observed] core-spawner Logical Session Attempt Orchestration; RFC 0027 §Failure and Replay Safety
Scope: v1-mandatory

#### Scenario: Successful fallback completes logical session once
- **WHEN** the primary model fails with a failover-eligible error
- **AND** a fallback model succeeds
- **THEN** exactly one logical session completion SHALL be recorded
- **AND** the session's final model SHALL be the successful fallback model
- **AND** provenance SHALL record the failed primary attempt

#### Scenario: Non-eligible failure completes without retry
- **WHEN** a runtime invocation fails with a non-failover-eligible error
- **THEN** the spawner SHALL preserve existing failure behavior
- **AND** it SHALL record no fallback invocation

#### Scenario: Attempt cap prevents infinite retry
- **WHEN** same-tier failover is active
- **THEN** the number of candidate attempts SHALL be bounded by the number of eligible same-tier catalog candidates
- **AND** no catalog entry SHALL be selected more than once for the same logical session
- **AND** one selected candidate SHALL have at most two presentation subattempts: its initial plan plus one replay-safe eager fallback

#### Scenario: Presentation fallback preserves candidate identity

- **WHEN** one candidate retries from native deferred to eager filtered under the replay-safe predicate
- **THEN** the logical session records one candidate attempt with two ordered presentation subattempts
- **AND** the retry does not consume or masquerade as a second model-catalog candidate selection

Historical candidate-selection wording preserved for archive safety: “the number of attempts SHALL be bounded by the number of eligible same-tier catalog candidates” and “no catalog entry SHALL be invoked more than once for the same logical session.” RFC 0027 narrows those sentences to candidate selection while permitting one bounded presentation fallback for the already-selected candidate.
