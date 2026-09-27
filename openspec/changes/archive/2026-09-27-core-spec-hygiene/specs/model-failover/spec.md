## MODIFIED Requirements

### Requirement: Failover Attempt Provenance
The system SHALL persist enough provenance for operators to audit model failover behavior
for a logical session. As built, attempt provenance is written best-effort to the
`public.model_dispatch_attempts` table (migration `core_104`): one row per attempt or skip,
carrying `outcome` (`quota_skip` / `runtime_failure` / `resume_failure` / `suppressed` / `success` / `exhausted`),
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
