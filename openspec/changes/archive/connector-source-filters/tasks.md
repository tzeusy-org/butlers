## 1. Database Migration

- [ ] 1.1 Create `roster/switchboard/migrations/026_create_source_filters.py` with `source_filters` table (id UUID PK, name TEXT UNIQUE NOT NULL, description TEXT, filter_mode TEXT CHECK IN ('blacklist','whitelist'), source_key_type TEXT NOT NULL, patterns TEXT[] NOT NULL DEFAULT '{}', created_at/updated_at TIMESTAMPTZ)
- [ ] 1.2 Add `connector_source_filters` table to same migration (connector_type, endpoint_identity, filter_id UUID FK→source_filters ON DELETE CASCADE, enabled BOOL DEFAULT true, priority INT DEFAULT 0, attached_at TIMESTAMPTZ; PK on (connector_type, endpoint_identity, filter_id))
- [ ] 1.3 Add indexes: `ix_connector_source_filters_connector` on (connector_type, endpoint_identity) WHERE enabled=true; `ix_connector_source_filters_filter_id` on (filter_id)
- [ ] 1.4 Implement `downgrade()` — drops both tables (cascade handles FK references)
- [ ] 1.5 Write migration test confirming upgrade/downgrade round-trip and FK cascade on filter delete

## 2. Shared Filter Evaluation Module

- [ ] 2.1 Create `src/butlers/connectors/source_filter.py` with `SourceFilterSpec` dataclass (id, name, filter_mode, source_key_type, patterns, priority)
- [ ] 2.2 Implement `FilterResult` dataclass (allowed: bool, reason: str, filter_name: str | None)
- [ ] 2.3 Implement `SourceFilterEvaluator.__init__(connector_type, endpoint_identity, db_pool, refresh_interval_s=300)` with asyncpg pool reference and TTL state
- [ ] 2.4 Implement `SourceFilterEvaluator._load_filters()` — async DB query joining `connector_source_filters` and `source_filters` for this connector where enabled=true, ordered by priority ASC; fail-open on DB error
- [ ] 2.5 Implement `SourceFilterEvaluator.ensure_loaded()` — initial load at startup; called before first `evaluate()` call
- [ ] 2.6 Implement TTL refresh: on `evaluate()` call, if `time.monotonic() - last_load_at > refresh_interval_s` trigger background `asyncio.create_task(_load_filters())` and continue with cached set
- [ ] 2.7 Implement `SourceFilterEvaluator.evaluate(key_value: str) -> FilterResult` with composition rules: no filters → pass; blacklists first (any match → block); whitelists next (must match at least one if any active)
- [ ] 2.8 Handle unknown `source_key_type`: skip filter with one-time WARNING log per filter_id
- [ ] 2.9 Register `butlers_connector_source_filter_total` Prometheus Counter with labels [endpoint_identity, action, filter_name, reason]; emit on every `evaluate()` call
- [ ] 2.10 Unit tests: blacklist domain blocks/passes; whitelist allows only matching; mixed composition; empty filter set; unknown key_type skipped; TTL refresh triggers on expiry; fail-open on DB error retains previous cache

## 3. Backend API — Source Filter CRUD

- [ ] 3.1 Add `SourceFilter`, `SourceFilterCreate`, `SourceFilterUpdate`, `ConnectorFilterAssignment`, `ConnectorFilterAssignmentItem` Pydantic models to `roster/switchboard/api/models.py`
- [ ] 3.2 Implement `GET /source-filters` — query all filters ordered by name ASC; return `ApiResponse[list[SourceFilter]]`
- [ ] 3.3 Implement `POST /source-filters` — insert new filter; return HTTP 201 with created record; HTTP 409 on duplicate name; HTTP 422 on invalid filter_mode or empty patterns
- [ ] 3.4 Implement `GET /source-filters/{filter_id}` — return single filter; HTTP 404 on missing
- [ ] 3.5 Implement `PATCH /source-filters/{filter_id}` — partial update of name/description/patterns only (filter_mode and source_key_type are immutable); bump updated_at; HTTP 404 on missing; HTTP 409 on duplicate name
- [ ] 3.6 Implement `DELETE /source-filters/{filter_id}` — delete filter (cascade removes connector_source_filters rows); return HTTP 200 with `{deleted_id}`; HTTP 404 on missing
- [ ] 3.7 Unit tests for all 5 endpoints: happy path, 404, 409 (name conflict), 422 (invalid mode, empty patterns), cascade delete

## 4. Backend API — Connector Filter Assignment

- [ ] 4.1 Implement `GET /connectors/{connector_type}/{endpoint_identity}/filters` — join all source_filters with LEFT JOIN connector_source_filters for this connector; return all filters with enabled/priority from assignment (enabled=false for unattached); flag incompatible source_key_types per connector channel
- [ ] 4.2 Implement PUT `/connectors/{connector_type}/{endpoint_identity}/filters` — within a single transaction: DELETE all existing assignments for this connector, INSERT new assignments from request body; HTTP 422 if any filter_id in payload is unknown; return updated assignment list
- [ ] 4.3 Define valid source_key_type sets per connector_type in a module-level dict (used for `incompatible` flag computation): `gmail` → {domain, sender_address, substring}; `telegram-bot`/`telegram-user-client` → {chat_id}; `discord` → {channel_id}
- [ ] 4.4 Unit tests: GET returns all filters including unattached; PUT replaces atomically; PUT empty list detaches all; PUT unknown filter_id returns 422; incompatible flag set correctly

