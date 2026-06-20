# Tasks — calendar-mutation-audit-and-undo

Two backend features over existing infrastructure: an audit-read endpoint
(bu-r71dbf) and an undo endpoint (bu-ytu9l4) plus its pre-state-capture
prerequisite. No DB migration, no new MCP tool. The `calendar_action_log` table
and the `core_076` provenance columns already exist; `action_result` is `JSONB`.

## 1. Pre-mutation state capture (undo prerequisite)

- [ ] 1.1 In `calendar_update_event`, include the pre-mutation `existing_event`
  state (title, start/end, timezone, location, description, attendees,
  recurrence_rule, resolved calendar id) in the finalized `action_result` under a
  stable key (e.g. `pre_state`), alongside the existing post-mutation outcome —
  reuse the already-fetched `existing_event`, no extra provider round-trip
- [ ] 1.2 In `calendar_delete_event`, fetch the event pre-image before delete and
  include it in the finalized `action_result` under the same `pre_state` key
- [ ] 1.3 Confirm `_record_projection_action` / `_finalize_workspace_mutation`
  persist the enriched `action_result` unchanged into the existing `JSONB` column
  (no schema change, no new column)
- [ ] 1.4 Confirm the idempotent-replay path (`_load_projection_action`,
  `_prepare_workspace_mutation`) is unaffected — `idempotency_key`, status
  transitions, and `idempotent_replay=true` replay are preserved
- [ ] 1.5 Unit tests: update logs `pre_state`; delete logs `pre_state`;
  create/noop/failed do not require `pre_state`

## 2. Audit-trail read endpoint (bu-r71dbf)

- [ ] 2.1 Add a versioned read-model query that fans out `calendar_action_log`
  rows joined to `calendar_events` (`source_butler`, `source_session_id` via
  `event_id`) across `butlers_with_module("calendar")`, ordered `created_at DESC`,
  bounded by `limit` (and `cursor`), consistent with `query_calendar_workspace`
- [ ] 2.2 Add `CalendarAuditEntry` and the audit list response models to
  `src/butlers/api/models/calendar_workspace.py` (`action_id`, `action_type`,
  `action_status`, `request_id`, payload summary, `error`, `created_at`,
  `applied_at`, `source_butler`, `source_session_id`)
- [ ] 2.3 Add `GET /api/calendar/workspace/audit` to
  `src/butlers/api/routers/calendar_workspace.py` returning the audit feed in an
  `ApiResponse` envelope; perform no provider/projection write
- [ ] 2.4 Surface `source_butler` / `source_session_id` on `UnifiedCalendarEntry`
  (sourced from the existing `core_076` columns) for the Activity-tab deep-link
- [ ] 2.5 Fail-open on an absent `calendar_action_log` table / empty log →
  HTTP 200 with an empty list

## 3. Undo endpoint (bu-ytu9l4)

- [ ] 3.1 Add `POST /api/calendar/workspace/undo/{action_id}` to
  `src/butlers/api/routers/calendar_workspace.py`; load the targeted
  `calendar_action_log` row from the owning butler
- [ ] 3.2 Synthesize the inverse: `workspace_user_update` → inverse
  `calendar_update_event` restoring `pre_state`; `workspace_user_delete` → inverse
  `calendar_create_event` from `pre_state`; `workspace_user_create` → inverse
  `calendar_delete_event` against the created event id
- [ ] 3.3 Dispatch the inverse through the existing calendar MCP tools with a
  freshly generated `request_id` (so the undo is itself idempotent and audited);
  do not add a new MCP tool
- [ ] 3.4 Fail fast (HTTP 409) when the row's status is `pending`/`failed`/`noop`
  (only an `applied` mutation is undoable); dispatch nothing
- [ ] 3.5 Fail fast (HTTP 422) with diagnostic detail (action_id, action_type,
  reason) when the row is `applied` but `pre_state` is missing/expired; dispatch
  nothing
- [ ] 3.6 Return HTTP 404 for an unknown `action_id`; reject repeated undo of an
  already-undone action (HTTP 409)
- [ ] 3.7 Unit tests: undo update / delete / create happy paths; non-applied 409;
  missing-pre-state 422; unknown-id 404; already-undone 409; assert undo dispatch
  carries a fresh `request_id` and is recorded in the audit log

## 4. Spec + inventory + gate

- [ ] 4.1 Add the `GET /api/calendar/workspace/audit` and
  `POST /api/calendar/workspace/undo/{action_id}` rows to the dashboard-api
  endpoint inventory (under `#### Calendar Workspace`)
- [ ] 4.2 Run `openspec validate calendar-mutation-audit-and-undo --strict`
- [ ] 4.3 Quality gate: `ruff check`/`format --check` on touched files, then the
  targeted calendar + calendar-workspace test suites, then full `pytest`
  (excluding e2e) before merge
