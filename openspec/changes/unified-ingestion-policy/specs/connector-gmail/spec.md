## MODIFIED Requirements

### Requirement: Source Filter Integration (Gmail)

The Gmail connector implements the ingestion policy gate using `IngestionPolicyEvaluator` with `scope = 'connector:gmail:<endpoint_identity>'`. It builds an `IngestionEnvelope` from the Gmail message's `From` header. Compatible rule types for Gmail connector scope: `sender_domain`, `sender_address`, `substring`.

#### Scenario: IngestionPolicyEvaluator instantiation
- **WHEN** the Gmail connector initializes
- **THEN** it creates an `IngestionPolicyEvaluator` with `scope = 'connector:gmail:<endpoint_identity>'` and the shared switchboard DB pool

#### Scenario: Filter gate position in Gmail pipeline
- **WHEN** the Gmail connector processes an incoming message
- **THEN** it evaluates the message via `IngestionPolicyEvaluator` AFTER label filtering and BEFORE Switchboard submission

#### Scenario: Valid rule types for Gmail connector scope
- **WHEN** the API validates a rule for `scope = 'connector:gmail:...'`
- **THEN** only `sender_domain`, `sender_address`, and `substring` rule types are accepted

#### Scenario: Envelope construction from Gmail message
- **WHEN** the Gmail connector builds an `IngestionEnvelope`
- **THEN** `sender_address` is the normalized From address (lowercase, no brackets), `source_channel = "email"`, `headers` contains the message headers, `raw_key` is the raw From header value

#### Scenario: Blocked message in live ingestion
- **WHEN** the evaluator returns `PolicyDecision(action='block')` for a live Gmail message
- **THEN** the message is skipped, not submitted to Switchboard, and the connector advances its cursor

#### Scenario: Blocked message in backfill
- **WHEN** the evaluator returns `PolicyDecision(action='block')` during a backfill job
- **THEN** the message is counted as skipped and the backfill continues to the next message
