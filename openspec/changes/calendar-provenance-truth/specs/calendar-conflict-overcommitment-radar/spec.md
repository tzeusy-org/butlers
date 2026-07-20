## ADDED Requirements

### Requirement: Provenance-Aware Conflict Candidate Filter

The conflict radar SHALL continue to render all projected workspace rows, but
before overlap, back-to-back, or overloaded-day detection it SHALL exclude an
event with explicit `metadata.butler_generated=true`. It SHALL also exclude an
all-day event and a legacy locally-midnight-aligned event spanning at least 24
hours in its valid stored IANA timezone. The filter SHALL run before all three
detectors so an excluded row cannot pair, extend a density chain, or contribute
meeting hours.

The filter SHALL use only the explicit metadata marker for generated-event
exclusion. A comparable timed human event without that marker SHALL preserve
the existing radar behavior. Malformed metadata SHALL be treated as no explicit
marker; an invalid or missing timezone SHALL make only the legacy-midnight
inference unavailable. These malformed inputs SHALL not raise and SHALL not
hide a timed event solely through a failed parse.

#### Scenario: Generated row remains visible but produces no radar issue

- **WHEN** a butler-generated projection row overlaps or adjoins a human event
- **THEN** the workspace projection still returns the generated row
- **AND** the radar reports no overlap or back-to-back issue from that pair
- **AND** the generated row contributes no hours to an overloaded-day issue

#### Scenario: Equivalent human rows retain detector behavior

- **WHEN** two timed human projection rows have the same timing as excluded
  generated rows but lack `metadata.butler_generated=true`
- **THEN** overlap, back-to-back, and overloaded-day detection retain their
  existing behavior

#### Scenario: Legacy midnight row is excluded from every detector

- **WHEN** a legacy row has `all_day=false`, lasts at least 24 hours, and has
  local-midnight boundaries in its valid IANA timezone
- **THEN** it does not participate in overlap, back-to-back, or overloaded-day
  detection

#### Scenario: Malformed provenance does not hide a timed event

- **WHEN** an otherwise valid timed row has malformed metadata or an invalid
  timezone
- **THEN** the radar does not raise
- **AND** malformed metadata alone does not exclude it as generated
- **AND** an invalid timezone alone does not exclude it as a legacy all-day row
