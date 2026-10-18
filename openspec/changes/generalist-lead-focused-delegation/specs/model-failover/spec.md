## MODIFIED Requirements

### Requirement: Same-Tier Availability Failover
The runtime SHALL support automatic same-tier failover among model catalog entries after
a catalog candidate is selected but cannot safely complete the invocation. For a goal
invocation the goal runner SHALL relaunch a failed invocation at most once, as a new
invocation of the same goal, role, brief, grants and exposure on the next eligible
same-tier model; a relaunched lead SHALL advance the goal's lead epoch, which revokes the
failed attempt's token, exactly as recovery does; the relaunch SHALL count against the
goal's invocation budget; and any capability call the broker recorded for the failed
invocation SHALL suppress the relaunch, because failover never replays work.

Source: RFC 0038 D3, D10, D12, D14, D16
Scope: v1-mandatory

#### Scenario: Systemic primary failure before side effects
- **WHEN** a catalog-resolved runtime invocation fails with a systemic failover-eligible
  error before any MCP tool call is captured
- **AND** another eligible model exists in the same effective complexity tier
- **THEN** the goal runner (or, for an engineering executor outside goals, the spawner)
  SHALL retry with the next eligible same-tier model
- **AND** it SHALL exclude the failed `catalog_entry_id` from the next-candidate query
- **AND** it SHALL preserve the original dispatch intent and skip any same-tier candidate that was not fit-eligible for that intent before invoking an adapter
- **AND** for a goal invocation it SHALL launch at most one replacement invocation that
  keeps the `goal_id`, role, brief, grants and exposure and receives a new
  `invocation_id`, a new token and, for a lead, the next epoch; outside goals it SHALL
  preserve the original prompt, context, trigger source, request_id, and runtime session
  correlation

#### Scenario: A goal invocation's single relaunch also fails
- **WHEN** the one relaunch of a failed goal invocation fails as well
- **THEN** the goal runner SHALL NOT try another model
- **AND** a child invocation ends `failed` and the decision returns to its lead, while a lead's failure ends the goal `failed` with its ledger intact

#### Scenario: Broker-recorded call suppresses goal invocation failover
- **WHEN** a goal invocation fails with a failover-eligible error after the broker
  recorded at least one capability call made with that invocation's token
- **THEN** the goal runner SHALL NOT retry the invocation with another model
- **AND** it SHALL record that failover was suppressed because side effects were observed

#### Scenario: Budget refusal suppresses goal invocation failover
- **WHEN** a goal invocation fails with a failover-eligible error and the goal's budget
  is exhausted or a budget check cannot complete
- **THEN** the goal runner SHALL NOT launch another attempt
- **AND** the goal SHALL follow the budget-exhaustion path rather than same-tier failover

#### Scenario: Codex account cannot dispatch the selected provider model
- **WHEN** a catalog-resolved Codex invocation performs no tool call and rejects the selected model with the exact account-compatibility phrase `not supported when using Codex with a ChatGPT account`
- **THEN** the classifier treats the attempt as `provider_unavailable` and permits ordinary same-tier failover
- **AND** a generic unsupported-operation message without that account-specific phrase remains an unknown runtime error and suppresses failover
- **AND** this classification applies only after catalog selection; pool-free direct-adapter mode has no catalog tier and cannot enter same-tier failover

#### Scenario: Empty returned attempt fails over after token accounting
- **WHEN** a catalog-resolved runtime invocation returns no result text and no confirmed
  non-command MCP tool call after adapter and daemon-captured records are merged
- **THEN** the spawner SHALL classify the attempt as an empty-response failure even when
  the adapter reported token usage
- **AND** reported usage SHALL be recorded against the failed catalog entry before the
  spawner makes any failover decision
- **AND** same-tier failover SHALL proceed only when the merged tool-call list is empty
- **AND** command-execution evidence SHALL be preserved on the failed attempt and SHALL
  suppress failover because shell work may have produced side effects
- **AND** a return with a confirmed non-command MCP tool call SHALL remain a successful
  tool-only completion and SHALL NOT be retried

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
carrying `outcome` (`quota_skip` / `runtime_failure` / `resume_failure` / `suppressed` / `success` / `exhausted`),
`catalog_entry_id`, `attempt_index`, `failure_reason`, `error_code`, `error_message`,
`tool_call_count`, and a `logical_session_id` that ties all attempts of one logical session
together. For a goal invocation the logical session SHALL be the first attempt's
invocation: the goal runner SHALL write the rows of the attempt and its relaunch with the
first attempt's `invocation_id` as `logical_session_id`, and SHALL also record each row's
own `invocation_id` and the `goal_id`, so attempt provenance joins the goal ledger.
Operators read it via `GET /api/dispatch/attempts` and
`GET /api/settings/models/{entry_id}/attempts`.

Source: RFC 0038 D17
Scope: v1-mandatory

