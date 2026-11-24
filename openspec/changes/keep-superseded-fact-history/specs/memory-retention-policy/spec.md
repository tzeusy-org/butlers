## ADDED Requirements

### Requirement: Superseded facts are retained as closed validity intervals
A superseded fact SHALL be retained as a closed validity interval. The nightly purge SHALL delete a superseded fact only when `public.memory_retention_policies` holds a finite `kind='fact'` `ttl_days` that was read successfully, and only when the fact's `retention_class` has a `memory_policies` row with `archive_before_delete = false` and a non-NULL `ttl_days`. A NULL horizon, a missing row, a missing table or an unreadable policy SHALL delete nothing, and SHALL NOT fall back to a default horizon. `job_args.older_than_days` MAY lengthen a finite horizon and SHALL NOT shorten it or enable deletion. The horizon SHALL age a fact from `COALESCE(dropped_at, invalid_at, created_at)`. The orphaned `ha_state` fact purge SHALL run independently of the superseded purge. A storage reader SHALL return the facts whose validity interval contains a given timestamp, excluding retracted facts, while default retrieval SHALL continue to exclude superseded facts.

ID: REQ-memory-retention-policy-013
Source: bu-uhbhzq.3; owner decision run24-keep-history-default = A (2026-11-22)
Scope: v1-mandatory

#### Scenario: The horizon is NULL
- **WHEN** the nightly purge runs with `kind='fact'` `ttl_days` NULL
- **THEN** no superseded fact is deleted and each successor keeps its `supersedes_id`
- **AND** orphaned `ha_state` facts are still deleted

#### Scenario: The policy cannot be read
- **WHEN** the policy table is missing, the `kind='fact'` row is absent or the read raises
- **THEN** no superseded fact is deleted and a warning is logged

#### Scenario: A finite horizon protects durable classes
- **WHEN** the horizon is finite
- **THEN** only superseded facts of a class with `archive_before_delete = false` and a non-NULL class `ttl_days`, aged past the horizon, are deleted
- **AND** `personal_profile`, `health_log`, `financial_log` and unknown classes are kept

#### Scenario: Reading as of a time
- **WHEN** fact A was superseded by fact B at time T
- **THEN** a read before T returns A and a read after T returns B
