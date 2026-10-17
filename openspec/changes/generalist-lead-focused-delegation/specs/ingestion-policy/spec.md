## MODIFIED Requirements

### Requirement: Ingestion rules data model

The system SHALL store all ingestion filtering rules in a single `ingestion_rules` table in the switchboard schema. Each rule has a `scope` that determines its pipeline position: `'global'` rules are evaluated post-ingest and before goal admission; `'connector:<type>:<identity>'` rules are evaluated at the connector before Switchboard submission. No rule SHALL select a target butler: a stored `route_to:<butler>` action SHALL be evaluated as `pass_through`, so a matching envelope reaches goal admission with no target, and the stored row SHALL be retained unchanged as history.

The table schema:
- `id` UUID PRIMARY KEY (default gen_random_uuid())
- `scope` TEXT NOT NULL -- `'global'` or `'connector:<connector_type>:<endpoint_identity>'`
- `rule_type` TEXT NOT NULL -- unconstrained; known types: `sender_domain`, `sender_address`, `source_endpoint`, `header_condition`, `mime_type`, `substring`, `chat_id`, `channel_id`, `source_channel`, `mic_id`. `source_endpoint` is a global API-creatable exact-match rule. `source_channel` and `mic_id` are evaluated by the engine and seeded via migrations (the OwnTracks/Home Assistant global `skip` rules and the live-listener voice gate) but are not accepted by the REST create/update validator.
- `condition` JSONB NOT NULL -- schema determined by `rule_type`
- `action` TEXT NOT NULL -- `block`, `skip`, `metadata_only`, `low_priority_queue`, or `pass_through`; historic rows may hold `route_to:<butler>`, which is evaluated as `pass_through`
- `priority` INTEGER NOT NULL (>= 0) -- lower = evaluated first
- `enabled` BOOLEAN NOT NULL DEFAULT TRUE
- `name` TEXT -- optional human-readable label
- `description` TEXT -- optional
- `created_by` TEXT NOT NULL DEFAULT `'dashboard'` -- conventional values include `'dashboard'`, `'api'`, `'seed'`, `'migration'`, and `'promotion'` (rules minted via the switchboard rule-promotion suggestion-confirmation flow); the column remains unconstrained TEXT, this is a documented convention, not a DB CHECK
- `promoted_from_suggestion_id` UUID, nullable -- FK to `switchboard.rule_promotion_suggestions(id)`, set only on rules created via promotion-suggestion confirmation; NULL for all other creation paths
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `deleted_at` TIMESTAMPTZ -- soft-delete marker

Constraints:
- `scope = 'global' OR scope LIKE 'connector:%'`
- Connector-scoped rules MUST satisfy `action IN ('block', 'pass_through')` (DB CHECK: `scope = 'global' OR action IN ('block', 'pass_through')`). `pass_through` exists only to support whitelist-equivalent connector rules emitted by the source-filter migration. The REST create/update API is stricter and accepts only `block` for connector scope, so connector `pass_through` rows are migration-seeded, never API-created.
- `priority >= 0`

Indexes:
- `(scope, priority, created_at, id) WHERE enabled = TRUE AND deleted_at IS NULL` -- primary query path per scope
- `(priority, created_at, id) WHERE scope = 'global' AND enabled = TRUE AND deleted_at IS NULL` -- global-only fast path

Source: RFC 0038 D1, D5
Scope: v1-mandatory

#### Scenario: Global rule creation
- **WHEN** a rule is created with `scope = 'global'` and `action = 'skip'`
- **THEN** the rule is persisted and available to the post-ingest evaluator

#### Scenario: Connector-scoped rule creation
- **WHEN** a rule is created with `scope = 'connector:gmail:gmail:user:dev'` and `action = 'block'`
- **THEN** the rule is persisted and available to the Gmail connector's evaluator

#### Scenario: Connector-scoped rule action constraint
- **WHEN** a rule is created with `scope = 'connector:gmail:gmail:user:dev'` and `action = 'skip'`
- **THEN** the database CHECK constraint rejects the insert

#### Scenario: Soft delete
- **WHEN** a rule is deleted
- **THEN** `deleted_at` is set and `enabled` is set to FALSE; the rule is excluded from all evaluator queries

#### Scenario: Promoted rule carries suggestion provenance
- **WHEN** a rule is created through the rule-promotion suggestion-confirmation flow (see `switchboard-rule-promotion` spec)
- **THEN** the rule's `created_by` field MUST be `'promotion'` and `promoted_from_suggestion_id` MUST reference the originating `rule_promotion_suggestions` row

#### Scenario: Non-promoted rules leave provenance null
- **WHEN** a rule is created via the existing dashboard CRUD API, a migration seed, or any path other than suggestion confirmation
- **THEN** `promoted_from_suggestion_id` MUST be NULL, and existing `created_by` behavior (e.g. `'dashboard'`) is unchanged

#### Scenario: Historic route rule admits without a target
- **WHEN** an enabled global rule stored with `action = 'route_to:finance'` matches an envelope
- **THEN** the envelope is submitted to goal admission with no target butler
- **AND** the stored rule row is not rewritten

### Requirement: PolicyDecision

The evaluator SHALL return a `PolicyDecision` dataclass that never names a target butler:

