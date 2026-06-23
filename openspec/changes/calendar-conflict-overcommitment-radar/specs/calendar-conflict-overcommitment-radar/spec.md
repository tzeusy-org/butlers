# Calendar Conflict & Overcommitment Radar

## Purpose

The conflict radar proactively scans a forward calendar window for scheduling
problems — event overlaps, back-to-back meeting density, and overloaded days —
and proposes one-click fixes via the existing proposals lane. The user sees a
quiet banner in the week/day view and confirms or declines each proposed fix;
**nothing is ever silently written to the real calendar**.

## ADDED Requirements

### Requirement: [TARGET-STATE] Forward-Window Conflict Scan Endpoint

The capability SHALL expose `GET /api/calendar/workspace/conflicts` that
accepts `start`, `end`, optional `timezone`, and optional `butler_name`
parameters and returns a `ConflictScanResponse`. The endpoint MUST be
deterministic and read-only — it queries the synced `calendar_events` /
`calendar_event_instances` tables using the existing `GIST(tstzrange)` index
and SHALL make no provider API call and no LLM call at request time.

The endpoint MUST reject windows where `end <= start` or `end - start > 90 days`
with HTTP 400. It MUST be fail-open: any DB query failure SHALL return HTTP 200
with `issues: []` and `issues_available: false`; HTTP 500 MUST NOT be returned.

#### Scenario: Overlap detected in window

- **WHEN** two confirmed or tentative events in the window share overlapping
  time ranges (`tstzrange(a.starts_at, a.ends_at, '[)') &&
  tstzrange(b.starts_at, b.ends_at, '[)')`) and belong to active sources
- **THEN** the endpoint returns a `ConflictIssue` with:
  - `kind: "overlap"`
  - `date`: the calendar date (in the display timezone) of the earlier event
  - `summary`: a human-readable one-liner (e.g. "Design review and 1:1 overlap by 30 min")
  - `severity: "warning"`
  - `events`: the two overlapping `ConflictEventRef` objects
  - `proposal_ids`: UUIDs of any `pending` proposals in `calendar_event_proposals`
    whose `source_event_id` matches the canonical overlap-pair id (deterministic
    UUID5 of the sorted `entry_id` pair)
- **AND** `issues_available: true`

#### Scenario: Back-to-back density detected

- **WHEN** two consecutive non-cancelled events in the same calendar day are
  separated by fewer than `back_to_back_gap_minutes` (default 15) minutes
- **THEN** a `ConflictIssue` of `kind: "back_to_back"` is returned covering the
  cluster of consecutive events with no adequate gap
- **AND** `severity: "info"` when exactly two events are adjacent; `"warning"` when
  three or more form an unbroken chain

#### Scenario: Overloaded day detected

- **WHEN** the total confirmed/tentative meeting time on a calendar day exceeds
  `overloaded_day_hours` (default 6.0 hours)
- **THEN** a `ConflictIssue` of `kind: "overloaded_day"` is returned with
  `severity: "warning"` and the total meeting-hours in `summary`

#### Scenario: No issues in window

- **WHEN** no overlaps, back-to-back chains, or overloaded days exist in the window
- **THEN** HTTP 200 with `issues: []` and `issues_available: true`

#### Scenario: DB unreachable (degraded mode)

- **WHEN** any fan-out query fails during the scan
- **THEN** HTTP 200 with `issues: []` and `issues_available: false`
- **AND** no HTTP 500 is returned

### Requirement: [TARGET-STATE] ConflictScanResponse Model

The response envelope MUST conform to the following schema, with all fields
present. `issues_available` SHALL be `false` only in degraded mode; `issues`
SHALL be an empty list when no problems exist in a healthy scan.

```
ConflictScanResponse {
  issues: ConflictIssue[]        # list of detected issues, empty on degraded
  scan_window: { start, end }    # the requested window (ISO-8601)
  issues_available: bool         # false on degraded; FE hides banner when false
}

ConflictIssue {
  kind: "overlap" | "back_to_back" | "overloaded_day"
  date: str                      # YYYY-MM-DD in display timezone
  summary: str                   # human-readable one-liner
  severity: "info" | "warning"
  events: ConflictEventRef[]     # events contributing to the issue
  proposal_ids: str[]            # UUIDs of pending fix proposals (empty list when none)
}

ConflictEventRef {
  entry_id: str                  # workspace entry id
  title: str
  start_at: str                  # ISO-8601 with timezone offset
  end_at: str
  timezone: str
  status: str                    # "confirmed" | "tentative"
}
```

`proposal_ids` MUST reference only `pending` rows in `calendar_event_proposals`;
accepted or dismissed proposals MUST NOT appear in this list.

#### Scenario: Response includes proposal_ids for pending proposals

- **GIVEN** a `pending` row in `calendar_event_proposals` whose `source_event_id`
  equals the canonical overlap-pair id for two events in the window
- **WHEN** `GET /api/calendar/workspace/conflicts` is called for a window
  containing those events
- **THEN** the matching `ConflictIssue` includes that proposal's UUID in `proposal_ids`
- **AND** the proposal UUID is NOT included if its status is `accepted` or `dismissed`

### Requirement: [TARGET-STATE] calendar_scan_conflicts MCP Tool

The calendar module MUST register `calendar_scan_conflicts(start_at, end_at,
back_to_back_gap_minutes=15, overloaded_day_hours=6.0)` as an MCP tool. It
SHALL return a structured list of `ConflictIssue` objects (same shape as the API
response). The tool MUST NOT call the provider API — it SHALL query only the
synced DB tables. On any DB error it SHALL return
`{"issues": [], "issues_available": false}` (fail-open). The LLM fix-proposal
session MUST call this tool to read the issues list before emitting proposals.

