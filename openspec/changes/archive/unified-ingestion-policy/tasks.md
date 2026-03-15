## 1. Database Migration

- [ ] 1.1 Create migration file with `ingestion_rules` table DDL (schema, constraints, indexes per design D9)
- [ ] 1.2 Add data migration: copy `triage_rules` rows into `ingestion_rules` with `scope = 'global'`
- [ ] 1.3 Add data migration: expand blacklist `source_filters` × `connector_source_filters` into per-pattern `block` rules with connector scope
- [ ] 1.4 Add data migration: expand whitelist `source_filters` × `connector_source_filters` into per-pattern `pass_through` rules + catch-all `block` rule
- [ ] 1.5 Add migration verification: assert row counts (triage_rules migrated + source_filter expansions = ingestion_rules total)
- [ ] 1.6 Write migration test covering triage rules, blacklist, whitelist, disabled assignments, and empty tables

## 2. Unified Evaluator — IngestionPolicyEvaluator

- [ ] 2.1 Create `src/butlers/ingestion_policy.py` with `IngestionEnvelope` and `PolicyDecision` dataclasses
- [ ] 2.2 Implement `IngestionPolicyEvaluator.__init__` with scope, db_pool, refresh_interval_s params
- [ ] 2.3 Implement `_load_rules()` — SQL query filtered by scope, enabled, not deleted, ordered by priority/created_at/id
- [ ] 2.4 Implement `ensure_loaded()` with asyncio lock for initial load
- [ ] 2.5 Implement condition matchers: `sender_domain` (exact/suffix), `sender_address` (exact), `header_condition` (present/equals/contains), `mime_type` (exact/wildcard)
- [ ] 2.6 Implement condition matchers: `substring` (case-insensitive), `chat_id` (exact), `channel_id` (exact)
- [ ] 2.7 Implement `evaluate()` — first-match-wins loop, key extraction per rule_type, returns PolicyDecision
- [ ] 2.8 Implement TTL-based background refresh via `_maybe_schedule_refresh()` (non-blocking)
- [ ] 2.9 Implement `invalidate()` method for cache invalidation on mutations
- [ ] 2.10 Write unit tests for evaluator: all rule types, first-match-wins ordering, no-match pass_through, fail-open on DB error, TTL refresh

## 3. Observability

- [ ] 3.1 Create telemetry module with `butlers.ingestion.rule_matched`, `rule_pass_through`, and `evaluation_latency_ms` metrics
- [ ] 3.2 Integrate telemetry into `IngestionPolicyEvaluator.evaluate()` with cardinality-safe labels
- [ ] 3.3 Write tests for telemetry label bounding (scope_type, action normalization)

## 4. Backend API — Ingestion Rules CRUD

- [ ] 4.1 Add Pydantic models: `IngestionRule`, `IngestionRuleCreate`, `IngestionRuleUpdate`, `IngestionRuleTestRequest`, `IngestionRuleTestResponse`
- [ ] 4.2 Add condition schema validators per rule_type (reuse/adapt from existing triage models)
- [ ] 4.3 Add scope-aware action validation (connector scope → block only; global → full action set)
- [ ] 4.4 Add rule_type compatibility validation per connector type (gmail → domain/address/substring; telegram-bot → chat_id; discord → channel_id)
- [ ] 4.5 Implement GET `/ingestion-rules` — list with optional scope, rule_type, action, enabled filters
- [ ] 4.6 Implement POST `/ingestion-rules` — create with validation, return 201
- [ ] 4.7 Implement GET `/ingestion-rules/{id}` — single rule, 404 if not found/deleted
- [ ] 4.8 Implement PATCH `/ingestion-rules/{id}` — partial update with re-validation
- [ ] 4.9 Implement DELETE `/ingestion-rules/{id}` — soft-delete (set deleted_at + enabled=false)
- [ ] 4.10 Implement POST `/ingestion-rules/test` — dry-run evaluation against active rules
- [ ] 4.11 Add cache invalidation hook on create/update/delete mutations
- [ ] 4.12 Write API tests for all endpoints, validation errors, scope constraints, and cache invalidation

## 5. Remove Old API Endpoints

- [ ] 5.1 Remove `/source-filters` CRUD endpoints from router.py
- [ ] 5.2 Remove `/connectors/{type}/{identity}/filters` assignment endpoints from router.py
- [ ] 5.3 Remove `/triage-rules` CRUD + test endpoints from router.py
- [ ] 5.4 Remove old Pydantic models (SourceFilter*, TriageRule*, ConnectorFilterAssignment*)
- [ ] 5.5 Update or remove old API test files (test_switchboard_source_filters.py, test_switchboard_connector_filters.py, triage rule tests)

## 6. Connector Integration — Gmail

