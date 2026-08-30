## MODIFIED Requirements

### Requirement: Ephemeral MCP Config Generation
Each invocation SHALL generate a locked-down MCP configuration pointing exclusively at this butler's MCP server URL. The runtime session ID SHALL be appended as a query parameter to the MCP URL for tool-call-to-session correlation.
The generated runtime URL SHALL also identify that `tools/list` uses the
LLM-presentable projection. That presentation marker and the existing session
and trigger query values are untrusted correlation/presentation inputs, not
caller authentication. A runtime/model failover attempt SHALL rebuild its
tool-surface plan and adapter assets for the newly resolved tuple rather than
reusing native-discovery configuration from the preceding attempt.

ID: REQ-core-spawner-001
Source: RFC 0001 §Trigger Dispatch; RFC 0002 §Ephemeral MCP Config Generation; RFC 0027 §LLM Tool-List Projection
Scope: v1-mandatory

#### Scenario: MCP config includes only butler's server
- **WHEN** the spawner prepares an invocation
- **THEN** the `mcp_servers` dict contains exactly one entry keyed by the butler's name
- **AND** the entry's URL points to `http://localhost:<port>/sse` (or `/mcp`) with the runtime session ID as a query parameter
- **AND** the URL selects the LLM-presentable tool-list projection without adding any other MCP server

#### Scenario: Every candidate uses matching presentation capabilities

- **WHEN** a logical session starts a new attempt with a different runtime, CLI profile, or model/provider tuple
- **THEN** the attempt receives freshly prepared MCP and discovery assets for that tuple
- **AND** no native presentation mode or loaded-tool state is reused from the prior attempt

#### Scenario: Infrastructure client remains on the complete surface

- **WHEN** an existing Switchboard, connector, scheduler, dashboard, or recovery client connects without the LLM-presentation marker
- **THEN** it receives the complete registered MCP list governed by its existing endpoint contract
- **AND** the runtime projection does not remove or rename its tools

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
- **AND** presentation fallback remains ineligible unless effect evidence is complete and both effect counts are zero

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

## Source References

- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 4 (deterministic daemon and ephemeral intelligence)
- RFC 0001 (daemon lifecycle and triggers)
- RFC 0002 (MCP tool surface and modules)
- RFC 0027 (runtime tool surface discovery and exposure)
