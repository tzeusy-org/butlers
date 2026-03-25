# Session Management (Delta)

## MODIFIED Requirements

### Requirement: Trigger Source Tracking
Valid trigger sources are: `tick`, `external`, `trigger`, `route`, `healing`, `dashboard`, and `schedule:<task-name>` (where task-name is any non-empty string). The trigger source is validated at session creation.

#### Scenario: Schedule trigger source
- **WHEN** `trigger_source="schedule:daily_digest"` is provided
- **THEN** validation passes (matches the `schedule:<name>` pattern)

#### Scenario: Route trigger source
- **WHEN** `trigger_source="route"` is provided
- **THEN** validation passes (exact match in the `TRIGGER_SOURCES` frozenset)

#### Scenario: Healing trigger source
- **WHEN** `trigger_source="healing"` is provided
- **THEN** validation passes (exact match in the `TRIGGER_SOURCES` frozenset)

#### Scenario: Dashboard trigger source
- **WHEN** `trigger_source="dashboard"` is provided
- **THEN** validation passes (exact match in the `TRIGGER_SOURCES` frozenset)
- **AND** the session is attributed to a dashboard conversational interaction
