## ADDED Requirements

### Requirement: Deviation Claims Carry Baseline Evidence

A candidate in a deviation-claiming category (`baseline-deviation`) SHALL carry `metadata.baseline_evidence` with numeric `n_observed`, `n_expected`, `coverage`, `center` and `dispersion` and a non-empty `method_version`, where `n_expected` is at least 1 and `n_observed` does not exceed it. `propose_insight_candidate` SHALL reject such a candidate with `status="error"` before any write when the evidence is absent or malformed.

#### Scenario: A deviation claim without evidence is rejected

- **WHEN** a `baseline-deviation` candidate is proposed with no `baseline_evidence`, or with only some of its fields
- **THEN** the call returns `status="error"` and no row is inserted

#### Scenario: Other categories are unaffected

- **WHEN** a candidate in any other category is proposed without `baseline_evidence`
- **THEN** it is validated exactly as before
