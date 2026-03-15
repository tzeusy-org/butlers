## ADDED Requirements

### Requirement: Ingestion rules data model

The system SHALL store all ingestion filtering and routing rules in a single `ingestion_rules` table in the switchboard schema. Each rule has a `scope` that determines its pipeline position: `'global'` rules are evaluated post-ingest/pre-LLM; `'connector:<type>:<identity>'` rules are evaluated at the connector before Switchboard submission.

The table schema:
- `id` UUID PRIMARY KEY (default gen_random_uuid())
- `scope` TEXT NOT NULL — `'global'` or `'connector:<connector_type>:<endpoint_identity>'`
- `rule_type` TEXT NOT NULL — unconstrained; known types: `sender_domain`, `sender_address`, `header_condition`, `mime_type`, `substring`, `chat_id`, `channel_id`
- `condition` JSONB NOT NULL — schema determined by `rule_type`
- `action` TEXT NOT NULL — `block`, `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, or `route_to:<butler>`
- `priority` INTEGER NOT NULL (>= 0) — lower = evaluated first
- `enabled` BOOLEAN NOT NULL DEFAULT TRUE
- `name` TEXT — optional human-readable label
- `description` TEXT — optional
- `created_by` TEXT NOT NULL DEFAULT `'dashboard'`
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT NOW()
- `deleted_at` TIMESTAMPTZ — soft-delete marker

Constraints:
- `scope = 'global' OR scope LIKE 'connector:%'`
- Connector-scoped rules MUST have `action = 'block'`
- `priority >= 0`

Indexes:
- `(scope, priority, created_at, id) WHERE enabled = TRUE AND deleted_at IS NULL` — primary query path per scope
- `(priority, created_at, id) WHERE scope = 'global' AND enabled = TRUE AND deleted_at IS NULL` — global-only fast path

#### Scenario: Global rule creation
- **WHEN** a rule is created with `scope = 'global'` and `action = 'route_to:finance'`
- **THEN** the rule is persisted and available to the post-ingest evaluator

#### Scenario: Connector-scoped rule creation
- **WHEN** a rule is created with `scope = 'connector:gmail:gmail:user:dev'` and `action = 'block'`
- **THEN** the rule is persisted and available to the Gmail connector's evaluator

#### Scenario: Connector-scoped rule action constraint
- **WHEN** a rule is created with `scope = 'connector:gmail:gmail:user:dev'` and `action = 'route_to:finance'`
- **THEN** the database CHECK constraint rejects the insert

#### Scenario: Soft delete
- **WHEN** a rule is deleted
- **THEN** `deleted_at` is set and `enabled` is set to FALSE; the rule is excluded from all evaluator queries

### Requirement: Condition schemas per rule type

Each `rule_type` defines the expected shape of the `condition` JSONB field. The API layer SHALL validate condition schemas on create and update.

| rule_type | condition schema | matching semantics |
|-----------|------------------|--------------------|
| `sender_domain` | `{"domain": "<string>", "match": "exact"\|"suffix"}` | Extract domain from sender address; exact or suffix match (case-insensitive) |
| `sender_address` | `{"address": "<string>"}` | Normalize full email address; exact match (case-insensitive) |
| `header_condition` | `{"header": "<string>", "op": "present"\|"equals"\|"contains", "value": null\|"<string>"}` | Case-insensitive header lookup; value required for equals/contains |
| `mime_type` | `{"type": "<string>"}` | Exact match or wildcard `type/*` |
| `substring` | `{"pattern": "<string>"}` | Case-insensitive substring search in raw key value |
| `chat_id` | `{"chat_id": "<string>"}` | Exact string equality |
| `channel_id` | `{"channel_id": "<string>"}` | Exact string equality |

#### Scenario: Valid sender_domain condition
- **WHEN** a rule is created with `rule_type = 'sender_domain'` and `condition = {"domain": "chase.com", "match": "suffix"}`
- **THEN** the API accepts the rule

#### Scenario: Invalid condition schema
- **WHEN** a rule is created with `rule_type = 'sender_domain'` and `condition = {"pattern": "chase.com"}`
- **THEN** the API returns 422 with a validation error

#### Scenario: Rule type compatibility with connector scope
- **WHEN** a rule is created with `scope = 'connector:telegram-bot:...'` and `rule_type = 'sender_domain'`
- **THEN** the API returns 422 because `sender_domain` is not valid for Telegram bot connectors

### Requirement: First-match-wins evaluation

The evaluation engine SHALL process rules in `priority ASC, created_at ASC, id ASC` order. The first rule whose condition matches the input envelope determines the outcome. If no rule matches, the result is `pass_through`.

This applies uniformly to both connector-scoped and global evaluation — there is no separate blacklist/whitelist composition model.

#### Scenario: First match wins
- **WHEN** two rules exist at priority 10 (block spammer.com) and priority 20 (block all .com)
- **AND** a message arrives from `spammer.com`
- **THEN** the priority-10 rule matches first and its action is applied

#### Scenario: No match defaults to pass_through
- **WHEN** no active rules match the incoming message
- **THEN** the evaluator returns `pass_through`

#### Scenario: Whitelist-equivalent pattern
- **WHEN** rules exist: priority 10 pass_through for `trusted.com`, priority 1000 block catch-all
- **AND** a message arrives from `trusted.com`
- **THEN** the priority-10 pass_through rule matches first, allowing the message

### Requirement: IngestionPolicyEvaluator

The system SHALL provide a single `IngestionPolicyEvaluator` class that handles both connector-scoped and global evaluation. An evaluator instance is created with a `scope` string and loads only rules matching that scope.

```
IngestionPolicyEvaluator(
    scope: str,                     # "global" or "connector:gmail:gmail:user:dev"
    db_pool: asyncpg.Pool | None,
    refresh_interval_s: float = 60,
)
```

Methods:
- `async ensure_loaded() -> None` — initial load, must call before first evaluation
- `evaluate(envelope: IngestionEnvelope) -> PolicyDecision` — synchronous, cache-based, no DB I/O
- `invalidate() -> None` — mark cache stale, forces refresh on next evaluate

The evaluator uses TTL-based cache refresh (default 60s). On DB error, it retains the previous cache (fail-open). Background refresh is non-blocking — `evaluate()` always returns from the current cache.

#### Scenario: Connector-scoped evaluator loads only connector rules
- **WHEN** an evaluator is created with `scope = 'connector:gmail:gmail:user:dev'`
- **THEN** it loads only rules where `scope = 'connector:gmail:gmail:user:dev'` and `enabled = TRUE` and `deleted_at IS NULL`

#### Scenario: Global evaluator loads only global rules
- **WHEN** an evaluator is created with `scope = 'global'`
- **THEN** it loads only rules where `scope = 'global'` and `enabled = TRUE` and `deleted_at IS NULL`

#### Scenario: Fail-open on DB error
- **WHEN** the DB is unreachable during a cache refresh
- **THEN** the evaluator retains its previous cache and logs a warning

#### Scenario: TTL-based background refresh
- **WHEN** the cache age exceeds `refresh_interval_s`
- **AND** `evaluate()` is called
- **THEN** a background task is scheduled to reload the cache without blocking the current evaluation

### Requirement: IngestionEnvelope

The evaluator accepts an `IngestionEnvelope` dataclass that carries all fields needed by any rule type:

- `sender_address: str` — normalized email or empty string
- `source_channel: str` — `"email"`, `"telegram"`, `"discord"`
- `headers: dict[str, str]` — email headers with case-insensitive keys; empty dict for non-email
- `mime_parts: list[str]` — MIME type strings; empty list for non-email
- `thread_id: str | None` — external thread identity
- `raw_key: str` — raw opaque key for substring/chat_id/channel_id matching

Connectors populate only the fields relevant to their channel. The evaluator extracts the appropriate key per rule's `rule_type` internally.

#### Scenario: Gmail connector populates envelope
- **WHEN** the Gmail connector builds an IngestionEnvelope
- **THEN** `sender_address` contains the normalized From address, `source_channel = "email"`, `headers` contains email headers, `raw_key` contains the raw From header

#### Scenario: Telegram connector populates envelope
- **WHEN** the Telegram bot connector builds an IngestionEnvelope
- **THEN** `sender_address` is empty, `source_channel = "telegram"`, `headers` is empty, `raw_key` contains the chat_id string

### Requirement: PolicyDecision

The evaluator returns a `PolicyDecision` dataclass:

- `action: str` — the matched rule's action, or `"pass_through"` if no match
- `target_butler: str | None` — extracted from `route_to:<butler>` if applicable
- `matched_rule_id: str | None` — UUID of the matched rule
- `matched_rule_type: str | None` — rule_type of the matched rule
- `reason: str` — human-readable explanation
- `bypasses_llm: bool` (property) — True when action is not `pass_through`

#### Scenario: Block decision
- **WHEN** a connector-scoped rule matches with `action = 'block'`
- **THEN** `PolicyDecision(action='block', matched_rule_id=<id>, reason='block:<rule_name or condition summary>')`

#### Scenario: Route decision
- **WHEN** a global rule matches with `action = 'route_to:finance'`
- **THEN** `PolicyDecision(action='route_to:finance', target_butler='finance', bypasses_llm=True)`

#### Scenario: Pass-through decision
- **WHEN** no rule matches
- **THEN** `PolicyDecision(action='pass_through', matched_rule_id=None, bypasses_llm=False)`

### Requirement: Ingestion rules REST API

The switchboard API SHALL expose unified CRUD endpoints at `/api/switchboard/ingestion-rules`:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ingestion-rules` | List rules. Optional query params: `scope`, `rule_type`, `action`, `enabled` |
| POST | `/ingestion-rules` | Create rule. Validates condition schema and action per scope. Returns 201. |
| GET | `/ingestion-rules/{id}` | Get single rule. Returns 404 if not found or soft-deleted. |
| PATCH | `/ingestion-rules/{id}` | Partial update: condition, action, priority, enabled, scope, name, description. |
| DELETE | `/ingestion-rules/{id}` | Soft-delete. Sets deleted_at and enabled=false. |
| POST | `/ingestion-rules/test` | Dry-run: evaluate a test envelope against active rules. Returns the PolicyDecision. |

Scope-aware validation on create/update:
- Global scope: action MUST be one of skip, metadata_only, low_priority_queue, pass_through, or route_to:<butler>
- Connector scope: action MUST be `block`
- rule_type MUST be compatible with the scope's connector type

Mutations MUST invalidate the global evaluator cache. Connector caches refresh on their TTL cycle.

#### Scenario: Create global rule
- **WHEN** POST `/ingestion-rules` with `scope = 'global'`, `rule_type = 'sender_domain'`, `action = 'route_to:finance'`
- **THEN** rule is created and returned with status 201

#### Scenario: Create connector-scoped rule with invalid action
- **WHEN** POST `/ingestion-rules` with `scope = 'connector:gmail:...'`, `action = 'route_to:finance'`
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

### Requirement: Data migration from legacy tables

The migration SHALL transfer all existing data from `triage_rules`, `source_filters`, and `connector_source_filters` into `ingestion_rules` with zero data loss.

Migration rules:
- **Triage rules** → `scope = 'global'`, all fields mapped directly (id, rule_type, condition, action, priority, enabled, created_by, timestamps, deleted_at)
- **Source filter blacklist patterns** → one `ingestion_rules` row per pattern per connector assignment, with `scope = 'connector:<type>:<identity>'`, `action = 'block'`, priority from assignment
- **Source filter whitelist patterns** → one `pass_through` row per pattern per connector assignment, plus one catch-all `block` row at priority = assignment priority + 1000

#### Scenario: Triage rule migration
- **WHEN** the migration runs
- **THEN** each row in `triage_rules` has a corresponding row in `ingestion_rules` with `scope = 'global'` and identical field values

#### Scenario: Blacklist source filter migration
- **WHEN** a source filter exists with `filter_mode = 'blacklist'`, patterns `['spammer.com', 'junk.org']`, assigned to `gmail:user:dev` with priority 5
- **THEN** two `ingestion_rules` rows are created: one per pattern, each with `scope = 'connector:gmail:gmail:user:dev'`, `rule_type` matching the source filter's `source_key_type`, `action = 'block'`, priority 5

#### Scenario: Whitelist source filter migration
- **WHEN** a source filter exists with `filter_mode = 'whitelist'`, patterns `['trusted.com']`, assigned to `gmail:user:dev` with priority 10
- **THEN** one `pass_through` rule (priority 10) and one catch-all `block` rule (priority 1010) are created for that connector scope

#### Scenario: Disabled assignments are not migrated as enabled
- **WHEN** a connector_source_filters row has `enabled = false`
- **THEN** the migrated ingestion_rules rows have `enabled = false`

### Requirement: Ingestion policy observability

The system SHALL emit unified OpenTelemetry metrics replacing both triage and source filter metrics:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `butlers.ingestion.rule_matched` | Counter | `scope_type`, `rule_type`, `action`, `source_channel` | Incremented when a rule matches |
| `butlers.ingestion.rule_pass_through` | Counter | `scope_type`, `source_channel`, `reason` | Incremented on no-match pass_through |
| `butlers.ingestion.evaluation_latency_ms` | Histogram | `scope_type`, `result` | End-to-end evaluation latency |

`scope_type` label is bounded to `global` or `connector` (endpoint identity stripped for cardinality safety). `action` label strips butler name (`route_to:finance` → `route_to`).

#### Scenario: Connector-scoped block is recorded
- **WHEN** a connector-scoped rule blocks a message
- **THEN** `rule_matched` counter is incremented with `scope_type=connector`, `action=block`

#### Scenario: Global route is recorded
- **WHEN** a global rule routes a message to finance
- **THEN** `rule_matched` counter is incremented with `scope_type=global`, `action=route_to`

#### Scenario: No-match pass_through is recorded
- **WHEN** no rule matches at global scope
- **THEN** `rule_pass_through` counter is incremented with `scope_type=global`, `reason=no_match`
