## ADDED Requirements

### Requirement: Upcoming Travel Degraded Disclosure

`GET /api/travel/upcoming` and its dashboard KPI strip consumer SHALL NOT
render a healthy-looking empty or zero result when the upstream query fails
or when an individual trip's row cannot be normalized. A source failure or a
per-trip normalization failure SHALL be disclosed by name rather than
suppressed.

#### Scenario: Upstream query failure never renders as an all-clear zero

- **WHEN** the query backing `GET /api/travel/upcoming` fails or is
  unreachable
- **THEN** the KPI strip SHALL render "unavailable" (never a numeral, never
  `0`) for next departure, active trips, planned trips, and open actions
- **AND** the KPI strip SHALL show a degraded-source notice naming the
  endpoint, with a retry action

#### Scenario: One unreadable trip is excluded and disclosed, not silently dropped

- **WHEN** `GET /api/travel/upcoming` otherwise succeeds but one trip's
  nested legs or accommodations cannot be normalized
- **THEN** that trip SHALL be excluded from `upcoming_trips` and its id SHALL
  appear in the response's `unreadable_trip_ids` list
- **AND** the remaining trips SHALL still render normally
- **AND** the KPI strip SHALL disclose the number of excluded trips rather
  than silently undercounting
