## Why

Every calendar workspace mutation (`calendar_create_event`,
`calendar_update_event`, `calendar_delete_event`, and the butler-event family)
already writes a durable row into the `calendar_action_log` table via
`_record_projection_action` (the `CREATE TABLE calendar_action_log` from
migration `core_003`): `idempotency_key`, `request_id`, `action_type` (e.g.
`"workspace_user_update"`), `action_status` (`pending`/`applied`/`failed`/`noop`),
`action_payload`, `action_result`, `error`, and timestamps. **There is no read
endpoint** — the table is write-only today, consumed only by the idempotent-replay
path (`_load_projection_action`). The agent's own writes are therefore invisible
to the owner: a butler can move or delete an event and the workspace shows the
effect with no trace of who did it, when, or why.

Worse, calendar writes are framed as irreversible — the approval gate
(`_gate_high_impact_mutation`) exists precisely because "calendar writes can't be
undone." But the information needed to reverse a mutation is *almost* already
captured: on update, the handler fetches the pre-mutation `existing_event` from
the provider before patching (`calendar.py` ~3127), and on every finalize it
records `action_result`. Today `action_result` stores only the **post-mutation**
outcome, so an inverse cannot be reconstructed from the log.

This change makes the agent's calendar writes **self-explaining and reversible**:
surface the audit trail, capture the pre-mutation state so an inverse is
reconstructable, and add a one-call undo that synthesizes the inverse mutation
through the existing, audited calendar tools. It is the safety counterweight that
makes the write-heavy roadmap directions (drag/resize, quick-add, proposals)
safe. No LLM, no migration.

## What Changes

- **New audit-read endpoint.** Add `GET /api/calendar/workspace/audit` to the
  calendar workspace router (`src/butlers/api/routers/calendar_workspace.py`),
  reading the existing `calendar_action_log` rows across calendar-owning butlers.
  It returns each logged action with its `action_status`
  (`applied`/`pending`/`failed`/`noop`), `action_type`, a payload summary,
  `request_id`, `error`, and timestamps, ordered most-recent-first with bounded
  pagination. It surfaces the existing `source_butler` / `source_session_id`
  provenance columns (added in migration `core_076`, joined from
  `calendar_events` via `event_id`) so each row deep-links into the originating
  session log. **No DB change.**
- **Pre-mutation state capture.** Extend the recorded `action_result` so that for
  reversible mutations it includes the **pre-mutation event state** (the
  pre-image fetched before the write — title, start/end, timezone, location,
  description, attendees, recurrence, calendar id) alongside the existing
  post-state, under a stable key so an inverse is reconstructable. This reuses the
  existing `existing_event` fetch on update and adds the equivalent pre-image
  fetch on delete. **No schema change** — `action_result` is already `JSONB`.
- **New undo endpoint.** Add `POST /api/calendar/workspace/undo/{action_id}` that
  reads a logged `calendar_action_log` row, synthesizes the inverse mutation from
  its `action_payload` + captured pre-state, and dispatches it through the
  existing calendar MCP tools (`calendar_create_event` to undo a delete,
  `calendar_update_event` to undo an update/move, `calendar_delete_event` to undo
  a create) with a **fresh `request_id`** so the undo is itself idempotent and
  audited. Undo of an action that was never applied, was already undone, or whose
  captured pre-state is missing/expired **fails fast** with diagnostic context
  rather than guessing.

## Capabilities

### New Capabilities

_None — this adds two read/dispatch HTTP endpoints to the existing dashboard
calendar workspace API and one provenance-capture behavior to the existing
calendar module action log._

### Modified Capabilities

- `dashboard-api`: the Calendar Workspace HTTP surface gains a mutation
  audit-trail read endpoint and an undo endpoint; both reuse the existing
  `calendar_action_log` and the existing calendar MCP tools.
- `module-calendar`: the calendar action log captures pre-mutation event state in
  `action_result` so a logged mutation can be inverted; the captured pre-image is
  the contract the undo endpoint reverse-applies.

## Impact

- **Calendar workspace router (`src/butlers/api/routers/calendar_workspace.py`):**
  new `GET /api/calendar/workspace/audit` handler and new
  `POST /api/calendar/workspace/undo/{action_id}` handler; new request/response
  Pydantic models in `src/butlers/api/models/calendar_workspace.py`.
- **Calendar module (`src/butlers/modules/calendar.py`):**
  `_finalize_workspace_mutation` / the create/update/delete handlers capture the
  pre-mutation event pre-image into `action_result` under a stable key (reusing
  the existing `existing_event` fetch on update; adding the pre-image fetch on
  delete). No change to `_record_projection_action`'s schema or the
  idempotent-replay path.
- **Read-model boundary (`src/butlers/api/read_models/`):** a versioned query that
  fans out `calendar_action_log` rows joined to `calendar_events`
  (`source_butler` / `source_session_id`) across `butlers_with_module("calendar")`,
  consistent with the existing `query_calendar_workspace` boundary.
- **`UnifiedCalendarEntry`:** surfaces `source_butler` / `source_session_id` (the
  existing `core_076` columns) so an Activity tab can deep-link an entry to its
  originating session.
- **No new MCP tool, no DB schema change, no migration.** `calendar_action_log`
  and the `core_076` provenance columns already exist; `action_result` is `JSONB`.
- **Frontend:** an Activity tab over the audit feed and an "Undo" affordance /
  toast. (FE work is out of scope for the contract here.)

## Out of Scope

- A new MCP tool for undo — undo dispatches through the existing
  `calendar_create_event` / `calendar_update_event` / `calendar_delete_event`
  tools; no tool is added (the spec's "16 MCP tools total" pin is unchanged).
- Any database migration or schema change — pre-state is stored in the existing
  `action_result` JSONB column; provenance reads the existing `core_076` columns.
- Undo of butler-lane workspace events whose pre-state is not captured — v1 undo
  covers user-lane create/update/delete mutations whose pre-image is logged;
  butler-event undo can follow once their handlers capture pre-state.
- Multi-step undo history / redo — v1 undoes a single logged action by id.
- An LLM in the audit or undo path — both are deterministic over logged rows.
- Recurrence-scope changes — recurrence updates stay series-scoped in v1; undo
  reverse-applies the same series-scoped fields that were logged.