- [ ] 6.1 Replace `SourceFilterEvaluator` instantiation with `IngestionPolicyEvaluator(scope='connector:gmail:<identity>')` in GmailConnector.__init__
- [ ] 6.2 Replace `evaluate(from_header)` calls with `evaluate(IngestionEnvelope(...))` in live ingestion path
- [ ] 6.3 Replace `evaluate()` calls in backfill path
- [ ] 6.4 Remove `extract_gmail_filter_key` imports
- [ ] 6.5 Update Gmail connector tests

## 7. Connector Integration — Telegram Bot

- [ ] 7.1 Replace `SourceFilterEvaluator` with `IngestionPolicyEvaluator(scope='connector:telegram-bot:<identity>')` in TelegramBotConnector
- [ ] 7.2 Replace evaluate calls with `IngestionEnvelope` construction (raw_key = chat_id string)
- [ ] 7.3 Remove `extract_telegram_filter_key` imports
- [ ] 7.4 Update Telegram bot connector tests

## 8. Connector Integration — Telegram User Client + Discord

- [ ] 8.1 Replace `SourceFilterEvaluator` with `IngestionPolicyEvaluator` in TelegramUserClientConnector (scope: `connector:telegram-user-client:<identity>`)
- [ ] 8.2 Replace `SourceFilterEvaluator` with `IngestionPolicyEvaluator` in DiscordUserConnector (scope: `connector:discord:<identity>`)
- [ ] 8.3 Remove `extract_telethon_filter_key` and `extract_discord_filter_key` imports
- [ ] 8.4 Update connector tests for user-client and Discord

## 9. Switchboard Pipeline Integration

- [ ] 9.1 Replace `TriageRuleCache` with `IngestionPolicyEvaluator(scope='global')` in Switchboard startup
- [ ] 9.2 Replace `_run_triage()` in ingest.py with `evaluator.evaluate(IngestionEnvelope(...))` returning PolicyDecision
- [ ] 9.3 Update request context embedding to use PolicyDecision fields (action, target_butler, matched_rule_id, matched_rule_type)
- [ ] 9.4 Update ingest.py tests

## 10. Remove Old Modules

- [ ] 10.1 Delete `src/butlers/connectors/source_filter.py`
- [ ] 10.2 Delete `roster/switchboard/tools/triage/evaluator.py`
- [ ] 10.3 Delete `roster/switchboard/tools/triage/cache.py`
- [ ] 10.4 Delete `roster/switchboard/tools/triage/telemetry.py`
- [ ] 10.5 Delete `tests/connectors/test_source_filter.py`
- [ ] 10.6 Verify no remaining imports of removed modules

## 11. Frontend — API Layer + Hooks

- [ ] 11.1 Add API client functions: `getIngestionRules`, `createIngestionRule`, `updateIngestionRule`, `deleteIngestionRule`, `testIngestionRule`
- [ ] 11.2 Add TypeScript types: `IngestionRule`, `IngestionRuleCreate`, `IngestionRuleUpdate`, `IngestionRuleTestRequest`
- [ ] 11.3 Add React Query hooks: `useIngestionRules(params)`, `useCreateIngestionRule()`, `useUpdateIngestionRule()`, `useDeleteIngestionRule()`, `useTestIngestionRule()`
- [ ] 11.4 Remove old hooks: `use-source-filters.ts`, `use-triage.ts`
- [ ] 11.5 Remove old API client functions for source filters and triage rules

## 12. Frontend — Unified Filters Tab

- [ ] 12.1 Rewrite FiltersTab to show single unified rules table with columns: Priority, Scope (badge), Condition, Action (badge), Enabled toggle, Actions
- [ ] 12.2 Add scope filter dropdown above table (All / Global / per-connector scopes)
- [ ] 12.3 Update RuleEditorDrawer with scope selector (Global / Connector + type/identity pickers)
- [ ] 12.4 Add scope-aware action constraint in rule editor (connector scope → block only)
- [ ] 12.5 Update import-defaults dialog for unified seed rules
- [ ] 12.6 Preserve thread affinity panel and Gmail label filters panel unchanged
- [ ] 12.7 Remove ManageSourceFiltersPanel component
- [ ] 12.8 Write FiltersTab tests for unified table, scope filtering, and scope-constrained editor

## 13. Frontend — Connector Detail Page

- [ ] 13.1 Replace ConnectorFiltersDialog with inline rules section showing rules filtered by `scope = 'connector:<type>:<identity>'`
- [ ] 13.2 Add "+ Add Rule" button that opens rule editor with pre-filled scope
- [ ] 13.3 Remove ConnectorFiltersDialog component and its test file
- [ ] 13.4 Update ConnectorDetailPage tests

## 14. Cleanup Migration (Deferred — after soak period)

- [ ] 14.1 Create deferred migration to drop `triage_rules`, `source_filters`, and `connector_source_filters` tables
- [ ] 14.2 Verify no code references old table names before running
