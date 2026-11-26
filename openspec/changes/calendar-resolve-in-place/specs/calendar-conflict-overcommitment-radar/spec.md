## MODIFIED Requirements

### Requirement: ConflictScanResponse Model

The response envelope MUST conform to the following schema, with all fields
present. `issues_available` SHALL be `false` in degraded mode, which includes
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
  options: ResolutionOption[]    # overlap issues only; see calendar-conflict-resolution
  issue_digest: str | null       # overlap issues only; binds a resolve to what was read
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

`proposal_ids` is removed: nothing produces the create-only proposals it referenced, and the
resolution options replace the Fix card.

ID: REQ-calendar-conflict-overcommitment-radar-005
Source: bu-5ii4gj.11 slice R1; design "Resolve, not Fix"
Scope: v1-mandatory

#### Scenario: Overlap issues carry options and a digest

- **WHEN** `GET /api/calendar/workspace/conflicts` is called for a window containing an overlap
- **THEN** the overlap issue SHALL include `options` and an `issue_digest`, and no `proposal_ids`

#### Scenario: Response includes proposal_ids for pending proposals

- **GIVEN** a `pending` row in `calendar_event_proposals` whose `source_event_id`
  equals the canonical overlap-pair id for two events in the window
- **WHEN** `GET /api/calendar/workspace/conflicts` is called for a window
  containing those events
- **THEN** the matching `ConflictIssue` SHALL NOT carry any proposal reference: `proposal_ids` no longer exists and options replace it

#### Scenario: Proposal stays attached when a resync rotates the surviving copy

- **GIVEN** an overlap whose options and digest were read
- **WHEN** a resync makes another butler schema's copy of one event survive the dedup, changing that event's `entry_id`
- **THEN** the issue SHALL keep the same provider-identity pair id and its options and digest SHALL be recomputed from the surviving copy

### Requirement: FE Radar Banner

The week/day view SHALL fetch `GET /api/calendar/workspace/conflicts` for the
visible window and MUST render a radar banner above the calendar grid when
`issues_available: true` and `issues` is non-empty.

The banner MUST:
- Show a condensed one-liner summarising issues by day (e.g.
  "Tue has 2 overlaps · Wed has 8.5h of meetings").
- Expand to per-issue cards on click; each overlap card shows the contributing
  event titles and its resolution options as keyboard-addressable verbs (1-9).
- Replace the session-local dismiss with a persisted "keep both" decision that
  writes a receipt and clears the issue until either event changes.
- Not render at all when `issues_available: false`; degraded mode SHALL be silent.

ID: REQ-calendar-conflict-overcommitment-radar-006
Source: bu-5ii4gj.11 slice R3; design "Resolve, not Fix"
Scope: v1-mandatory

#### Scenario: Banner rendered with overlap issue

- **GIVEN** the visible week contains a Tuesday with two overlapping events
- **WHEN** the FE fetches `GET /api/calendar/workspace/conflicts?start=...&end=...`
  and the response contains an `overlap` issue dated Tuesday
- **THEN** a radar banner appears above the grid: "Tue has 2 overlaps"
- **AND** expanding the banner shows the two event titles

#### Scenario: No banner in degraded mode

- **WHEN** the conflicts endpoint returns `issues_available: false`
- **THEN** no radar banner is rendered (silent degraded mode)

#### Scenario: Fix card with proposal

- **GIVEN** an overlap issue with resolution options
- **WHEN** the card is expanded
- **THEN** it lists the options, and pressing a number key runs that option once and renders its receipt, with no Fix action backed by the proposals accept and dismiss surface

#### Scenario: Fix card without proposal (LLM session not yet run)

- **GIVEN** an overlap issue whose options are empty because a source is stale
- **WHEN** the card is shown
- **THEN** it is informational only, and states that the options are unavailable because a source is stale
