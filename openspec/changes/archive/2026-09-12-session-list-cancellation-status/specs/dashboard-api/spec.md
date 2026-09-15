## ADDED Requirements

### Requirement: Session List Owner-Cancellation Discriminator

The session-list API SHALL include `cancelled_by_owner: boolean` on every
`SessionSummary` returned by `GET /api/sessions` and `GET
/api/butlers/{name}/sessions`.
The field SHALL be true only when the row is terminally unsuccessful and its
stored outcome is the exact canonical owner-cancellation marker written by
`Spawner.cancel_session()`. List responses SHALL NOT expose the raw `error`
string to derive this presentation state.

#### Scenario: Canonical owner cancellation is projected without error text

- **WHEN** a session row has `success = false` and the canonical
  owner-cancellation outcome
- **THEN** both session-list routes return `cancelled_by_owner: true` for that
  summary
- **AND** neither response item contains an `error` field

#### Scenario: Generic failure remains distinct

- **WHEN** a terminal unsuccessful session has any error outcome other than
  the canonical owner-cancellation marker
- **THEN** both session-list routes return `cancelled_by_owner: false`

#### Scenario: Non-terminal session is never labelled cancelled

- **WHEN** a session is non-terminal (`success = null`)
- **THEN** both session-list routes return `cancelled_by_owner: false`
- **AND** their existing pagination envelopes and semantics remain unchanged
