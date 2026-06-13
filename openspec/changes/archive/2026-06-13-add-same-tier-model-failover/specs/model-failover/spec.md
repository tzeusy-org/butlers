## ADDED Requirements

### Requirement: Same-Tier Availability Failover
The runtime SHALL support automatic same-tier failover among model catalog entries after
a catalog candidate is selected but cannot safely complete the invocation.

#### Scenario: Systemic primary failure before side effects
- **WHEN** a catalog-resolved runtime invocation fails with a systemic failover-eligible
  error before any MCP tool call is captured
- **AND** another eligible model exists in the same effective complexity tier
- **THEN** the spawner SHALL retry the logical session with the next eligible same-tier
  model
- **AND** it SHALL exclude the failed `catalog_entry_id` from the next-candidate query
- **AND** it SHALL preserve the original prompt, context, trigger source, request_id,
  and runtime session correlation for the logical session

#### Scenario: Side effects suppress failover
- **WHEN** a runtime invocation fails after one or more MCP tool calls have been captured
- **THEN** the spawner SHALL NOT automatically retry with another model
- **AND** it SHALL complete the logical session as failed
- **AND** it SHALL record that failover was suppressed because side effects were observed

#### Scenario: Unknown error suppresses failover
- **WHEN** the spawner cannot classify a runtime failure as systemic and failover-safe
- **THEN** the spawner SHALL NOT retry with another model
- **AND** it SHALL preserve the original failure behavior

#### Scenario: Guardrail termination suppresses failover
- **WHEN** a session is terminated by a runtime guardrail such as
  `degenerate_tool_loop`, `tool_call_budget_exceeded`, or `token_budget_exceeded`
- **THEN** the spawner SHALL NOT retry with another model
- **AND** the guardrail error SHALL remain the terminal session error

#### Scenario: Failover exhausted
- **WHEN** every eligible model in the effective tier has been attempted or skipped
- **AND** no attempt succeeds
- **THEN** the spawner SHALL complete the logical session as failed
- **AND** the terminal error SHALL identify that same-tier failover was exhausted
- **AND** attempt provenance SHALL include each attempted or skipped catalog entry

### Requirement: Failover Attempt Provenance
The system SHALL persist enough provenance for operators to audit model failover behavior
for a logical session.

#### Scenario: Failed primary then successful fallback
- **WHEN** the primary model fails with a failover-eligible systemic error
- **AND** a fallback model succeeds
- **THEN** operator-visible provenance SHALL identify the failed primary
  `catalog_entry_id`, the fallback `catalog_entry_id`, the failure reason, and the
  final successful model

#### Scenario: Failover suppressed by side effects
- **WHEN** failover is suppressed because captured tool calls are present
- **THEN** operator-visible provenance SHALL identify the failed `catalog_entry_id`,
  the suppression reason, and the captured tool-call count

#### Scenario: Quota skip provenance
- **WHEN** a candidate is skipped because its quota is exhausted
- **THEN** operator-visible provenance SHALL identify the skipped `catalog_entry_id`,
  the exhausted quota window, current usage, and configured limit
