## ADDED Requirements

### Requirement: Confidential Classification of Clinical Fact Writes

The Health butler SHALL classify condition, symptom, medication, and dose fact
writes as `confidential` explicitly. A guarded, idempotent core migration SHALL
reclassify historical Health facts with those predicates and remove their
already-published catalog entries. Measurement facts remain outside this
clinical classification rule.

#### Scenario: Clinical Health tools write confidential facts

- **WHEN** the Health butler records or updates a condition, symptom,
  medication, or medication dose
- **THEN** the resulting Health fact MUST have `sensitivity='confidential'`
- **AND** the tool MUST pass that classification explicitly rather than rely
  on a memory-store default

#### Scenario: Historical under-classified clinical facts are repaired

- **WHEN** the core migration runs against a schema containing an affected
  Health fact with a lower sensitivity
- **THEN** it MUST reclassify that fact as `confidential`
- **AND** it MUST remove catalog entries sourced from that affected fact
- **AND** a repeated migration execution MUST make no additional change

#### Scenario: Non-clinical measurement facts remain discoverable

- **WHEN** the migration encounters a Health measurement fact outside the
  condition, symptom, medication, and dose predicate set
- **THEN** it MUST leave that fact and any catalog entry unchanged
