## 1. Owner sign-off (gate)

- [ ] 1.1 Confirm the design: deterministic SQL scan endpoint, `calendar_scan_conflicts`
  MCP tool, LLM fix-proposal session emitting via `calendar_propose_event`, no new
  migration required, amber edge derived client-side from `entry_id` matching.
- [ ] 1.2 Confirm sequencing: both prerequisites are merged
  (`calendar-availability-find-time` PR #2640, `calendar-event-proposals` all
  sub-PRs); the radar uses `get_free_busy`, `calendar_find_free_slots`, and
  `calendar_propose_event` which are already in `main`.

## 2. Backend: `query_calendar_conflicts` read-model function

- [ ] 2.1 Add `query_calendar_conflicts(db, start, end, butlers, back_to_back_gap_minutes,
  overloaded_day_hours)` to `src/butlers/api/read_models/calendar_workspace_v1.py`.
  Fan-out across all active butler schemas via the single-pool pattern. Three
  sub-queries:
  - **Overlap**: self-join on `calendar_events` / `calendar_event_instances` using
    `tstzrange(starts_at, ends_at, '[)') && tstzrange(...)` against the existing
    GIST index.
  - **Back-to-back**: window function ordering events by `starts_at` per day; flag
    pairs where `LEAD(starts_at) - ends_at < interval`.
  - **Overloaded day**: `SUM` of event durations per calendar day.
- [ ] 2.2 Return `list[ConflictIssueRow]` (new dataclass); include `proposal_ids`
  by joining to `calendar_event_proposals` on the canonical overlap-pair
  `source_event_id` where `status='pending'`.
- [ ] 2.3 Unit tests: overlap detected; back-to-back detected; overloaded day
  detected; no false positives on gap >= threshold; degraded mode returns empty
  list, no exception.

## 3. Backend: `GET /api/calendar/workspace/conflicts` endpoint

- [ ] 3.1 Add `ConflictIssue`, `ConflictEventRef`, `ConflictScanResponse` Pydantic
  models to `src/butlers/api/models/calendar_workspace.py`.
- [ ] 3.2 Add `GET /api/calendar/workspace/conflicts` to
  `src/butlers/api/routers/calendar_workspace.py`:
  - Parameters: `start`, `end` (datetime), optional `timezone`, `butler_name`.
  - Validates: `end > start`, window ≤ 90 days (HTTP 400 otherwise).
  - Calls `query_calendar_conflicts`; wraps in `ConflictScanResponse`.
  - Fail-open: any exception → HTTP 200, `issues=[]`, `issues_available=false`.
- [ ] 3.3 Tests: endpoint returns overlap issue; back-to-back issue; overloaded
  day issue; empty on clean window; degraded response on DB failure; 400 on invalid
  window.

## 4. Backend: `calendar_scan_conflicts` MCP tool

- [ ] 4.1 Implement `calendar_scan_conflicts(start_at, end_at, back_to_back_gap_minutes=15,
  overloaded_day_hours=6.0)` in `src/butlers/modules/calendar.py`. Calls the
  same `query_calendar_conflicts` logic (or a shared helper). Fail-open on DB
  error.
- [ ] 4.2 Register the tool in the Calendar module's `register_tools`. Update the
  tool-count literal in `openspec/specs/module-calendar/spec.md` from "22 MCP
  tools total" to "23 MCP tools total".
- [ ] 4.3 Unit tests: tool returns issues; fail-open on DB error; no provider call
  is made.

## 5. Backend: LLM fix-proposal session

- [ ] 5.1 Add a scheduled butler job (cron, default every 6 h) that:
  a. Calls `calendar_scan_conflicts` for the forward window (default 7 days).
  b. If zero `warning`-severity issues: exits with no proposals.
  c. Otherwise: spawns a low-med-tier ephemeral session with the conflict-radar
     skill prompt.
- [ ] 5.2 The session skill prompt instructs the LLM to:
  - For each `overlap`: call `calendar_find_free_slots`, then
    `calendar_propose_event` (with canonical overlap-pair `source_event_id`).
  - For each `back_to_back` cluster: propose a buffer block.
  - For each `overloaded_day`: propose declining/rescheduling the lowest-priority
    event.
  - Emit at most one proposal per issue.
- [ ] 5.3 Idempotency: derive `source_event_id` for proposals from a deterministic
  UUID5 of the issue pair (e.g. namespace + sorted entry_ids) so re-runs never
  duplicate proposals.
- [ ] 5.4 Integration test: session with one overlap issue emits exactly one pending
  proposal; re-running with same issue emits no duplicate; session with only `info`
  issues emits no proposals.

## 6. Frontend: radar banner

- [ ] 6.1 Add `useConflictScan(start, end)` hook in
  `frontend/src/pages/CalendarWorkspacePage.tsx` that fetches
  `GET /api/calendar/workspace/conflicts` for the visible window on mount and on
  window change.
- [ ] 6.2 Render `<ConflictRadarBanner>` above the grid when `issues_available &&
  issues.length > 0`. Banner text: one-liner summarising issues by day. Dismiss
  hides it for the session.
- [ ] 6.3 `<ConflictIssueCard>`: one card per issue; shows contributing event titles;
  when `proposal_ids` is non-empty, shows a fix card with Accept / Decline backed
  by the existing `POST /proposals/{id}/accept` and `/dismiss` endpoints.
- [ ] 6.4 E2E test: a mock conflicts response with one overlap issue renders the
  banner; clicking Accept on the fix card calls the accept endpoint.

## 7. Frontend: amber edge on overlapping event blocks

- [ ] 7.1 After the conflicts response is fetched, build a `Set<string>` of
  `entry_id`s that appear in any `overlap` issue.
- [ ] 7.2 Pass the set to the grid event component; apply a `conflict-edge` CSS class
  when the event's `entry_id` is in the set.
- [ ] 7.3 Visual test (Storybook or snapshot): event block with `conflict-edge`
  renders amber left border; non-conflicting block is unaffected.

## 8. Spec validation and quality gate

- [ ] 8.1 Run `openspec validate calendar-conflict-overcommitment-radar --strict`
  and fix until green.
- [ ] 8.2 Run `ruff check src/ tests/ --output-format concise` and
  `ruff format --check src/ tests/` on all touched files.
- [ ] 8.3 Run the targeted test suite:
  ```bash
  uv run pytest tests/test_calendar_workspace*.py tests/test_calendar_module*.py \
    -q --tb=short
  ```
- [ ] 8.4 Full `pytest tests/ --ignore=tests/e2e -q --maxfail=1 --tb=short` before
  merge.
- [ ] 8.5 FE: `npm run build` + `eslint .` + `vitest run` in `frontend/`.
