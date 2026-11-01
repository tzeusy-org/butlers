## ADDED Requirements

### Requirement: Location-Aware Travel Intent Reconciliation

The deterministic day-close reconciler SHALL weigh one-way travel intents against accepted OwnTracks location fixes and SHALL stamp producer provenance on every calendar intent. A travel intent is a calendar intent between 30 minutes and 24 hours long whose title names a flight or a route written with an arrow. The check SHALL NOT call an LLM or any geocoding or external location service.

ID: REQ-butler-chronicler-013
Source: bu-2tsgqb.7; Chronicler review 2026-10-22 (Oct 16 SQ 800, Oct 21 SIN -> Aomori)
Scope: v1-mandatory

#### Scenario: Displacement across a flight is consistent, not attendance

- **WHEN** two or more accepted fixes at one place fall within 6 hours before (or 1 hour after) a travel intent's start
- **AND** two or more accepted fixes at another place fall within 1 hour before (or 6 hours after) its end
- **AND** the two places are at least 300 km apart and the implied speed is at most 1000 km/h
- **THEN** the intent SHALL be kept with a `consistent` location verdict and the displacement
- **AND** the narration SHALL NOT state that the owner took the planned trip

#### Scenario: Same place before and after contradicts the trip

- **WHEN** both anchors exist and lie within 50 km of each other
- **THEN** the intent SHALL be dropped as contradicted with `evidence` set to `location`

#### Scenario: Missing or unreliable location stays unknown

- **WHEN** either anchor has fewer than two fixes, or the anchors are between 50 and 300 km apart, or the implied speed is implausible
- **THEN** the location verdict SHALL be `unknown` and SHALL be narrated as neither corroborated nor contradicted
- **AND** fixes flagged `trail_outlier`, fixes with accuracy worse than 1000 m, fixes without a timezone and non-normal privacy fixes SHALL NOT form an anchor

#### Scenario: Travel across local midnight reads neighbouring days

- **WHEN** a travel intent's window plus 6 hours on either side extends beyond the closed local day
- **THEN** the day-close bundle SHALL read location fixes over that whole span, compared as absolute instants

#### Scenario: Butler-written plans keep producer provenance

- **WHEN** a calendar intent comes from an event the calendar module marked `butler_generated`, or its title starts with `BUTLER:`
- **THEN** the reconciled intent SHALL carry `plan_producer = butler`, otherwise `calendar`
- **AND** the narration SHALL describe a butler plan as a plan, never as the owner's own schedule or as attended
