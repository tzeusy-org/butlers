> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Canonical-owner selection + durable queued sync shipped via the calendar workspace sync-queue change.
> **Successor:** `openspec/changes/calendar-workspace-sync-queue`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Calendar Workspace Sync Dispatch Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Calendar Workspace select a sync-capable canonical owner for duplicate provider-source ledger rows and make manual sync an honest, durable queued operation.

**Architecture:** The read model remains a truthful fan-out of every physical `calendar_sources` row. The dashboard router selects one canonical operational row per duplicate provider `source_key`, preferring enabled/core-capable/fresh ownership with deterministic ties. Global Sync now groups those rows by owner and asks each owner’s CalendarModule to persist a queued pull-all command. Each module drains its own durable action-log queue serially, recovers interrupted running commands at startup, and returns terminal status through existing action/freshness telemetry.

**Tech Stack:** Python 3.13, FastAPI, FastMCP, asyncio, asyncpg, Alembic, React/TypeScript, pytest, Vitest, OpenSpec Markdown.

## Global Constraints

- Work only in `agent/bu-rey88`; preserve the root checkout and its unrelated `.gitignore` change.
- Do not delete, migrate, or rewrite `calendar_sources` / `calendar_sync_cursors` rows. The fan-out ledger remains intact.
- Preserve direct `calendar_force_sync` inline behavior unless `queue=true` is explicitly requested by the dashboard.
- Do not extend the generic 15-second browser timeout. Long provider work must leave the request path.
- Keep all provider I/O in the owning CalendarModule. The dashboard may only perform cross-owner MCP calls.
- Use an Alembic migration for action-log lifecycle/index changes; no raw live DDL.
- No new dependency, provider configuration, credential change, or external provider call is in scope.

---

### Task 1: Pin ownership, queue, and schema behavior with red tests

**Files:**

- Modify: `tests/api/test_calendar_workspace.py`
- Modify: `tests/api/test_read_models_calendar_workspace_v1.py`
- Modify/Test: existing CalendarModule queue/force-sync test file(s)
- Modify/Test: migration/schema regression location as established by the project

**Interfaces:**

- Consumes: raw cross-schema source fan-out, `calendar_force_sync`, and schema-local `calendar_action_log`.
- Produces: failing proof that a stale non-core duplicate cannot win, global sync is acknowledged quickly once per canonical owner, and queued work survives lifecycle boundaries.

- [ ] **Step 1: Preserve raw fan-out at the read-model boundary**

Add a passing read-model test that duplicate provider rows retain their distinct `db_butler`, cursor, and freshness provenance. Do not deduplicate inside v1.

Run: `uv run pytest tests/api/test_read_models_calendar_workspace_v1.py -q`

- [ ] **Step 2: Add stale-first workspace/meta regressions**

Fixture order: Finance (no cursor/non-core), Relationship (stale/core), General (fresh/core), all sharing a source key. Assert workspace freshness and meta connected/writable data select General.

Run: `uv run pytest tests/api/test_calendar_workspace.py -k 'fresh_core_owner' -q`

Expected before the fix: FAIL due to first-seen selection.

- [ ] **Step 3: Add queued global-sync acknowledgement regression**

Assert `POST /api/calendar/workspace/sync` with `all=true` returns `202`, one queued General target, passes `queue=true`, omits `calendar_id` for owner-wide pull-all, and does not call Finance/Relationship duplicate owners.

Run: `uv run pytest tests/api/test_calendar_workspace.py -k 'canonical.*owner' -q`

Expected before the fix: FAIL due to synchronous per-schema/calendar fan-out.

- [ ] **Step 4: Add CalendarModule queue lifecycle regressions**

Prove queued acknowledgement performs no provider I/O, drains exactly one command at a time, coalesces redundant incremental requests, preserves an incoming full recovery request, records terminal action state, and requeues interrupted running work on startup.

Run: focused CalendarModule queue tests.

Expected before the fix: FAIL because no durable command queue exists.

### Task 2: Add the action-log command lifecycle

**Files:**

- Add: `alembic/versions/core/core_194_calendar_force_sync_queue.py`
- Modify: `src/butlers/api/models/calendar_workspace.py`
- Modify/Test: migration/schema test coverage

**Interfaces:**

- Consumes: schema-local `calendar_action_log` (`pending|applied|failed|noop`).
- Produces: an additive `running` state and a partial uniqueness rule for one pending force-sync successor per owner.

- [ ] **Step 1: Write an idempotent forward migration**

Safely replace the status check to allow `running` and create a partial index restricting `action_type='calendar_force_sync' AND action_status='pending'` to one row in each schema. Existing rows and old runtime code remain valid.

- [ ] **Step 2: Write a reversible downgrade**

Map interrupted `running` force-sync commands to `pending`, drop the partial index, and restore the old status check without deleting action history.

- [ ] **Step 3: Extend action-status models**

