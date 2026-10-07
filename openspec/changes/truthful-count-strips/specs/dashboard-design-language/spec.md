## ADDED Requirements

### Requirement: Count-bucket truth

A count strip SHALL place each observation by an explicit UTC bucket_start within its source-declared window, show relative range labels and owner-timezone accessible ranges, and distinguish measured count zero, unavailable counts, DEAF recording gaps and UNKNOWN listening by shape and text. Missing keys or degraded sources SHALL never synthesize count zeros, LIVE, clock-of-day placement or complete history. Existing Time-True Trend Grammar SHALL remain unchanged.

ID: REQ-dashboard-design-language-007
Source: bu-s11n0s.5 complete-protocol D1-D6; heart-and-soul/vision.md failure and staleness honesty; owner-timezone-context and dashboard-design-language temporal contracts
Scope: v1-mandatory

#### Scenario: Sparse keyed positives preserve positions

- **WHEN** counts exist at hours 2 and 20 of the declared 24-hour grid
- **THEN** their marks SHALL occupy keys 2 and 20 regardless of array order or sparsity
- **AND** the exact source bounds SHALL remain explicit: the last rolling slot ends at captured as_of and is closed; any separate open calendar/source-incomplete diagnostic SHALL be marked partial/UNKNOWN

#### Scenario: Eligible deaf interval retains original accessible words

- **WHEN** three consecutive closed hours have complete receiver recording and no accepted exact-endpoint heartbeat
- **THEN** the strip SHALL hatch those cells and its aria description SHALL include `not listening 3h`
- **AND** the legend SHALL qualify this as complete receiver-recorded absence, not a provider-outage proof

#### Scenario: Measured zero and uncertainty are independent

- **WHEN** a successful count query measures zero while listening evidence is unavailable
- **THEN** the count SHALL remain a measured zero and the listening overlay SHALL say `liveness unknown`
- **AND** count-source failure SHALL instead draw unavailable cells, never measured zeros

#### Scenario: Owner-zone ranges survive clock boundaries

- **WHEN** UTC keys cross midnight, a DST fold/gap or a noninteger owner-zone offset with a different host timezone
- **THEN** placement SHALL remain UTC-keyed and accessible ranges SHALL use the owner timezone
- **AND** keyboard/click actions SHALL use those actual source bounds

#### Scenario: Chart derivation lint has a positioned exception

- **WHEN** a zero-filled positional array or its local alias feeds a count-strip derivation outside the canonical bucket library
- **THEN** configured ESLint SHALL reject it
- **AND** unrelated non-chart arrays in the same file SHALL remain allowed
- **AND** the canonical source-availability-checked count densifier and existing trend-grammar guards SHALL remain allowed
