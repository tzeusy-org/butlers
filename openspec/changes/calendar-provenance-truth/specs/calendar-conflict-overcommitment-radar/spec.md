## ADDED Requirements

### Requirement: Provenance-Aware Conflict Candidate Filter

The conflict radar SHALL continue to render all projected workspace rows, but
before overlap, back-to-back, or overloaded-day detection it SHALL exclude an
event with explicit `metadata.butler_generated=true`. It SHALL also exclude an
all-day event and a legacy locally-midnight-aligned event spanning at least 24
hours in its valid stored IANA timezone. The filter SHALL run before all three
detectors so an excluded row cannot pair, extend a density chain, or contribute
meeting hours.

The filter SHALL use only the explicit metadata marker for generated-event
exclusion. A comparable timed human event without that marker SHALL preserve
the existing radar behavior. Malformed metadata SHALL be treated as no explicit
marker; an invalid or missing timezone SHALL make only the legacy-midnight
inference unavailable. These malformed inputs SHALL not raise and SHALL not
hide a timed event solely through a failed parse.

#### Scenario: Generated row remains visible but produces no radar issue

- **WHEN** a butler-generated projection row overlaps or adjoins a human event
- **THEN** the workspace projection still returns the generated row
- **AND** the radar reports no overlap or back-to-back issue from that pair
- **AND** the generated row contributes no hours to an overloaded-day issue

#### Scenario: Equivalent human rows retain detector behavior

- **WHEN** two timed human projection rows have the same timing as excluded
  generated rows but lack `metadata.butler_generated=true`
- **THEN** overlap, back-to-back, and overloaded-day detection retain their
  existing behavior

#### Scenario: Unmarked BUTLER-prefixed provider event retains detector behavior

- **WHEN** a timed provider projection row has a title beginning `BUTLER:` but
  default or unmarked metadata, and it overlaps, adjoins, or contributes meeting
  hours alongside another timed human row
- **THEN** it remains a radar candidate and overlap, back-to-back, and
  overloaded-day detection retain their existing behavior
- **AND** title, source, and lane do not substitute for explicit generated
  metadata

#### Scenario: Legacy midnight row is excluded from every detector

- **WHEN** a legacy row has `all_day=false`, lasts at least 24 hours, and has
  local-midnight boundaries in its valid IANA timezone
- **THEN** it does not participate in overlap, back-to-back, or overloaded-day
  detection

#### Scenario: Malformed provenance does not hide a timed event

- **WHEN** an otherwise valid timed row has malformed metadata or an invalid
  timezone
- **THEN** the radar does not raise
- **AND** malformed metadata alone does not exclude it as generated
- **AND** an invalid timezone alone does not exclude it as a legacy all-day row

## MODIFIED Requirements

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
strategy, keep-separate pins honored). Provenance-based candidate exclusion
MUST be governed exclusively by the Provenance-Aware Conflict Candidate
Filter; title prefixes, source names, and calendar lanes MUST NOT infer
authorship or exclude a timed row. A dedup-store read failure degrades to the
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
