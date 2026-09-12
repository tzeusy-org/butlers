## ADDED Requirements

### Requirement: Canonical CLI Authority Projection

The Secrets inventory SHALL use a canonical shared CLI row as the health authority for a CLI credential key whenever that row exists. Same-key per-butler system rows are compatibility mirrors and SHALL NOT override the canonical state or inflate CLI-family failing/unverified counts. When no canonical CLI row exists, per-butler mirrors MAY supply the legacy display fallback and SHALL retain most-severe aggregation.

#### Scenario: Canonical CLI health overrides stale mirrors

- **WHEN** `cli[]` contains a credential key and `system[]` contains same-key `cli-auth` mirrors
- **THEN** CLI-family state and KPI counts use the canonical `cli[]` row only
- **AND** the raw per-source System evidence remains available without rewriting credential data

#### Scenario: Legacy mirror remains visible without canonical state

- **WHEN** no canonical `cli[]` row exists for a `cli-auth` key
- **THEN** same-key per-butler mirrors remain eligible for the CLI display family
- **AND** their most severe state determines the fallback display state
