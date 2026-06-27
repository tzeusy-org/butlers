# model-failover

## Purpose

Same-tier availability failover semantics for model catalog candidates. The model catalog
selects one winning model per effective complexity tier, but when that selected model is
temporarily unavailable, misconfigured, over quota, or its CLI fails systemically before
doing useful work, the whole butler session need not fail — a lower-priority model in the
same tier can safely handle the request. This capability defines when the runtime retries a
logical session against the next eligible same-tier model, when failover MUST be suppressed
to avoid duplicating side effects, and what attempt provenance operators can audit.

Failover is deliberately scoped to systemic runtime failures and pre-invocation blocks.
Retrying after user/event work has started can duplicate side effects, so automatic failover
applies only when the failed attempt produced no captured tool calls and no other side-effect
evidence.

## Requirements

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
for a logical session. As built, attempt provenance is written best-effort to the
`public.model_dispatch_attempts` table (migration `core_104`): one row per attempt or skip,
carrying `outcome` (`quota_skip` / `runtime_failure` / `suppressed` / `success` / `exhausted`),
`catalog_entry_id`, `attempt_index`, `failure_reason`, `error_code`, `error_message`,
`tool_call_count`, and a `logical_session_id` that ties all attempts of one logical session
together. Operators read it via `GET /api/dispatch/attempts` and
`GET /api/settings/models/{entry_id}/attempts`.

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

## Source References
- Non-Negotiable Rule 4 (The daemon is deterministic infrastructure; intelligence is in ephemeral LLM CLI instances — failover orchestration is deterministic daemon behavior wrapping the ephemeral runtime invocation)
- RFC 0001 (Daemon Lifecycle and Triggers; the spawner orchestrates ephemeral runtime invocations for a logical session)
- RFC 0005 (Observability and Telemetry; attempt provenance is operator-visible auditing of failover behavior)
- RFC 0006 (Database Schema and Isolation; any new public write surface for attempt provenance must update the public-schema write authorization matrix)
