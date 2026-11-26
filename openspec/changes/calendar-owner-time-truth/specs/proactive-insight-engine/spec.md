## ADDED Requirements

### Requirement: The calendar occurrence probe

The engine SHALL register the premise probe `('calendar', 'occurrence_still_changed')`, backed by
`public.resolve_calendar_occurrence(butler, event_id, original_start)`, a SECURITY DEFINER function
that answers true while the occurrence still differs from what the owner accepted, false when it
matches again or no longer applies, and unknown when it cannot tell. Unknown SHALL deliver the
candidate stamped "as of", as for every probe.

ID: REQ-proactive-insight-engine-040
Source: bu-5ii4gj.10 slice A-S3; design "Changed since you said yes"
Scope: v1-mandatory

#### Scenario: A reverted change reads false

- **WHEN** an occurrence's current terms match the agreed terms
- **THEN** the probe SHALL answer false

#### Scenario: A missing occurrence reads unknown

- **WHEN** the probe finds no occurrence row
- **THEN** it SHALL answer unknown, never false
