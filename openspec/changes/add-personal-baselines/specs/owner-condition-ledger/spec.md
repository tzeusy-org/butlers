## ADDED Requirements

### Requirement: Baseline deviation condition source

Producers reconciling personal-baseline deviation episodes SHALL use the source `{origin_butler}:baseline-deviation`, one observation per open episode with a fingerprint computed from `{metric, opened_on}`, and SHALL reconcile with `snapshot_complete=True` over the episodes in their own schema so that a closed episode resolves its condition.

#### Scenario: A closed episode resolves its condition

- **WHEN** an episode closes and the producer reconciles its open episodes
- **THEN** the condition for that episode SHALL resolve and delivered insights premised on it SHALL be amended in place
