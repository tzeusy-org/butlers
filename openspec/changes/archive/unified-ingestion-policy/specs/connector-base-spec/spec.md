## MODIFIED Requirements

### Requirement: Source Filter Gate (Base Contract)

All connectors MUST implement the ingestion policy gate as a mandatory pipeline step. After normalizing an event and before submitting to the Switchboard, each connector evaluates the message against its active connector-scoped ingestion rules via `IngestionPolicyEvaluator`. Messages that receive a `block` action are dropped at the connector and never reach the Switchboard.

The evaluator is instantiated with `scope = 'connector:<connector_type>:<endpoint_identity>'` and loads only rules matching that scope from the unified `ingestion_rules` table.

#### Scenario: Filter gate position in the connector pipeline
- **WHEN** a connector processes an incoming message
- **THEN** the connector evaluates the message against its `IngestionPolicyEvaluator` AFTER normalization and BEFORE submitting to the Switchboard

#### Scenario: Blocked message handling
- **WHEN** the evaluator returns `PolicyDecision(action='block')`
- **THEN** the message is NOT submitted to the Switchboard, the Prometheus counter is incremented, and the connector advances its checkpoint

#### Scenario: Filter state at startup
- **WHEN** a connector starts its ingestion loop
- **THEN** it MUST call `evaluator.ensure_loaded()` before processing the first message

#### Scenario: DB error fail-open behavior
- **WHEN** the evaluator cannot reach the database during a cache refresh
- **THEN** it retains its previous cache and logs a warning; ingestion is NOT blocked

#### Scenario: IngestionPolicyEvaluator contract
- **WHEN** a connector instantiates its evaluator
- **THEN** it passes `scope = 'connector:<connector_type>:<endpoint_identity>'` and a shared DB pool; the evaluator loads only connector-scoped rules for that scope

### Requirement: Triage Integration

Connector-side and server-side ingestion rules gate ingestion and early routing decisions before LLM classification. Connector-scoped rules (`block` action) are evaluated at the connector. Global rules (all other actions) are evaluated post-ingest by the Switchboard.

#### Scenario: Thread affinity lookup (email only)
- **WHEN** an email message is ingested with a thread_id
- **THEN** Switchboard checks thread affinity BEFORE evaluating global ingestion rules

#### Scenario: Deterministic rule evaluation
- **WHEN** a message passes connector-scoped evaluation and is accepted by the Switchboard
- **THEN** global ingestion rules are evaluated in priority order; the first match determines routing/action

#### Scenario: Ingestion tier classification
- **WHEN** no global ingestion rule matches (pass_through)
- **THEN** the message proceeds to LLM classification

## REMOVED Requirements

### Requirement: SourceFilterEvaluator contract
**Reason**: Replaced by unified `IngestionPolicyEvaluator`. The `SourceFilterEvaluator` class, `SourceFilterSpec`, `FilterResult`, and all `extract_*_filter_key()` helpers are removed.
**Migration**: All connectors switch to `IngestionPolicyEvaluator` with `scope = 'connector:<type>:<identity>'`. The `evaluate()` method returns `PolicyDecision` instead of `FilterResult`. Key extraction logic moves into the unified evaluator's condition matching.
