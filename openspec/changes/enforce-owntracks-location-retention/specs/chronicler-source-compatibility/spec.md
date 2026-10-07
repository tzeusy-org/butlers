## MODIFIED Requirements

### Requirement: Privacy and Retention Declaration

- Compatibility declarations SHALL specify how source privacy and retention apply to Chronicler projections.
- The OwnTracks declaration SHALL cover source_names owntracks.points, owntracks.place_cluster and owntracks.ssid_presence; connector accepted raw ts/recorded_at and immutable effective retention_at; actual accepted request_id/logical-row revision source refs; deterministic point/movement/place/SSID outputs and source-owned idempotence; normal owner-view privacy with existing sensitive/restricted overrides; existing confidence/clustering and asynchronous approved-source adapter path. Raw and copied location evidence SHALL target30days or protected owner-shorter policy conditional on committed coverage; projected legs/visits SHALL survive under existing Chronicler policy at approximately150m unbound spatial precision, with independently owner-supplied static reference exception. Source tombstone/floor, metrics and provenance SHALL survive; reset/replay/widening SHALL not refill raw. This declaration SHALL not mark a missing future source or raw-error projection as delivered.

ID: REQ-chronicler-source-compatibility-003
Source: bu-s11n0s.7 original S1; RFC 0014 D2; openspec/specs/chronicler-source-compatibility/spec.md; proposed location-retention amendment
Scope: v1-mandatory

#### Scenario: Sensitive source includes retention policy

- **WHEN** a source declares `privacy_tier = sensitive`
- **THEN** the declaration SHALL specify raw evidence retention, projected evidence retention, allowed precision after source purge, and tombstone behavior

#### Scenario: Source purge behavior defined

- **WHEN** source records may expire or be deleted
- **THEN** the compatibility declaration SHALL define whether Chronicler deletes, tombstones, or lower-precision-retains derived records

#### Scenario: Privacy and Retention Declaration preserves genuine OwnTracks retention

- **WHEN** an actual OwnTracks source reaches the declared policy/coverage/purge branch for this owning contract
- **THEN** The OwnTracks declaration SHALL cover source_names owntracks.points, owntracks.place_cluster and owntracks.ssid_presence; connector accepted raw ts/recorded_at and immutable effective retention_at; actual accepted request_id/logical-row revision source refs; deterministic point/movement/place/SSID outputs and source-owned idempotence; normal owner-view privacy with existing sensitive/restricted overrides; existing confidence/clustering and asynchronous approved-source adapter path. Raw and copied location evidence SHALL target30days or protected owner-shorter policy conditional on committed coverage; projected legs/visits SHALL survive under existing Chronicler policy at approximately150m unbound spatial precision, with independently owner-supplied static reference exception. Source tombstone/floor, metrics and provenance SHALL survive; reset/replay/widening SHALL not refill raw. This declaration SHALL not mark a missing future source or raw-error projection as delivered.
- **AND** no planning/source presence, caller status, unrelated episode or UI-only age filter SHALL substitute for genuine owning runtime proof
