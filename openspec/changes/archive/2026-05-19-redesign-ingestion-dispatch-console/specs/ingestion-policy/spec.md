## MODIFIED Requirements

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

Scope-aware validation on create/update is enforced **at the handler** (not only by the DB CHECK constraint):
- Global scope: action MUST be one of `skip`, `metadata_only`, `low_priority_queue`, `pass_through`, or `route_to:<butler>`.
- Connector scope (`scope LIKE 'connector:%'`): action MUST be exactly `block`. Any other action SHALL be rejected with HTTP 400 (not 422 or 500) and an error message naming the offending action and the allowed action set. The handler enforces this before any DB write, so a malformed request never reaches the CHECK constraint.
- `rule_type` MUST be compatible with the scope's connector type.

Mutations MUST invalidate the global evaluator cache. Connector caches refresh on their TTL cycle. Every mutation (create / update / delete / bulk operation) MUST emit an `audit.append()` entry with actor, action, target rule id(s), reason, and `request_id`.

#### Scenario: Create global rule
- **WHEN** POST `/ingestion-rules` with `scope = 'global'`, `rule_type = 'sender_domain'`, `action = 'route_to:finance'`
- **THEN** rule is created and returned with status 201

#### Scenario: Create connector-scoped rule with non-block action returns HTTP 400 at handler
- **WHEN** POST `/ingestion-rules` with `scope = 'connector:gmail:gmail:user:dev'` and `action = 'route_to:finance'` (or any action other than `block`)
- **THEN** the handler SHALL reject the request with HTTP 400 before any DB write occurs
- **AND** the response body SHALL identify the offending action and state that connector-scoped rules only support `action = 'block'`
- **AND** no row is inserted into `ingestion_rules`

#### Scenario: PATCH that would change action to non-block on connector scope returns HTTP 400
- **WHEN** PATCH `/ingestion-rules/{id}` targets a rule with `scope LIKE 'connector:%'` and sets `action` to anything other than `block`
- **THEN** the handler SHALL reject the request with HTTP 400 before any DB write

#### Scenario: PATCH that would change scope to connector with non-block action returns HTTP 400
- **WHEN** PATCH `/ingestion-rules/{id}` would result in `scope LIKE 'connector:%'` AND `action != 'block'`
- **THEN** the handler SHALL reject the request with HTTP 400 before any DB write

#### Scenario: List rules filtered by scope
- **WHEN** GET `/ingestion-rules?scope=connector:gmail:gmail:user:dev`
- **THEN** only rules with that exact scope are returned

#### Scenario: Dry-run test
- **WHEN** POST `/ingestion-rules/test` with a test envelope
- **THEN** the active rules are evaluated against the envelope and the PolicyDecision is returned without side effects

#### Scenario: Cache invalidation on mutation
- **WHEN** a rule is created, updated, or deleted (individually or via bulk endpoint)
- **THEN** the global evaluator cache is invalidated so the next evaluation loads fresh rules

#### Scenario: Audit log entry on mutation
- **WHEN** any mutation endpoint (POST, PATCH, DELETE, bulk) succeeds
- **THEN** an `audit.append()` entry is written with actor, action (`rule.create` / `rule.update` / `rule.delete` / `rule.bulk_<op>`), the affected rule id(s), reason (if provided), and the request's `request_id`

## ADDED Requirements

### Requirement: Bulk ingestion rule operations
The system SHALL expose a single bulk operations endpoint at `POST /api/switchboard/ingestion-rules/bulk` that supports `enable`, `disable`, and `delete` (soft-delete) over a list of rule ids in one request. The endpoint MUST cap a single request at 100 rule ids. All other validation (scope rules, connector-scope `block`-only enforcement) applies per-id.

The endpoint exists to avoid N round-trips from the dashboard when an operator wants to apply the same lifecycle action across many rules (typical for ingestion rule grooming). It is the only bulk surface; per-rule endpoints continue to handle single-rule mutations.

#### Scenario: Bulk disable
- **WHEN** POST `/api/switchboard/ingestion-rules/bulk` with body `{ "op": "disable", "ids": ["<id1>", "<id2>", ...] }` (≤ 100 ids)
- **THEN** each named rule SHALL have `enabled` set to `false` and `updated_at` set to now
- **AND** the global evaluator cache SHALL be invalidated exactly once for the batch
- **AND** a single `audit.append()` entry SHALL be written with action `rule.bulk_disable` listing all affected ids
- **AND** the response SHALL include per-id outcome (`ok` / `not_found` / `error_reason`)

#### Scenario: Bulk enable
- **WHEN** POST `/api/switchboard/ingestion-rules/bulk` with `{ "op": "enable", "ids": [...] }`
- **THEN** each named rule SHALL have `enabled` set to `true`
- **AND** rules whose current state would violate scope-aware action enforcement (e.g., a connector-scoped rule that was migrated with `action != 'block'`) SHALL be skipped with `error_reason = "scope_action_invalid"` and not enabled

#### Scenario: Bulk delete
- **WHEN** POST `/api/switchboard/ingestion-rules/bulk` with `{ "op": "delete", "ids": [...] }`
- **THEN** each named rule SHALL be soft-deleted: `deleted_at = now`, `enabled = false`
- **AND** a single `audit.append()` entry SHALL be written with action `rule.bulk_delete`

#### Scenario: Batch size cap enforcement
- **WHEN** POST `/api/switchboard/ingestion-rules/bulk` is called with more than 100 ids
- **THEN** the endpoint SHALL return HTTP 400 with an error naming the limit
- **AND** no rule is mutated

#### Scenario: Unknown op rejected
- **WHEN** the `op` field is not `enable`, `disable`, or `delete`
- **THEN** the endpoint SHALL return HTTP 400 and no rule is mutated

#### Scenario: Unknown id in batch
- **WHEN** a bulk request contains a rule id that does not exist (or is already soft-deleted)
- **THEN** the endpoint SHALL skip that id with per-id `not_found` outcome and continue processing the remaining ids
- **AND** the overall response SHALL be HTTP 200 with a per-id outcome map
