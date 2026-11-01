## ADDED Requirements

### Requirement: OwnTracks Location Trail Quality

Chronicler SHALL apply one deterministic trail-plausibility policy to OwnTracks evidence before deriving any location episode. The point projection, the place-cluster projection and the dashboard Map trail SHALL share it. This projection SHALL NOT call an LLM or any external geocoding or location service.

ID: REQ-butler-chronicler-012
Source: bu-2tsgqb.6; Chronicler review 2026-10-22 (Oct 16-18 Beijing trail)
Scope: v1-mandatory

#### Scenario: Impossible jump is kept as evidence but excluded

- **WHEN** a fix lies 1 km or more from the endpoint's last accepted fix and would need a ground speed above 1000 km/h to reach
- **THEN** its `location` point event SHALL still be projected with `payload.trail_outlier` giving the reason and the implied speed
- **AND** the original evidence row SHALL stay unchanged
- **AND** the fix SHALL NOT contribute to any movement, place or away episode, or to the Map trail
- **AND** after three consecutive fixes that are plausible relative to each other, those fixes SHALL be accepted as the new anchor

#### Scenario: Alternating far-city fixes do not fragment the trail

- **WHEN** fixes alternate between two cities about 1,900 km apart within a minute
- **THEN** only the fixes consistent with the accepted trail SHALL be accepted
- **AND** no episode SHALL be derived from the alternating fixes

#### Scenario: One fix is not a movement

- **WHEN** a movement segment holds a single accepted fix, counting fixes carried from earlier batches
- **THEN** no `movement_episode` SHALL be emitted for it
- **AND** the segment SHALL be carried so a later fix within the gap can extend it
- **AND** an emitted episode's `point_count` SHALL count the whole episode, not only the current batch

#### Scenario: Overlapping movement chunks are merged

- **WHEN** a newly started movement segment overlaps a stored movement episode for the same endpoint
- **THEN** the stored episode SHALL be widened to cover both spans
- **AND** no second overlapping movement episode SHALL be emitted

#### Scenario: Place labels name only justified places

- **WHEN** a place cluster is emitted
- **THEN** its label SHALL come from an owner-declared reference point within its radius
- **OR** failing that, from an OwnTracks region the owner defined that is reported on at least half the cluster's fixes
- **OR** otherwise SHALL be `place_unknown`
- **AND** `payload.label_source` SHALL record which of the three applied

#### Scenario: Away-from-home span

- **WHEN** the owner has declared a `home` reference and accepted fixes lie more than 50 km from every `home` reference
- **THEN** one evidence-layer `away_episode` SHALL span the first through the last such fix, with at least two fixes and a duration of at least 3 hours
- **AND** the span SHALL close at the first fix back within 50 km, and a gap of more than 36 hours SHALL split it
- **AND** the episode SHALL record the maximum distance from home and SHALL NOT count as lived activity time
- **AND** without a `home` reference no away span SHALL be emitted