#### Scenario: Goal invocation attempts carry invocation lineage
- **WHEN** the goal runner writes attempt rows for a lead or child invocation and its relaunch
- **THEN** every row carries the first attempt's `invocation_id` as `logical_session_id` and its own `invocation_id`
- **AND** every row carries the invocation's `goal_id`

#### Scenario: Failed primary then successful fallback
- **WHEN** the primary model fails with a failover-eligible systemic error
- **AND** a fallback model succeeds
- **THEN** operator-visible provenance SHALL identify the failed primary
  `catalog_entry_id`, the fallback `catalog_entry_id`, the failure reason, and the
  final successful model

#### Scenario: Failover attempts carry spend evidence independently
- **WHEN** one logical session invokes multiple candidates before a fallback succeeds
- **THEN** every invoked `runtime_failure`, `suppressed`, or `success` attempt SHALL have one corresponding `public.token_usage_ledger` row whose `attempt_id` identifies that attempt
- **AND** reported provider usage SHALL be classified `measured`
- **AND** an invoked attempt with no parseable usage SHALL be classified `unmeasurable` rather than omitted or assigned zero tokens
- **AND** synthetic `quota_skip` and `exhausted` provenance rows SHALL NOT create token-usage rows because they do not represent provider invocations

#### Scenario: Failed provider resume remains non-breaker provenance
- **WHEN** a provider-native resume fails safely and the spawner retries the same candidate cold
- **THEN** the failed invocation SHALL use `outcome='resume_failure'` and SHALL carry its own token-usage evidence
- **AND** that outcome SHALL NOT count as a same-tier failover slot or a model circuit-breaker failure

#### Scenario: Failover suppressed by side effects
- **WHEN** failover is suppressed because captured tool calls are present
- **THEN** operator-visible provenance SHALL identify the failed `catalog_entry_id`,
  the suppression reason, and the captured tool-call count

#### Scenario: Quota skip provenance
- **WHEN** a candidate is skipped because its quota is exhausted
- **THEN** operator-visible provenance SHALL identify the skipped `catalog_entry_id`,
  the exhausted quota window, current usage, and configured limit

#### Scenario: Exhaustion provenance
- **WHEN** every eligible same-tier candidate has been attempted or skipped and none succeeds
- **THEN** the spawner SHALL write one terminal attempt row with `outcome='exhausted'`
- **AND** that row SHALL carry the last failed `catalog_entry_id`, the terminal error code,
  a `failure_reason` identifying same-tier failover exhaustion, and the
  `logical_session_id` tying it to the other attempts of the logical session
- **AND** downstream readers SHALL be able to detect terminal exhaustion from the explicit
  `exhausted` row rather than inferring it from the last `runtime_failure` row

#### Scenario: Fleet-wide attempt query
- **WHEN** a caller requests `GET /api/dispatch/attempts?outcome=<outcome>` without
  `session_id` or `logical_session_id`
- **THEN** the endpoint SHALL return attempt rows matching `outcome` across ALL
  sessions, ordered by `ts` (`order` query param, `asc` or `desc`, default `desc`),
  instead of requiring a session identifier up front
- **AND** an optional `reason_prefix` query param SHALL further restrict rows to
  those whose `failure_reason` starts with the given prefix — needed because
  `outcome='quota_skip'` alone conflates the monthly spend-ceiling hard block
  (`failure_reason` starting `"Monthly spend ceiling reached"`) with routine
  same-tier token-quota failovers, which are normal operation and share the same
  `outcome`
- **AND** an optional `since` query param SHALL restrict rows to `ts >= since`
- **AND** `meta.total` SHALL be the full count matching the filter (`outcome` +
  `reason_prefix` + `since`), independent of `limit`, so a caller can read an
  accurate count without fetching every row
- **AND** callers SHALL select exactly one query mode: session mode accepts
  `session_id` and/or `logical_session_id`, while fleet mode requires `outcome`
- **AND** a request with neither session selector nor `outcome` SHALL return `422`
- **AND** this mode SHALL power the `/spend` fleet-halt state (dashboard-spend-dashboard
  spec) rather than requiring a new dedicated endpoint

#### Scenario: Mixed fleet and session mode is rejected
- **WHEN** a caller requests `GET /api/dispatch/attempts` with `outcome` and either
  `session_id` or `logical_session_id`
- **THEN** the endpoint SHALL return `422` before querying attempt provenance

#### Scenario: Session selectors may be combined
- **WHEN** a caller requests `GET /api/dispatch/attempts` with both `session_id` and
  `logical_session_id`, without `outcome`
- **THEN** the endpoint SHALL use session mode and return rows matching either selector
  in `attempt_index ASC` order

#### Scenario: Malformed session identifier is rejected at the API boundary
- **WHEN** a caller supplies a blank or non-UUID `session_id`, with or without an
  `outcome` filter
- **THEN** `GET /api/dispatch/attempts` SHALL return `422` before issuing a database query
- **AND** it SHALL NOT fall through to fleet-wide outcome mode or pass the value to a SQL UUID cast
