## ADDED Requirements

### Requirement: Aggregate correspondence enrichment

Relationship SHALL run a deterministic, zero-LLM `email_correspondence_enrichment`
job (`dispatch_mode="job"`, daily) that reads `messenger.v_confirmed_email_outbound`
for at most 100 active literal `has-email` addresses per run. It SHALL report
`bidirectional=true` only when the view shows confirmed outbound mail and the
existing inbound recurrence signal matches the same address within 180 days;
otherwise it SHALL report `null`, never `false`. Relationship SHALL NOT read the
ledger table. `run_email_identity_enrichment` SHALL remain inbound-only.

#### Scenario: Both legs produce a positive result

- **WHEN** an address has confirmed outbound mail and inbound recurrence within 180 days
- **THEN** the job reports `bidirectional=true` for that entity

#### Scenario: One leg alone is unknown

- **WHEN** only inbound recurrence or only confirmed outbound exists
- **THEN** the job reports `null`

#### Scenario: Missing view is unknown

- **WHEN** the view is absent or its grant is revoked
- **THEN** the job reports `null` and does not fail the scheduler
