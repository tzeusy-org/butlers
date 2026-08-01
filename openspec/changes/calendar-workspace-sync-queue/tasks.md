## 1. Regression Coverage and Schema Lifecycle

- [x] 1.1 Add read-model and Calendar Workspace endpoint regressions for stale non-core duplicate selection, canonical global-owner batching, and the prompt queued acknowledgement.
- [x] 1.2 Add CalendarModule unit coverage for durable queue acknowledgement, serialized drain, coalesced incremental/full requests, terminal outcome recording, and restart recovery.
- [x] 1.3 Add an idempotent core migration that permits `calendar_action_log.running`, indexes one pending force-sync command per schema, and safely downgrades interrupted running commands.

## 2. Durable CalendarModule Force-Sync Queue

- [x] 2.1 Extract the existing inline force-sync implementation into a shared execution helper while retaining direct default behavior.
- [x] 2.2 Implement action-log command enqueue/coalescing and atomic pending-to-running claim helpers.
- [x] 2.3 Start a queue drainer independently of the optional normal sync poller; recover interrupted work on startup and preserve it on shutdown cancellation.
- [x] 2.4 Extend `calendar_force_sync` with queued acknowledgement arguments and result correlation while preserving inline callers.

## 3. Canonical Dashboard Sync Dispatch

- [x] 3.1 Add deterministic router-level canonical provider-source selection that preserves raw read-model fan-out rows.
- [x] 3.2 Apply canonical selection to workspace freshness, meta connected/writable sources, and global sync grouping.
- [x] 3.3 Dispatch `queue=true` MCP commands once per selected owner, return HTTP 202 queued targets, and retain honest per-target failures.
- [x] 3.4 Extend API/frontend sync models and UI messaging for request correlation, coalescing, and queued versus completed/recovery language.

## 4. Documentation and Verification

- [x] 4.1 Update the frontend backend-API contract for canonical ownership, HTTP 202 acknowledgement, and action/freshness completion telemetry.
- [x] 4.2 Run focused migration/schema, module, API/read-model, frontend, lint, and OpenSpec validation checks.
- [ ] 4.3 Review the scoped diff, commit it on the isolated branch, push it, and open a protected pull request without changing `main`.