#### Scenario: LLM session calls `calendar_scan_conflicts`

- **WHEN** the fix-proposal session calls
  `calendar_scan_conflicts(start_at=..., end_at=...)`
- **THEN** it receives a structured list of issues within the window
- **AND** the tool makes no provider API call and no LLM call

#### Scenario: Tool is fail-open on DB error

- **WHEN** the DB query inside `calendar_scan_conflicts` fails
- **THEN** the tool returns `{"issues": [], "issues_available": false}`
- **AND** no exception is raised to the calling session

### Requirement: [TARGET-STATE] LLM Fix-Proposal Session

A butler scheduler job SHALL run a conflict-radar session over the configured
forward window (default 7 days ahead) at the configured cadence (default every
6 hours). The session MUST fire only when `calendar_scan_conflicts` returns at
least one `warning`-severity issue; it MUST exit without emitting proposals when
only `info`-severity issues exist.

The session MUST:
1. Call `calendar_scan_conflicts` to read the issue list.
2. For each `overlap` issue: call `calendar_find_free_slots` to locate an
   alternative slot, then call `calendar_propose_event` for the lower-priority or
   tentative event, using the canonical overlap-pair id as `source_event_id`
   (deterministic UUID5 of the sorted `entry_id` pair) for idempotency.
3. For each `back_to_back` cluster: propose a 15-minute buffer block between the
   densest adjacent pair.
4. For each `overloaded_day`: propose declining or rescheduling the event with the
   lowest priority signal (tentative preference, shortest attendee list) on that day.
5. Emit at most one proposal per issue.

The session MUST NEVER write directly to the user's real Google Calendar. All fix
proposals SHALL go through `calendar_propose_event` → `pending` → human
confirm/decline via the existing proposals accept/dismiss endpoints.

#### Scenario: Proposal emitted for overlap issue

- **WHEN** the session detects an overlap between a `confirmed` and a `tentative` event
- **THEN** it proposes declining the tentative event via `calendar_propose_event`
  with `source_event_id` derived from the canonical overlap-pair id
- **AND** the proposal row has `status: "pending"` in `calendar_event_proposals`
- **AND** the proposal is idempotent: re-running the session does not create a
  second proposal for the same overlap pair

#### Scenario: Session skipped when no warning issues

- **WHEN** `calendar_scan_conflicts` returns zero `"warning"`-severity issues
- **THEN** the session exits without emitting any proposals
- **AND** no `calendar_propose_event` calls are made

#### Scenario: Session finds no free slot for reschedule

- **WHEN** `calendar_find_free_slots` returns no available slots for an overlap fix
- **THEN** no proposal is emitted for that issue (rather than proposing an
  impossible reschedule)

### Requirement: [TARGET-STATE] FE Radar Banner

The week/day view SHALL fetch `GET /api/calendar/workspace/conflicts` for the
visible window and MUST render a radar banner above the calendar grid when
`issues_available: true` and `issues` is non-empty.

The banner MUST:
- Show a condensed one-liner summarising issues by day (e.g.
  "Tue has 2 overlaps · Wed has 8.5h of meetings").
- Expand to per-issue cards on click; each card shows contributing event titles
  and, when `proposal_ids` is non-empty, a fix action backed by the existing
  proposals accept/dismiss surface.
- Include a dismiss control that hides the banner for the current browser session
  (client-side only; not persisted server-side; reappears on next page load).
- Not render at all when `issues_available: false`; degraded mode SHALL be silent.

#### Scenario: Banner rendered with overlap issue

- **GIVEN** the visible week contains a Tuesday with two overlapping events
- **WHEN** the FE fetches `GET /api/calendar/workspace/conflicts?start=...&end=...`
  and the response contains an `overlap` issue dated Tuesday
- **THEN** a radar banner appears above the grid: "Tue has 2 overlaps"
- **AND** expanding the banner shows the two event titles

#### Scenario: Fix card with proposal

- **GIVEN** the overlap issue has a non-empty `proposal_ids` list
- **WHEN** the fix card is expanded
- **THEN** it shows a "Fix" action backed by `POST /proposals/{id}/accept` and
  `POST /proposals/{id}/dismiss` (the existing proposals surface)

#### Scenario: Fix card without proposal (LLM session not yet run)

- **GIVEN** `proposal_ids` is empty for an issue (session not yet run)
- **WHEN** the fix card is shown
- **THEN** the card is informational only (issue and events shown, no action button)

#### Scenario: No banner in degraded mode

- **WHEN** the conflicts endpoint returns `issues_available: false`
- **THEN** no radar banner is rendered (silent degraded mode)

### Requirement: [TARGET-STATE] Amber Edge on Overlapping Grid Entries

The FE MUST render each grid event block whose `entry_id` appears in any
`overlap` issue's `events` list with a thin amber left border. The implementation
SHALL derive the amber-edge entry set client-side from the conflicts response,
keyed by `entry_id`, and MUST NOT add a new field to `UnifiedCalendarEntry` for
this signal. The workspace read path and the `UnifiedCalendarEntry` model MUST
NOT be changed for this feature.

#### Scenario: Amber edge on overlapping event block

- **GIVEN** a grid event block whose `entry_id` appears in a conflict issue's
  `events` list
- **WHEN** the conflicts response is available to the FE
- **THEN** the grid block receives a CSS class (`conflict-edge` or equivalent)
  rendering a thin amber left border
- **AND** non-conflicting event blocks are unaffected

#### Scenario: No amber edge when conflicts unavailable

- **WHEN** the conflicts endpoint returns `issues_available: false`
- **THEN** no event blocks receive the amber-edge style
