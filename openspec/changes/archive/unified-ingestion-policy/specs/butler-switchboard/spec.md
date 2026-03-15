## MODIFIED Requirements

### Requirement: Deterministic Pre-Classification Triage

Before invoking LLM classification, Switchboard runs the unified ingestion policy evaluator with `scope = 'global'` that can route, skip, or deprioritize messages without LLM cost. This replaces the previous triage-specific rule system with the shared `IngestionPolicyEvaluator`.

#### Scenario: Rule-based first-match routing
- **WHEN** a message is accepted by the Switchboard
- **THEN** the global `IngestionPolicyEvaluator` evaluates it in `priority ASC, created_at ASC, id ASC` order; the first matching rule determines the action

#### Scenario: Supported rule types
- **WHEN** global ingestion rules are loaded
- **THEN** all rule types are valid: `sender_domain`, `sender_address`, `header_condition`, `mime_type`, `substring`, `chat_id`, `channel_id`

#### Scenario: Triage actions
- **WHEN** a global rule matches
- **THEN** its action is one of: `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, or `route_to:<butler>`

#### Scenario: Fail-open semantics
- **WHEN** the evaluator cannot load rules from the database
- **THEN** it retains the previous cache; if no cache exists, all messages pass through to LLM classification

#### Scenario: Ingestion rule cache
- **WHEN** the Switchboard starts
- **THEN** it creates a global `IngestionPolicyEvaluator(scope='global')` with 60-second TTL refresh and calls `ensure_loaded()` before processing messages

### Requirement: Triage Rule Management

Dashboard and operator tools for managing ingestion rules via the unified `/api/switchboard/ingestion-rules` endpoints. All rule scopes (global and connector-scoped) are managed through the same API.

#### Scenario: Ingestion rule CRUD
- **WHEN** a user creates, updates, or deletes a rule via the API
- **THEN** the global evaluator cache is invalidated and changes take effect within 60 seconds for connector-scoped evaluators

#### Scenario: Thread affinity settings
- **WHEN** a user manages thread affinity settings
- **THEN** the existing `/api/switchboard/thread-affinity` endpoints remain unchanged (thread affinity is not part of the unified ingestion rules)

### Requirement: Triage Observability

The ingestion policy subsystem emits unified OpenTelemetry metrics for monitoring rule effectiveness across both connector-scoped and global evaluation.

#### Scenario: Rule match metrics
- **WHEN** any rule matches (connector-scoped or global)
- **THEN** the `butlers.ingestion.rule_matched` counter is incremented with labels: `scope_type` (global or connector), `rule_type`, `action` (normalized — `route_to` without target), `source_channel`

#### Scenario: Pass-through metrics
- **WHEN** no rule matches at either scope
- **THEN** the `butlers.ingestion.rule_pass_through` counter is incremented with labels: `scope_type`, `source_channel`, `reason` (no_match, cache_unavailable)

## REMOVED Requirements

### Requirement: Triage-specific rule cache
**Reason**: Replaced by the unified `IngestionPolicyEvaluator` cache. The `TriageRuleCache` class is removed.
**Migration**: The Switchboard creates an `IngestionPolicyEvaluator(scope='global')` instead of a `TriageRuleCache`. The evaluator provides equivalent caching with `ensure_loaded()`, TTL refresh, and `invalidate()`.

### Requirement: Triage-specific evaluator
**Reason**: Replaced by the unified `IngestionPolicyEvaluator`. The `evaluate_triage()` function, `TriageEnvelope`, and `TriageDecision` dataclasses are removed.
**Migration**: The Switchboard calls `evaluator.evaluate(envelope)` which returns `PolicyDecision`. The `IngestionEnvelope` replaces `TriageEnvelope` with the same fields plus `raw_key`.

### Requirement: Triage-specific API endpoints
**Reason**: Replaced by unified `/api/switchboard/ingestion-rules` endpoints.
**Migration**: All `/api/switchboard/triage-rules/*` endpoints are removed. The `/ingestion-rules` endpoints provide equivalent CRUD with additional scope-aware filtering. The `/ingestion-rules/test` endpoint replaces `/triage-rules/test`.