Allow `running` in audit/API models so the operator surface names active command work rather than validation-failing.

Run: targeted migration/schema tests.

### Task 3: Implement the durable CalendarModule queue

**Files:**

- Modify: `src/butlers/modules/calendar.py`
- Test: CalendarModule queue tests

**Interfaces:**

- Consumes: `calendar_force_sync(calendar_id?, full?)`, `calendar_action_log`, and existing provider sync/mirror helpers.
- Produces: `calendar_force_sync(queue=true, request_id=...) -> queued|coalesced acknowledgement`, with a module-owned drainer.

- [ ] **Step 1: Extract one inline execution helper**

Factor today’s `calendar_force_sync` provider work into a shared method, retaining byte-for-byte equivalent direct inline tool behavior when `queue=false`.

- [ ] **Step 2: Enqueue and coalesce durably**

Insert a pending action before acknowledgement; detect pending/running compatible commands, upgrade a pending request to full recovery when needed, or add one pending full successor behind a running incremental command.

- [ ] **Step 3: Drain and recover commands**

Start a queue worker independently of `sync.enabled`, atomically claim pending rows as `running`, invoke the shared execution helper serially, and finalize `applied|failed`. On startup and graceful cancellation, requeue interrupted running commands.

- [ ] **Step 4: Preserve observability**

Return request/action correlation and coalescing state; record provider result/error in the action log and keep cursor freshness/error handling unchanged.

Run: focused CalendarModule queue suite.

### Task 4: Canonicalize dashboard ownership and queue dispatch

**Files:**

- Modify: `src/butlers/api/routers/calendar_workspace.py`
- Modify: `src/butlers/api/models/calendar_workspace.py`
- Test: `tests/api/test_calendar_workspace.py`

**Interfaces:**

- Consumes: raw `query_calendar_sources()` mappings with `db_butler`, tool-group config, and sync timestamps.
- Produces: canonical aggregate source lists and HTTP `202` per-owner queued acknowledgements.

- [ ] **Step 1: Implement pure canonical selection**

Rank duplicate provider rows by enabled state, core capability, latest successful/sync timestamp, then schema/id. Keep raw rows untouched and leave explicit source-id targeting exact.

- [ ] **Step 2: Apply selection consistently**

Use the helper for workspace source freshness, meta connected sources/writable calendars, and global sync candidates so the UI and action target agree.

- [ ] **Step 3: Group global work by owner**

For `all=true`, call each canonical owner once with `queue=true`, a correlation id, `full`, and no calendar id. Preserve source-specific calls with their calendar id.

- [ ] **Step 4: Return honest acknowledgement**

Set endpoint status to `202`, return queued/coalesced per-target data, count accepted commands, and retain per-target failed MCP acknowledgements without claiming recovery completed.

Run: `uv run pytest tests/api/test_calendar_workspace.py -q`.

### Task 5: Update the user surface and contracts

**Files:**

- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/pages/CalendarWorkspacePage.tsx`
- Modify/Test: `frontend/src/pages/CalendarWorkspacePage.test.tsx`
- Modify: `docs/frontend/backend-api-contract.md`
- Modify: `openspec/changes/calendar-workspace-sync-queue/`

- [ ] **Step 1: Carry queued response fields through TypeScript**

Model request correlation/coalescing and allow the UI to distinguish queued acknowledgement from a terminal tool result.

- [ ] **Step 2: Correct button/toast language**

Say “queued” for Sync now/Recover acknowledgement; never say that a full recovery ran until terminal telemetry confirms it. Keep immediate known failures visible.

- [ ] **Step 3: Document and verify the contract**

Document canonical ownership, 202 queue acknowledgement, and action/freshness completion telemetry. Mark OpenSpec tasks as implemented only after matching code/tests exist.

Run: `cd frontend && npm test -- --run src/pages/CalendarWorkspacePage.test.tsx`.

### Task 6: Verify and prepare a protected PR

- [ ] **Step 1: Run focused gates**

```bash
uv run ruff check src/butlers/api/routers/calendar_workspace.py src/butlers/api/models/calendar_workspace.py src/butlers/modules/calendar.py tests/api/test_calendar_workspace.py tests/api/test_read_models_calendar_workspace_v1.py
uv run pytest tests/api/test_calendar_workspace.py tests/api/test_read_models_calendar_workspace_v1.py <calendar-queue-tests> -q
cd frontend && npm test -- --run src/pages/CalendarWorkspacePage.test.tsx
openspec validate calendar-workspace-sync-queue --strict
```

- [ ] **Step 2: Diff review**

Run `git diff --check`, inspect source/API/migration/docs diffs, and confirm no ledger cleanup, credentials, or provider calls were introduced.

- [ ] **Step 3: Protected delivery**

Use the repository PR-only workflow: commit on `agent/bu-rey88`, push the branch, create a clean PR, and report exact validation evidence. Do not merge or push to `main`.
