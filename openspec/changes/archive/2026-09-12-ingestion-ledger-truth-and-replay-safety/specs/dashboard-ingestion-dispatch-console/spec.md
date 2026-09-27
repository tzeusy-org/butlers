## ADDED Requirements

### Requirement: Passive Timeline Refresh Is Non-Disruptive
The Timeline ledger SHALL distinguish stale placeholder rows during a changed
filter from a same-query live background refresh.

#### Scenario: Stream-triggered refresh retains ledger focus
- **WHEN** an active Timeline query is invalidated by a live ingestion event or
  periodic refresh while it already has current-key data
- **THEN** the ledger remains fully usable and at normal opacity
- **AND** the existing freshness/status surface continues to communicate live
  state without stealing focus or blocking input

#### Scenario: Filter transition marks placeholder rows
- **WHEN** a changed Timeline filter retains prior rows as placeholder data
- **THEN** the ledger MAY visibly distinguish those stale rows until the new
  query resolves
- **AND** pagination loading remains independently visible without dimming the
  existing ledger

### Requirement: Replay Controls Respect Server Policy
Timeline replay controls SHALL require both a replayable status and
server-derived replay-safety evidence.

#### Scenario: Unsafe event remains visible but cannot be selected
- **WHEN** a filtered, error, or failed row has a non-actionable replay policy
- **THEN** its explanatory state remains visible
- **AND** it is excluded from select-all and bulk replay
- **AND** row and drawer replay actions are disabled or absent with a concise
  reason

#### Scenario: Stale replay policy rejection recovers selection
- **WHEN** a bulk replay request receives a replay-safety HTTP 409 after the
  selection was made
- **THEN** the Timeline offers the existing one-click action to deselect
  exactly the ineligible events
