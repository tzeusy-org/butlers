## ADDED Requirements

### Requirement: Last Workout Lookup

The Health butler SHALL provide a read-only `workout_last` tool that answers "when did I last
<activity>?" from active `workout_session` facts in the Health schema, without guessing.

ID: REQ-butler-health-002
Source: bu-q7vx1q.36; PR #4348 `workout_session` ingestion
Scope: v1-mandatory

#### Scenario: Latest matching workout is returned in the owner's time zone

- **WHEN** `workout_last` is called with an activity and several active `workout_session` facts match it
- **THEN** it SHALL return the fact with the latest `valid_at`
- **AND** the result SHALL include `started_at` in the owner's configured general time zone (UTC when unavailable), `started_at_utc`, `duration_minutes`, `activity`, `source`, and the fact `id`
- **AND** a start near a day boundary or a DST transition SHALL render the owner's local date and offset

#### Scenario: Activity vocabulary is explicit

- **WHEN** the activity is matched
- **THEN** wording SHALL be normalised to lowercase tokens and mapped through the alias table: `run`, `jog`, `jogging` to running; `swim` to swimming; `ride`, `bike`, `cycle`, `cycling` to biking; `walk` to walking; `hike` to hiking; `lift`, `lifting`, `weights` to weightlifting
- **AND** a stored activity matches when it contains every normalised query token
- **AND** an omitted or blank activity SHALL match any workout
- **AND** a token with no alias SHALL match only that token as written, as a whole token, so unlisted wording matches a stored activity only when it contains that exact token
- **AND** matching SHALL page through active facts newest-first until a match is found, so a match older than many newer workouts of other activities is still returned

#### Scenario: Source attribution and overlap

- **WHEN** a result is returned
- **THEN** `source` SHALL be `owner_logged` when the fact's `detection` is `manual` and `device` otherwise
- **AND** when the latest matching device record overlaps an owner-logged matching record, the owner-logged record SHALL be returned

#### Scenario: No matching workout is explicit absence

- **WHEN** no active `workout_session` fact matches
- **THEN** the tool SHALL return `found: false` with `reason: "no_workout_recorded"`
- **AND** it SHALL NOT raise an error or return a guessed workout

#### Scenario: Only active facts are read

- **WHEN** a `workout_session` fact is retracted or superseded
- **THEN** it SHALL NOT be considered
