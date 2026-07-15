## ADDED Requirements

### Requirement: Travel Medication Snapshot MCP Provider
The Health butler SHALL expose a purpose-specific `medication_travel_snapshot` MCP tool for travel
preparation consumers. Health SHALL remain the authoritative owner of the underlying medication data.

#### Scenario: Active medications use the versioned minimum contract
- **WHEN** `medication_travel_snapshot` is called and active medications exist
- **THEN** it SHALL return a strict `health.medication-travel.v1` response with `status = "ok"`
- **AND** each medication SHALL contain exactly `name`, `dosage`, `frequency`, and `schedule`
- **AND** the provider SHALL read the canonical Health fact surface with `predicate = 'medication'`,
  `scope = 'health'`, and active validity
- **AND** it SHALL exclude medications whose metadata has `active = false`

#### Scenario: Private and unrelated Health fields are excluded
- **WHEN** Health projects a medication into the travel snapshot
- **THEN** it MUST NOT include notes, dose history, adherence, timestamps, raw fact content, entity
  identifiers, conditions, symptoms, measurements, or any other Health data

#### Scenario: No active medications is a successful empty response
- **WHEN** `medication_travel_snapshot` finds no active medications
- **THEN** it SHALL return `status = "ok"` with `medications = []` and no error

#### Scenario: Existing Health storage is reused
- **WHEN** this provider is deployed
- **THEN** it SHALL use the existing Health medication facts and SHALL NOT require a new table,
  column, cross-schema grant, or shared medication store
