# Chronicler API — Spec Delta for recover-missing-day-close-coverage

## ADDED Requirements

### Requirement: Manual Historical Day-Close Regeneration

Manual historical regeneration SHALL execute inside the owning Chronicler
daemon through the existing MCP control boundary, reuse the scheduled
`chronicler_day_close` Tier-2 prompt/bundle and deterministic writer, and remain
notification-silent. The dashboard-facing result SHALL contain only safe
cache/admission metadata.

#### Scenario: Split-process dashboard reaches the owning daemon

- **WHEN** the dashboard refresh endpoint receives a valid settled `(date, tz)`
- **THEN** it SHALL call a Chronicler-only daemon control over MCP
- **AND** the daemon SHALL derive the exact manual-refresh trigger source,
  preserve configured complexity, invoke the existing token-bounded day-close
  path, run normal cache admission, and verify the exact coverage witness
- **AND** the dashboard SHALL NOT depend on an in-process spawner callback

#### Scenario: Manual regeneration is owner-silent

- **WHEN** a day-close session executes with the daemon-derived
  `api:day_close_refresh:<date>` trigger source
- **THEN** the Chronicler MCP boundary SHALL permit only the
  `chronicler_day_close_bundle` evidence read for that runtime session
- **AND** every other core or module tool SHALL return a non-retryable
  suppressed outcome before its handler executes
- **AND** this SHALL prevent notification, reminder, scheduler, child-trigger,
  routing, and deferred side paths independently of prompt compliance
- **AND** `schedule:chronicler_day_close` SHALL retain its existing once-daily
  notification behavior

#### Scenario: Administrative control refuses runtime recursion

- **WHEN** the Chronicler refresh control is invoked with a runtime session or
  runtime trigger context
- **THEN** it SHALL refuse before cache lookup or dispatch
- **AND** direct control invocation SHALL enforce timezone validity, settled
  date, and tuple rate limiting rather than trusting REST pre-validation
- **AND** concurrent calls for one tuple SHALL serialize in the owning daemon
  and re-check durable success before any second dispatch

#### Scenario: Missing witness remains retry-safe

- **WHEN** admissible cache persistence succeeds but its coverage-witness write
  does not
- **THEN** the operation SHALL NOT claim a recovered day
- **AND** cache presence alone SHALL NOT be promoted into coverage proof
- **AND** a later call SHALL remain eligible to re-run the canonical bounded
  evidence read and retry witness persistence
- **AND** a successful or quiet tuple SHALL retain the existing 24-hour rate
  limit

#### Scenario: Administrative response is content-blind

- **WHEN** the daemon returns a refresh outcome to the dashboard
- **THEN** it MAY include only status, cache key/timestamp, quiet, invalid,
  invalid reason, and safe structured error metadata
- **AND** it SHALL NOT include prompt text, prose, tool calls, bundle content,
  or provenance
- **AND** malformed or unknown daemon responses SHALL produce a contained API
  failure rather than a success claim

#### Scenario: Server execution deadline precedes client timeout

- **WHEN** the daemon refresh does not finish within the dashboard's bounded
  MCP execution window
- **THEN** the owning daemon SHALL cancel the operation before cache/witness
  work can continue in the background
- **AND** the dashboard SHALL return `504` with
  `error.code=dispatch_timeout`
- **AND** the daemon-operation deadline SHALL be shorter than the dashboard MCP
  deadline, which SHALL be shorter than the browser request budget
