## 1. Switchboard Registration

- [ ] 1.1 Add `google_calendar` to `SourceChannel` and `SourceProvider` literals in `roster/switchboard/tools/routing/contracts.py`
- [ ] 1.2 Add `google_calendar`/`google_calendar` to `_ALLOWED_PROVIDERS_BY_CHANNEL` validation
- [ ] 1.3 Add `"google_calendar": "realtime"` to `HISTORY_STRATEGY` in `src/butlers/modules/pipeline.py`
- [ ] 1.4 Update ingest.v1 envelope Pydantic model to accept `google_calendar` channel/provider pair

## 2. Core Connector Implementation

- [ ] 2.1 Create `src/butlers/connectors/google_calendar.py` with base connector scaffolding (imports, config dataclass, env var loading, main entry point)
- [ ] 2.2 Implement multi-account discovery from `shared.google_accounts` (query for active accounts with `calendar` in `granted_scopes`)
- [ ] 2.3 Implement per-account OAuth credential resolution (reuse Gmail connector's credential resolution pattern)
- [ ] 2.4 Implement Google Calendar API client with token refresh and rate-limit retry (429/503 with exponential backoff)

## 3. Sync and Ingestion Loop

- [ ] 3.1 Implement initial full sync (no syncToken) to establish baseline — persist `nextSyncToken` via `cursor_store`, skip event ingestion
- [ ] 3.2 Implement incremental sync poll loop using `events.list(syncToken=...)` with pagination support
- [ ] 3.3 Implement expired syncToken handling (410 Gone fallback to full sync with event ingestion)
- [ ] 3.4 Implement event change classification (created/updated/deleted based on event status)
- [ ] 3.5 Implement `ingest.v1` envelope normalization (`normalized_text` format, field mapping, idempotency key)
- [ ] 3.6 Implement checkpoint-after-acceptance cursor advancement

## 4. Starting Soon Notifications

- [ ] 4.1 Implement upcoming event window scan after each sync cycle
- [ ] 4.2 Implement in-memory seen-set with `(event_id, lead_minutes)` key for dedup
- [ ] 4.3 Implement `event_starting_soon` envelope with `interactive` policy tier
- [ ] 4.4 Implement seen-set pruning (remove entries for past events)
- [ ] 4.5 Implement restart recovery (emit overdue notifications for not-yet-started events)

## 5. Multi-Account and Lifecycle

- [ ] 5.1 Implement per-account asyncio poll loop spawning with error isolation
- [ ] 5.2 Implement dynamic account discovery (periodic re-scan at `GCAL_ACCOUNT_RESCAN_INTERVAL_S`)
- [ ] 5.3 Implement graceful loop shutdown on account removal (complete in-flight, checkpoint, stop)
- [ ] 5.4 Implement per-account configuration via `metadata.calendar` overrides

## 6. Connector Base Contract

- [ ] 6.1 Implement `IngestionPolicyEvaluator` integration with `scope = 'connector:google_calendar:<endpoint_identity>'`
- [ ] 6.2 Implement filtered event batch flush to `connectors.filtered_events`
- [ ] 6.3 Implement replay queue drain loop
- [ ] 6.4 Implement heartbeat protocol (connector.heartbeat.v1 envelope, periodic send)
- [ ] 6.5 Implement Prometheus metrics (`connector_ingest_submissions_total`, `connector_source_api_calls_total`, `connector_checkpoint_saves_total`, `connector_errors_total`)
- [ ] 6.6 Implement health/metrics HTTP server (`/health`, `/metrics` endpoints)
- [ ] 6.7 Implement aggregated health status (worst-case across account loops)

## 7. Infrastructure

- [ ] 7.1 Add google-calendar-connector service to `docker-compose.yml`
- [ ] 7.2 Add connector entry point to `pyproject.toml` (console script or module)

## 8. Tests

- [ ] 8.1 Unit tests for ingest.v1 envelope normalization (all event types: created, updated, deleted, starting_soon)
- [ ] 8.2 Unit tests for syncToken cursor lifecycle (initial full sync, incremental, expired token recovery)
- [ ] 8.3 Unit tests for starting-soon notification logic (dedup, pruning, restart recovery)
- [ ] 8.4 Unit tests for multi-account discovery and scope validation
- [ ] 8.5 Unit tests for source filter integration
- [ ] 8.6 Integration test for end-to-end poll cycle (mock Google API, verify Switchboard submission)
