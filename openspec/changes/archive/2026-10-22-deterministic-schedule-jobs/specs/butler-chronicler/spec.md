## ADDED Requirements

### Requirement: Day-Close Gap Interview Ask Job

The Chronicler SHALL run the once-daily day-close gap interview ask as a deterministic job-mode schedule. The job and the `chronicler_gap_interview` MCP tool SHALL share one ask service, which evaluates the closed day, enforces the max-one-per-day dedupe, applies the low-priority delivery-preferences gate and delivers the one-tap prompt. The answer contracts stay those of chronicler-api "Chronicler Corrections" and connector-telegram-bot gap-interview callback routing, unchanged.

ID: REQ-butler-chronicler-008
Source: RFC 0038 D2; bu-whhll.12; bu-idc5mx.4
Scope: v1-mandatory

#### Scenario: Ask job runs for yesterday in the owner's timezone
- **WHEN** the `chronicler_gap_interview` schedule fires (0 9 * * *, job, owner-local)
- **THEN** the job SHALL compute yesterday as the local calendar day before the dispatch instant in the owner's general timezone, correct across DST changes
- **AND** it SHALL call the shared ask service with that date and timezone and return its status unchanged: `asked`, `already_asked`, `no_gap`, `deferred_quiet_hours`, `delivery_failed` or `not_configured`
- **AND** it SHALL reject unsupported job arguments

#### Scenario: Ask job spawns no session and sends nothing else
- **WHEN** the `chronicler_gap_interview` job runs
- **THEN** it SHALL spawn no session and call no model
- **AND** it SHALL send no message beyond the single prompt the ask service delivers

#### Scenario: Tool and job share the per-day dedupe
- **WHEN** the owner calls the `chronicler_gap_interview` tool for a day the job already asked
- **THEN** the shared ask service SHALL return `already_asked` without delivering again
- **AND** the tool SHALL keep its signature and return statuses
