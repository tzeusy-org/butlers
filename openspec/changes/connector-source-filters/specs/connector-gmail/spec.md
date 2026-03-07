# Gmail Connector (delta)

## ADDED Requirements

### Requirement: Source Filter Integration (Gmail)
The Gmail connector MUST implement the source filter gate using the sender address as the evaluated key, with support for `domain`, `sender_address`, and `substring` key types.

#### Scenario: Valid source key types for Gmail
- **WHEN** source filters are configured for a Gmail connector
- **THEN** the valid `source_key_type` values are: `"domain"`, `"sender_address"`, `"substring"`
- **AND** filters with any other `source_key_type` are skipped with a one-time WARNING log (they are incompatible with the email channel)

#### Scenario: Key extraction for domain filters
- **WHEN** the filter gate evaluates a Gmail message with `source_key_type="domain"`
- **THEN** the connector normalizes the `From` header (strip display name and angle brackets, lowercase) and extracts the domain part (substring after `@`)
- **AND** passes the bare domain string (e.g. `"newsletter.example.com"`) to `SourceFilterEvaluator.evaluate()`

#### Scenario: Key extraction for sender_address filters
- **WHEN** the filter gate evaluates a Gmail message with `source_key_type="sender_address"`
- **THEN** the connector normalizes the `From` header to a bare, lowercased email address (e.g. `"alice@example.com"`)
- **AND** passes the full normalized address to `SourceFilterEvaluator.evaluate()`

#### Scenario: Key extraction for substring filters
- **WHEN** the filter gate evaluates a Gmail message with `source_key_type="substring"`
- **THEN** the connector passes the raw `From` header value verbatim to `SourceFilterEvaluator.evaluate()`
- **AND** matching is case-insensitive substring search

#### Scenario: Filter gate position in Gmail pipeline
- **WHEN** the Gmail connector processes a message
- **THEN** source filter evaluation runs AFTER label filtering (`LabelFilterPolicy`) and BEFORE triage rule evaluation
- **AND** the pipeline order is: (1) label include/exclude filter → (2) source filter gate → (3) triage rule evaluation for ingestion tier → (4) policy tier assignment → (5) Switchboard submission
- **AND** a message blocked by the source filter gate is dropped without incrementing Gmail label filter counters (label filter already passed at step 1)

#### Scenario: SourceFilterEvaluator instantiation
- **WHEN** the Gmail connector starts
- **THEN** it instantiates `SourceFilterEvaluator(connector_type="gmail", endpoint_identity=<configured endpoint identity>, db_pool=<shared switchboard pool>)`
- **AND** performs the initial filter load before beginning the watch/history-delta ingestion loop
