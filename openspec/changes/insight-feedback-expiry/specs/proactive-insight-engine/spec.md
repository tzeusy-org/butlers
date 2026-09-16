## ADDED Requirements

### Requirement: Bounded Explicit Insight Feedback
The broker SHALL expose useful, not-now, and never owner-feedback verbs without
adding another global verbosity control. Feedback attribution SHALL be derived by
the server and the persisted evidence SHALL remain content-blind.

#### Scenario: Useful reverses a family hold
- **WHEN** the owner marks an insight useful after snoozing or muting its family
- **THEN** the family cooldown SHALL be removed and its category weight SHALL be eligible to return to baseline

#### Scenario: Not-now is bounded
- **WHEN** the owner marks an insight not-now
- **THEN** a future `snooze_until` SHALL be required and the family SHALL resume after that instant

#### Scenario: Never remains reversible
- **WHEN** the owner marks an insight never
- **THEN** the family SHALL receive an indefinite cooldown
- **AND** a later useful verdict SHALL reverse it

#### Scenario: Bounded doors share one behavior
- **WHEN** feedback is invoked through Switchboard MCP, REST, delivered-message action metadata, or the dashboard insight row
- **THEN** every door SHALL call the same useful, not-now, or never behavior
- **AND** no door SHALL accept a caller-asserted actor

### Requirement: Per-Category Reversible Attention Shaping
The broker SHALL shape candidate ordering with per-category engagement weights
inside the existing global delivery cap. It SHALL publish a content-blind reason
for every reduced category weight.

#### Scenario: Uniform engagement preserves the prior budget
- **WHEN** every eligible category has the same engagement history
- **THEN** category weights SHALL be equal
- **AND** the effective global budget and candidate count SHALL equal the previous global-budget behavior

#### Scenario: One category does not quiet another
- **WHEN** nine of the last ten Health insights were ignored and another category remained engaged
- **THEN** Health SHALL receive a lower weight without reducing the other category's weight
- **AND** its reason SHALL read `hearing less from Health: 9 of last 10 ignored`

### Requirement: Expired-Unseen Attention Truth
Every pending candidate that expires unseen SHALL produce exactly one attention
ledger row with `outcome=expired` and a closed `blocked_by` reason from `budget`,
`cooldown`, `held_by`, or `dedup`.

#### Scenario: Expiry is visible per origin
- **WHEN** a pending candidate expires before delivery
- **THEN** one content-blind ledger row SHALL reference that candidate
- **AND** `GET /api/attention/ledger/summary` SHALL count it as `expired_unseen` for the originating butler

#### Scenario: Equal priority prefers the perishable candidate
- **WHEN** two eligible candidates have equal weighted priority and only one expires before the next regular cycle
- **THEN** the perishable candidate SHALL rank first
