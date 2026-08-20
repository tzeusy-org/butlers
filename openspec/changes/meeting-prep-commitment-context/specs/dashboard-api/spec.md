## MODIFIED Requirements

### Requirement: Meeting-Prep Rail Endpoint

The dashboard API `GET /api/calendar/workspace/prep/{event_id}` SHALL include
a `commitments` array per attendee in the response model. Each commitment entry
SHALL carry `kind` (promise, waiting_for, follow_up, obligation, decision),
`direction` (owner_to_other, other_to_owner, self), `summary`, `deadline`
(nullable ISO-8601), `escalation_level` (0–3), and `fingerprint`. The endpoint
continues to read exclusively from the precomputed
`calendar.v_prep_contributions` cached view and MUST NOT query
`public.owner_conditions` at request time.

ID: REQ-dashboard-api-054
Source: RFC 0026 §Out of Scope ("Moment Prep integration")
Scope: v1-mandatory

#### Scenario: Prep rail response includes commitments per attendee

- **WHEN** `GET /api/calendar/workspace/prep/{event_id}` is called for an event
  whose precomputed prep contribution includes attendee commitments
- **THEN** each attendee in the response carries a `commitments` array with
  `kind`, `direction`, `summary`, `deadline`, `escalation_level`, and
  `fingerprint` per entry
- **AND** no on-demand query against `public.owner_conditions` occurs

#### Scenario: Prep rail response with no commitments

- **WHEN** the precomputed prep contribution has an empty `commitments` list for
  an attendee (or the field is absent in a legacy envelope)
- **THEN** the API response carries `commitments: []` for that attendee
- **BECAUSE** the response model normalizes absent to empty for backward
  compatibility with pre-commitment prep envelopes

#### Scenario: Commitment fields render correctly in the frontend prep rail

- **WHEN** the prep rail component renders an attendee with active commitments
- **THEN** each commitment is displayed as a chip showing the kind icon,
  direction indicator, summary text, and deadline (when present)
- **AND** commitments at escalation level >= 2 are visually emphasized
