## MODIFIED Requirements

### Requirement: Layer Stamped On Every Projection Write

Every projection adapter SHALL stamp `layer` on insert for all newly-projected
rows — calendar → `intent`, lived-activity sources → `activity`, raw point
signals → `evidence` — not only on a one-time backfill. The `layer` column SHALL
have a conservative non-null default that never causes uncounted activity or
counted intent.

#### Scenario: Freshly-projected calendar block is intent

- **WHEN** the calendar adapter projects a new event
- **THEN** the stored episode has `layer = intent`
- **AND** it is excluded from lived-time totals

#### Scenario: Freshly-projected activity is counted

- **WHEN** an activity-source adapter projects a new episode
- **THEN** the stored episode has `layer = activity`
- **AND** it is included in lived-time totals