- `action: str` -- the matched rule's action, or `"pass_through"` if no match or if the matched rule holds a historic `route_to:<butler>` action
- `matched_rule_id: str | None` -- UUID of the matched rule
- `matched_rule_type: str | None` -- rule_type of the matched rule
- `reason: str` -- human-readable explanation
- `bypasses_llm: bool` (property) -- True when action is not `pass_through`, meaning the envelope is not submitted to goal admission

Source: RFC 0038 D1, D5
Scope: v1-mandatory

#### Scenario: Block decision
- **WHEN** a connector-scoped rule matches with `action = 'block'`
- **THEN** `PolicyDecision(action='block', matched_rule_id=<id>, reason='block:<rule_name or condition summary>')`

#### Scenario: Route decision
- **WHEN** a global rule stored with `action = 'route_to:finance'` matches
- **THEN** `PolicyDecision(action='pass_through', matched_rule_id=<id>, bypasses_llm=False)`
- **AND** the decision carries no target butler

#### Scenario: Pass-through decision
- **WHEN** no rule matches
- **THEN** `PolicyDecision(action='pass_through', matched_rule_id=None, bypasses_llm=False)`

### Requirement: Ingestion rules REST API

The switchboard API SHALL expose unified CRUD endpoints at `/api/switchboard/ingestion-rules`, and SHALL reject any create or update whose action is `route_to:<butler>`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ingestion-rules` | List rules. Optional query params: `scope`, `rule_type`, `action`, `enabled` |
| POST | `/ingestion-rules` | Create rule. Validates condition schema and action per scope. Returns 201. |
| GET | `/ingestion-rules/{id}` | Get single rule. Returns 404 if not found or soft-deleted. |
| PATCH | `/ingestion-rules/{id}` | Partial update: condition, action, priority, enabled, scope, name, description. |
| DELETE | `/ingestion-rules/{id}` | Soft-delete. Sets deleted_at and enabled=false. |
| POST | `/ingestion-rules/test` | Dry-run: evaluate a test envelope against active rules. Returns the PolicyDecision. |

Scope-aware validation on create/update:
- Global scope: action MUST be one of skip, metadata_only, low_priority_queue, or pass_through
- Connector scope: action MUST be `block`
- rule_type MUST be compatible with the scope's connector type

Mutations MUST invalidate the global evaluator cache. Connector caches refresh on their TTL cycle.

Source: RFC 0038 D1, D5
Scope: v1-mandatory

#### Scenario: Create global rule
- **WHEN** POST `/ingestion-rules` with `scope = 'global'`, `rule_type = 'sender_domain'`, `action = 'skip'`
- **THEN** rule is created and returned with status 201

#### Scenario: Create global rule with a route action
- **WHEN** POST `/ingestion-rules` with `scope = 'global'`, `action = 'route_to:finance'`
- **THEN** API returns 422 and no rule is created

#### Scenario: Create connector-scoped rule with invalid action
- **WHEN** POST `/ingestion-rules` with `scope = 'connector:gmail:...'`, `action = 'skip'`
- **THEN** API returns 422 with error: connector-scoped rules only support block

#### Scenario: List rules filtered by scope
- **WHEN** GET `/ingestion-rules?scope=connector:gmail:gmail:user:dev`
- **THEN** only rules with that exact scope are returned

#### Scenario: Dry-run test
- **WHEN** POST `/ingestion-rules/test` with a test envelope
- **THEN** the active rules are evaluated against the envelope and the PolicyDecision is returned without side effects

#### Scenario: Cache invalidation on mutation
- **WHEN** a rule is created, updated, or deleted
- **THEN** the global evaluator cache is invalidated so the next evaluation loads fresh rules

### Requirement: Ingestion policy observability

The system SHALL emit unified OpenTelemetry metrics replacing both triage and source filter metrics:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `butlers.ingestion.rule_matched` | Counter | `scope_type`, `rule_type`, `action`, `source_channel` | Incremented when a rule matches |
| `butlers.ingestion.rule_pass_through` | Counter | `scope_type`, `source_channel`, `reason` | Incremented on no-match pass_through |
| `butlers.ingestion.evaluation_latency_ms` | Histogram | `scope_type`, `result` | End-to-end evaluation latency |

`scope_type` label is bounded to `global` or `connector` (endpoint identity stripped for cardinality safety). The `action` label SHALL be the evaluated action and never carries a butler name; a historic `route_to:<butler>` match is labelled `pass_through`.

Source: RFC 0038 D1, D5
Scope: v1-mandatory

#### Scenario: Connector-scoped block is recorded
- **WHEN** a connector-scoped rule blocks a message
- **THEN** `rule_matched` counter is incremented with `scope_type=connector`, `action=block`

#### Scenario: Global route is recorded
- **WHEN** a historic global rule stored with `action = 'route_to:finance'` matches a message
- **THEN** `rule_matched` counter is incremented with `scope_type=global`, `action=pass_through`
- **AND** no label carries a butler name

#### Scenario: No-match pass_through is recorded
- **WHEN** no rule matches at global scope
- **THEN** `rule_pass_through` counter is incremented with `scope_type=global`, `reason=no_match`
