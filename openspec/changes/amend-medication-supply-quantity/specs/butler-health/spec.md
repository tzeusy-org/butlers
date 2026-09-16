## ADDED Requirements

### Requirement: Medication supply quantity API remains owner-recorded truth

The Health dashboard medication API SHALL accept an optional `quantity` on
medication create and update requests only when it is a strictly positive whole
number. The value SHALL remain the owner-recorded count in the current supply;
the API and Health fact tools SHALL NOT derive, round, default, or infer a
quantity from dosage, frequency, dose logs, or a conventional pack size.

#### Scenario: Creating a medication with a known supply round-trips the count

- **WHEN** the owner calls `POST /api/health/medications` with a valid positive
  integer `quantity`
- **THEN** the route SHALL pass that exact quantity to the existing
  `medication_add` fact path and return it in the created `Medication` response
- **AND** the persisted medication fact SHALL carry the quantity and a
  `quantity_updated_at` timestamp from the existing server-side write path

#### Scenario: Creating a medication without a supply keeps it unknown

- **WHEN** the owner omits `quantity` from `POST /api/health/medications`
- **THEN** the route SHALL return `quantity: null` and
  `quantity_updated_at: null` in the created response
- **AND** it SHALL NOT insert a default, zero, or inferred quantity

#### Scenario: Updating quantity records the current fill or refill

- **WHEN** the owner calls `PUT /api/health/medications/{id}` with a valid
  positive integer `quantity`
- **THEN** the route SHALL pass only the supplied quantity through the existing
  `medication_update` path
- **AND** the resulting medication SHALL expose the exact quantity and a new
  `quantity_updated_at` timestamp anchored to that server write

#### Scenario: Omitting quantity during an edit preserves existing truth

- **WHEN** the owner updates other medication fields without supplying
  `quantity`
- **THEN** the existing quantity and `quantity_updated_at` SHALL remain
  unchanged, including when the prior quantity is unknown

#### Scenario: Invalid supply values are refused before any write

- **WHEN** a create or update request supplies zero, a negative number, a
  decimal, a boolean, a numeric string, or other malformed quantity
- **THEN** the API SHALL return HTTP 422 with typed validation detail
- **AND** it SHALL NOT invoke `medication_add` or `medication_update`

#### Scenario: Health reads preserve absence as unknown

- **WHEN** a medication fact has no owner-recorded quantity
- **THEN** `GET /api/health/medications` SHALL return `quantity: null` and
  `quantity_updated_at: null`
- **AND** no Health API response in this contract SHALL substitute zero or a
  forecast for the absent value

## Source References

- Non-Negotiable Rule 1 (the owner has sovereignty over personal health data)
- Non-Negotiable Rule 4 (deterministic daemon behavior and explicit state)
- Non-Negotiable Rule 6 (Health manifesto governs medication scope)
- RFC 0007 (Dashboard and API Surface)