## 5. Gmail Connector Integration

- [ ] 5.1 Instantiate `SourceFilterEvaluator(connector_type="gmail", endpoint_identity=..., db_pool=<switchboard pool>)` in GmailConnector `__init__` or startup
- [ ] 5.2 Call `await evaluator.ensure_loaded()` before entering the main ingestion loop
- [ ] 5.3 Add `_extract_filter_key(from_header: str, key_type: str) -> str` helper: for `domain` extract lowercased domain; for `sender_address` normalize full address; for `substring` return raw header
- [ ] 5.4 Insert filter gate call after `LabelFilterPolicy.evaluate()` and before triage rule evaluation; drop message and advance checkpoint if `result.allowed is False`
- [ ] 5.5 Update `evaluate_message_policy()` pipeline or surrounding code to reflect new step order (label filter → source filter → triage → policy tier)
- [ ] 5.6 Unit tests: domain blacklist blocks `newsletter.spam.com`, passes `legit.com`; sender_address whitelist allows only exact address; substring blacklist blocks From header containing pattern; filter gate skips unsupported key types

## 6. Telegram Bot Connector Integration

- [ ] 6.1 Instantiate `SourceFilterEvaluator(connector_type="telegram-bot", endpoint_identity=..., db_pool=<switchboard pool>)` in the Telegram bot connector startup
- [ ] 6.2 Call `await evaluator.ensure_loaded()` before the first getUpdates call or webhook setup
- [ ] 6.3 Add `_extract_telegram_filter_key(update) -> str` helper that returns `str(update.message.chat.id)` (handles both private and group chats)
- [ ] 6.4 Insert filter gate call after update normalization and before Switchboard submission; drop blocked updates (advance update_id checkpoint so Telegram does not re-deliver)
- [ ] 6.5 Unit tests: chat_id blacklist blocks specific chat; whitelist allows only listed chats; non-chat_id key types skipped with WARNING

## 7. Frontend — API Hooks

- [ ] 7.1 Add `getSourceFilters()`, `createSourceFilter()`, `updateSourceFilter()`, `deleteSourceFilter()` API functions in `frontend/src/api/index.ts`
- [ ] 7.2 Add `getConnectorFilters()`, `updateConnectorFilters()` API functions in `frontend/src/api/index.ts`
- [ ] 7.3 Add `useSourceFilters`, `useCreateSourceFilter`, `useUpdateSourceFilter`, `useDeleteSourceFilter` hooks in `frontend/src/hooks/use-source-filters.ts`
- [ ] 7.4 Add `useConnectorFilters`, `useUpdateConnectorFilters` hooks in `frontend/src/hooks/use-ingestion.ts`
- [ ] 7.5 Ensure mutation hooks invalidate `useSourceFilters` and `useConnectorFilters` caches on success

## 8. Frontend — ConnectorCard Filter Button

- [ ] 8.1 Add Filters button to `ConnectorCard.tsx` in header action area; use `stopPropagation()` on click to prevent Link navigation
- [ ] 8.2 Show active filter count badge on Filters button when `enabled` count > 0 (fetch via `useConnectorFilters`)
- [ ] 8.3 Wire button click to open `ConnectorFiltersDialog` for the card's connector
- [ ] 8.4 Add Manage Filters button to `ConnectorDetailPage.tsx` header actions
- [ ] 8.5 Unit tests: clicking Filters button does not navigate; badge shows correct count; badge hidden when count is zero

## 9. Frontend — ConnectorFiltersDialog Component

- [ ] 9.1 Create `frontend/src/components/ingestion/ConnectorFiltersDialog.tsx` as a Sheet/Dialog showing all named filters in a table (Enabled checkbox | Name | Mode | Key Type | Patterns count)
- [ ] 9.2 Render enabled checkboxes bound to local state initialized from `useConnectorFilters`; incompatible filters show warning icon and disabled checkbox
- [ ] 9.3 Show Save button (disabled until state changes); clicking Save calls `useUpdateConnectorFilters.mutate()` with full assignment list; show loading state during mutation
- [ ] 9.4 Show toast on save error; close dialog on save success
- [ ] 9.5 Show empty state with "Manage Filters" link when no named filters exist
- [ ] 9.6 "Manage Filters" link/button opens `ManageSourceFiltersPanel`
- [ ] 9.7 Component tests: renders all filters; checkbox toggle enables Save button; Save calls PUT with correct payload; empty state shown when no filters

## 10. Frontend — ManageSourceFiltersPanel Component

- [ ] 10.1 Create `frontend/src/components/ingestion/ManageSourceFiltersPanel.tsx` as a Sheet showing filter list table (Name | Mode | Key Type | Patterns count | Edit / Delete actions)
- [ ] 10.2 Implement "Create filter" form with fields: Name, Description, Mode (radio: Blacklist/Whitelist), Key Type (select: domain/sender_address/substring/chat_id/channel_id), Patterns (tag input); submit calls `useCreateSourceFilter`
- [ ] 10.3 Implement inline Edit form pre-filled with name/description/patterns (mode and key_type read-only); save calls `useUpdateSourceFilter`
- [ ] 10.4 Implement Delete with confirmation dialog ("Delete '{name}'? Removes from all connectors."); confirm calls `useDeleteSourceFilter`
- [ ] 10.5 Show inline validation: duplicate name error under Name field; empty patterns error on submit
- [ ] 10.6 Component tests: create form submits correctly; edit shows read-only mode/key-type; delete confirmation shown before API call; validation errors shown without API call
