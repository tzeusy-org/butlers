## MODIFIED Requirements

### Requirement: Manual Historical Day-Close Regeneration

Manual historical regeneration SHALL be requested through a Chronicler-only
service-plane control that is not LLM-presentable and is callable only by the
signed dashboard control client. The control SHALL validate the request and
submit an obligation admission for an occurrence of the git-declared
`chronicler_day_close` obligation keyed to the requested `(date, tz)`; the
Chronicler daemon SHALL NOT spawn a session. The occurrence SHALL reuse the
scheduled day-close bundle and deterministic writer, and SHALL remain
notification-silent because its declared capability set excludes `notify` and
every `communicate`, `actuate`, and `administer` capability. The goal SHALL
return its candidate through a Chronicler capability that applies the
deterministic writer. The dashboard-facing result SHALL contain only safe
cache/admission metadata.

Source: RFC 0038 D2, D4, D5, D7; RFC 0014
Scope: v1-mandatory

#### Scenario: Split-process dashboard reaches the owning daemon

- **WHEN** the dashboard refresh endpoint receives a valid settled `(date, tz)`
- **THEN** it SHALL call the Chronicler-only service-plane control over MCP
- **AND** the daemon SHALL submit an obligation admission for the manual-refresh
  occurrence, await its outcome, run normal cache admission, and verify the
  exact coverage witness
- **AND** the dashboard SHALL NOT depend on an in-process spawner callback, and
  the daemon SHALL NOT spawn a session

#### Scenario: Manual regeneration is owner-silent

- **WHEN** a manual-refresh occurrence of the day-close obligation runs
- **THEN** its grant SHALL contain only the `chronicler_day_close_bundle`
  evidence read and the Chronicler candidate-return capability
- **AND** the broker SHALL neither list nor execute any other capability for
  that goal, so notification, reminder, scheduler, routing, and deferred side
  paths are absent independently of prompt compliance
- **AND** the scheduled once-daily `chronicler_day_close` obligation SHALL
  retain its existing once-daily notification behavior

#### Scenario: Administrative control refuses runtime recursion

- **WHEN** the Chronicler refresh control is called by anything other than the
  signed dashboard control client, including any goal invocation
- **THEN** it SHALL refuse before cache lookup or admission
- **AND** direct control invocation SHALL enforce timezone validity, settled
  date, and tuple rate limiting rather than trusting REST pre-validation
- **AND** concurrent calls for one tuple SHALL serialize in the owning daemon
  and re-check durable success before any second admission

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
- **THEN** the owning daemon SHALL abandon the operation, and the writer SHALL
  refuse a late candidate from that occurrence, so no cache/witness work
  continues in the background
- **AND** the dashboard SHALL return `504` with
  `error.code=dispatch_timeout`
- **AND** the daemon-operation deadline SHALL be shorter than the dashboard MCP
  deadline, which SHALL be shorter than the browser request budget
