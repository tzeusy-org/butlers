# calendar-conflict-overcommitment-radar Specification

## Purpose

The conflict and overcommitment radar scans the forward calendar window at read
time to detect scheduling problems — overlapping events, back-to-back density,
and overloaded days — and surfaces them in the calendar workspace as a radar
banner and amber-edged grid entries. The scan is a pure read: it runs off the
projected `calendar_event_instances` store, makes no provider API call and no LLM
call at request time, and fails open (degraded mode is silent). Detected issues
carry any `pending` fix proposals from the shared `calendar_event_proposals`
store so the UI can offer one-tap fixes, rendering an empty/informational state
until a proposal producer runs.

## Requirements
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

Before detection runs, the endpoint MUST collapse cross-source duplicate rows
with the SAME dedup pass the workspace grid read applies (persisted match
strategy, keep-separate pins honored) and MUST exclude butler-authored shadow
copies of the owner's own events from pairing — see the dedup and
butler-shadow-copy scenarios below. A dedup-store read failure degrades to the
default strategy with no overrides (fail-open) rather than failing the scan.

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

- **WHEN** the entire events fan-out fails during the scan (no schema responded)
- **THEN** HTTP 200 with `issues: []` and `issues_available: false`
- **AND** no HTTP 500 is returned

#### Scenario: Partial fan-out failure (degraded mode)

- **WHEN** at least one targeted butler schema's events fan-out query fails but
  one or more other schemas respond successfully
- **THEN** HTTP 200 with `issues_available: false`
- **AND** `issues` reflects only the conflicts detectable among the schemas that
  DID respond (it MAY be non-empty)
- **BECAUSE** the failed schema's events were silently dropped, so a real overlap
  could be hidden — the scan MUST NOT report a fabricated "all clear". A
  non-empty `issues` list with `issues_available: false` therefore means "these
  are real, but the set is incomplete", and the FE hides the banner (silent
  degraded mode) exactly as for a total failure.

#### Scenario: Cross-source duplicate cluster does not produce phantom overlaps

- **GIVEN** the same real-world provider event is synced into the workspace as
  N rows sharing one `origin_ref` — cross-butler-schema copies of one Google
  Calendar event
- **WHEN** `GET /api/calendar/workspace/conflicts` scans the window
- **THEN** the scan collapses the cluster with the same cross-source dedup
  pass the workspace grid read applies (persisted match strategy and
  keep-separate pins honored) BEFORE running overlap/back-to-back/overloaded-
  day detection
- **AND** the collapsed cluster yields ZERO `overlap` issues and no
  duplicate-hour double-counting in any `overloaded_day` issue
- **BECAUSE** scanning the raw, un-collapsed fan-out pairs every member of an
  N-row cluster combinatorially, fabricating N-choose-2 phantom overlaps for
  a slot the grid renders as a single entry — the radar's word must match
  exactly what the owner can see and act on

#### Scenario: Time-drifted re-sync of one event collapses to the fresher row

- **GIVEN** two workspace rows share one non-recurring `origin_ref` but sit at
  different start instants — a time-drifted re-sync of the same provider event
  (e.g. one window 8h off the corrected one)
- **WHEN** the workspace grid read or the conflict scan collapses the window
- **THEN** the `origin_ref` dedup pass keys on `origin_ref` ALONE (not
  `(origin_ref, start)`) so the two rows collapse to a single entry
- **AND** the surviving row is the most-recently-synced copy (highest
  `instance_updated_at`), with keyset order breaking ties deterministically
- **AND** a recurring event's occurrences (many rows legitimately sharing one
  `origin_ref` at different starts) and rows with no `origin_ref` are NOT
  collapsed by this pass — they retain the `(origin_ref, start)` key
- **BECAUSE** a re-sync that drifts an event's time leaves a stale prior copy in
  the ledger; keying identity on `origin_ref` alone lets the read converge to
  the copy that reflects the provider's current truth instead of rendering the
  same event twice

#### Scenario: Butler-authored shadow copy excluded from overlap pairing

- **GIVEN** a butler-authored event whose title carries the
  `BUTLER: ` prefix and whose stripped title case-insensitively matches
  another (non-butler-titled) row in the same window — a butler-projected
  copy of the owner's own event, not caught by the dedup pass above because
  its title and `origin_ref` genuinely differ from the row it shadows
- **WHEN** the conflict scan builds its candidate set
- **THEN** the butler-titled row is excluded from overlap/back-to-back/
  overloaded-day pairing
- **AND** a genuine overlap between two DIFFERENT butler-authored events
  (neither shadows a same-titled non-butler row) is still detected normally

### Requirement: [TARGET-STATE] ConflictScanResponse Model

The response envelope MUST conform to the following schema, with all fields
present. `issues_available` SHALL be `false` in degraded mode — which includes
BOTH a total fan-out failure AND a partial per-schema failure (any targeted
schema erroring). `issues` SHALL be an empty list when no problems exist in a
healthy scan, MAY be non-empty on a partial degraded scan (the conflicts found
among the responding schemas), and is empty on a total failure.

```
ConflictScanResponse {
  issues: ConflictIssue[]        # detected issues; empty on total-fail, may be partial+incomplete on partial-fail
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

