## ADDED Requirements

### Requirement: Journal Event Emission — Triage
The triage layer SHALL emit a `flagged` journal event into `public.qa_investigation_events` whenever it persists a novel finding that becomes the head of a new investigation. Triage MAY emit `sampled` and `cross-checked` events when it performs multi-source corroboration; v1 implementations are permitted to omit these.

#### Scenario: flagged event on novel finding dispatch
- **WHEN** triage persists a `qa_findings` row with `dedup_reason = null` AND the dispatcher proceeds to insert a new `healing_attempts` row for that finding (novelty gate atomic claim succeeds)
- **THEN** triage inserts a `qa_investigation_events` row with `step = 'flagged'`, `attempt_id = <new attempt id>`, `finding_id = <the qa_findings id>`, `text` summarizing the trigger (e.g. `"patrol cycle <N> · failure_streak crossed <K>"` or `"novel finding from <source_type>"`), and a `detail` with the source butler, fingerprint prefix, and severity heuristic label
- **AND** the event's `ts` matches the patrol's `started_at` to within one second

#### Scenario: No flagged event for deduplicated findings
- **WHEN** triage persists a `qa_findings` row with a non-null `dedup_reason` (active_investigation, dismissed, cooldown, concurrency_cap, etc.)
- **THEN** no `flagged` journal event is emitted
- **AND** the row is still persisted for dashboard visibility per the existing Finding Persistence requirement

#### Scenario: Optional sampled event for cross-source corroboration
- **WHEN** triage observes the same fingerprint across two or more discovery sources within a single patrol cycle
- **THEN** triage MAY insert a `qa_investigation_events` row with `step = 'sampled'`, `text` describing the corroboration (`"corroborated across <N> sources"`), and a `detail` listing the source types
- **AND** v1 implementations are explicitly permitted to omit this emission

#### Scenario: Optional cross-checked event for dispatch-history corroboration
- **WHEN** triage observes a fingerprint that has had at least one terminal `healing_attempts` row in the prior 24 hours (regardless of cooldown state)
- **THEN** triage MAY insert a `qa_investigation_events` row with `step = 'cross-checked'`, `text` describing the prior context (`"prior attempts: <N> in 24h"`), and a `detail` summarizing their statuses
- **AND** v1 implementations are explicitly permitted to omit this emission
