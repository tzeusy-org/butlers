## MODIFIED Requirements

### Requirement: Forward-Window Conflict Scan Endpoint
The capability SHALL expose `GET /api/calendar/workspace/conflicts` that
accepts `start`, `end`, optional `timezone`, and optional `butler_name`
parameters and returns a `ConflictScanResponse`. The endpoint MUST be
deterministic and read-only — it queries the synced `calendar_events` /
`calendar_event_instances` tables using the existing `GIST(tstzrange)` index
and SHALL make no provider API call and no LLM call at request time.
- The endpoint MUST reject windows where `end <= start` or `end - start > 90 days`
with HTTP 400. It MUST be fail-open: any DB query failure SHALL return HTTP 200
with `issues: []` and `issues_available: false`; HTTP 500 MUST NOT be returned.
- Before detection runs, the endpoint MUST collapse cross-source duplicate rows
with the SAME dedup pass the workspace grid read applies (persisted match
strategy, keep-separate pins honored). Provenance-based candidate exclusion
MUST be governed exclusively by the Provenance-Aware Conflict Candidate
Filter; title prefixes, source names, and calendar lanes MUST NOT infer
authorship or exclude a timed row. A dedup-store read failure degrades to the
default strategy with no overrides (fail-open) rather than failing the scan.
- For overloaded-day meeting-hours only, explicit `outOfOffice` and `workingLocation` events SHALL contribute zero duration. Other eligible types, including future unknown types, retain existing meeting-hours behavior. This exclusion SHALL NOT change overlap/back-to-back eligibility, free/busy, conflict-write policy, dedup, partial-availability, or workspace visibility.

ID: REQ-calendar-conflict-overcommitment-radar-001
Source: RFC 0009; RFC 0020; vision.md shared situational awareness; approved bu-s11n0s.4 Outcome
Scope: v1-mandatory

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


#### Scenario: Status-only events add zero meeting-hours
- **WHEN** OOO and working-location rows coexist with an eligible attended opaque meeting
- **THEN** the overloaded-day total counts the meeting and zero hours from either status-only row
- **AND** the workspace still exposes the status rows


#### Scenario: OOO still blocks a real scheduling conflict
- **WHEN** an eligible opaque OOO overlaps an eligible opaque ordinary event
- **THEN** existing overlap detection still reports the conflict
- **AND** the OOO duration does not add meeting-hours to day load
