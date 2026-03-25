# Travel Butler — Insight Scan

## Purpose
Adds an insight-scan scheduled task to the Travel butler that generates proactive insight candidates from travel domain data.

## MODIFIED Requirements

### Requirement: Travel Butler Schedules
The travel butler runs upcoming travel checks, document expiry scans, and insight scans.

#### Scenario: Scheduled task inventory
- **WHEN** the travel butler daemon is running
- **THEN** it executes three scheduled tasks: `upcoming-travel-check` (0 8 * * *), `trip-document-expiry` (0 9 * * 1), and `insight-scan` (0 7 45 * * *, job: evaluate travel domain data and generate insight candidates)

## ADDED Requirements

### Requirement: Travel Insight Scan Job
The travel butler's `insight-scan` job SHALL evaluate travel domain data and produce insight candidates covering pre-trip preparation, document expiry warnings, and cross-domain coordination hints. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool — the butler does not write to `shared.insight_candidates` directly.

#### Scenario: Insight-scan job handler registration
- **WHEN** the travel butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Pre-trip preparation insights
- **WHEN** the insight-scan job evaluates upcoming trips with status `planned`
- **THEN** it SHALL generate candidates for trips departing within 7 days
- **AND** trips departing within 1 day SHALL have priority 92 (time-critical)
- **AND** trips departing within 3 days SHALL have priority 78
- **AND** trips departing within 7 days SHALL have priority 65
- **AND** the `dedup_key` SHALL be `travel:pre-trip:{trip-id}:{departure-date}`
- **AND** `expires_at` SHALL be the departure date
- **AND** the message SHALL reference the destination and suggest reviewing the pre-trip checklist

#### Scenario: Document expiry insights
- **WHEN** the insight-scan job evaluates travel documents (passports, visas, travel insurance)
- **THEN** it SHALL generate candidates for documents expiring within 90 days
- **AND** documents expiring within 30 days SHALL have priority 85
- **AND** documents expiring within 60 days SHALL have priority 65
- **AND** documents expiring within 90 days SHALL have priority 45
- **AND** the `dedup_key` SHALL be `travel:document-expiry:{document-type}:{expiry-date}`
- **AND** `expires_at` SHALL be 14 days from generation (re-check periodically)
- **AND** `cooldown_days` SHALL be 14 for 90-day warnings, 7 for 60-day, 3 for 30-day

#### Scenario: Medication prep for travel insights
- **WHEN** the insight-scan job evaluates upcoming trips
- **AND** the user has active medications tracked by the health butler (queryable via `shared` schema or known from memory facts)
- **THEN** it SHALL generate candidates reminding the user to ensure adequate medication supply for the trip duration
- **AND** priority SHALL be 75 for trips within 7 days, 55 for trips within 14 days
- **AND** the `dedup_key` SHALL be `travel:medication-prep:{trip-id}`
- **AND** `expires_at` SHALL be the departure date
- **AND** this insight SHALL only be generated if the trip duration exceeds 3 days

#### Scenario: No insights for past or completed trips
- **WHEN** the insight-scan job evaluates trips
- **THEN** it SHALL exclude trips with status `completed` or `cancelled`
- **AND** it SHALL exclude trips whose departure date is in the past
