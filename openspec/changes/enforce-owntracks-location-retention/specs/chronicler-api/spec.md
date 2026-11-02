## ADDED Requirements

### Requirement: Owner-Declared Location Retention Horizon

The Chronicler API SHALL expose the raw OwnTracks location retention horizon as a single canonical, versioned policy record. The horizon SHALL default to 30 days and SHALL accept only integers from 1 to 30. `GET /api/chronicler/location-retention` SHALL return `days`, `version`, `updated_at`, `min_days`, `max_days`, `raw_age_basis`, `precision_after_forgetting`, `covered_sources` and `status`. `PUT /api/chronicler/location-retention` SHALL require dashboard owner control. It SHALL accept only a strict body of `days` and `expected_version`, SHALL derive the actor from the authenticated principal, and SHALL apply the change by compare-and-set on `version`. A missing policy record SHALL fail closed and SHALL NOT be reported as the default. Until raw expiry is gated on genuine projection coverage, `status` SHALL be `not_enforced` and the API SHALL NOT claim raw location evidence was forgotten.

ID: REQ-chronicler-api-018
Source: bu-s11n0s.7 (run16 #7); openspec/changes/enforce-owntracks-location-retention/proposal.md
Scope: v1-mandatory

#### Scenario: Default horizon is declared but not yet enforced

- **WHEN** the owner reads the policy on a freshly migrated database
- **THEN** the response SHALL report `days = 30`, `version = 1`, bounds 1..30 and `status = not_enforced`

#### Scenario: Owner shortens the horizon by compare-and-set

- **WHEN** the authenticated owner sends `days = 7` with the current `expected_version`
- **THEN** the stored horizon SHALL become 7 days, `version` SHALL increase by one, and `updated_by` SHALL be the authenticated principal
- **AND** a later request carrying the old version SHALL return 409 with the current version and SHALL write nothing

#### Scenario: Malformed or unauthenticated changes write nothing

- **WHEN** a request body carries a boolean, string, float, null, 0 or 31 for `days`, omits `expected_version`, or adds an unknown field such as `actor`
- **THEN** the API SHALL refuse it with 422 and the stored policy SHALL be unchanged
- **AND** a request without the owner credential SHALL be refused with 401 before any write

#### Scenario: Missing policy fails closed

- **WHEN** the policy record is absent
- **THEN** both GET and PUT SHALL return 503 and SHALL NOT create or report a default horizon
