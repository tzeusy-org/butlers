## ADDED Requirements

### Requirement: Per-Local-Day Clipped Day Load

Day load (the hours that feed `overloaded_day` detection and the day-load read model) SHALL be computed by splitting every counted timed interval at display-local midnights and charging each local day only the clipped portion that falls inside that day. An interval SHALL NOT be charged wholesale to its start date. A multi-day timed item (a four-day hold, an overnight item) therefore contributes to each local day it intersects only its overlap with that day.

The display timezone is the requested `timezone` (the owner's fleet timezone when omitted). Local-day boundaries SHALL be the actual local midnights in that zone, so on a DST transition the day is 23 or 25 hours and the invariant is `0 <= day_load <= that local day's actual duration`, never a blind 24 hours. Intervals are clipped before aggregation; the sum across days of one interval SHALL equal the interval's own duration. Clipping SHALL apply after the shared candidate filter, so the existing provenance, all-day, legacy-midnight, RSVP, transparency, duplicate-collapse and partial-fan-out contracts are unchanged. The shared `calendar_provenance.is_calendar_analysis_candidate()` filter SHALL be reused and not duplicated.

ID: REQ-calendar-conflict-overcommitment-radar-010
Source: bu-h40h2b.6 acceptance 2 and 4; bu-vpn5ij; conflicts.py currently charges a span to its start date
Scope: v1-mandatory

#### Scenario: A four-day timed hold is clipped to each local day

- **WHEN** a timed item runs from 09:00 on day 1 to 17:00 on day 4 in the display timezone
- **THEN** day 1 is charged 15 hours, days 2 and 3 are charged 24 hours each, and day 4 is charged 17 hours
- **AND** no day is charged 106 hours
- **AND** the four charges sum to the item's duration

#### Scenario: A DST day bounds the invariant at its actual length

- **WHEN** an item covers a whole local day on which clocks go forward (23 hours) or back (25 hours)
- **THEN** that day is charged 23 or 25 hours respectively
- **AND** no day's charge exceeds its actual duration

#### Scenario: Excluded rows stay excluded after clipping

- **WHEN** a row is excluded by the shared candidate filter (all-day, generated, legacy midnight, declined, transparent)
- **THEN** it is charged to no day

#### Scenario: Genuine empty result is unchanged

- **WHEN** a healthy scan finds no counted timed interval on a day
- **THEN** the day reports zero load with complete coverage

### Requirement: Category-Honest Day Density

Only an interval whose category is `meeting` SHALL count toward meeting density. `hold`, `travel` and `all_day` intervals SHALL NOT inflate meeting density, and an `uncategorized` interval SHALL be reported separately and SHALL NOT be presented as a meeting. The category SHALL come only from the calendar module's structured category evidence; the radar SHALL NOT infer a category from titles, sources or lanes. An interval in a `hold` or `travel` category remains visible in the workspace and still participates in overlap detection unless excluded by the existing filters.

Each day's load SHALL be reported as `meeting_minutes` (a lower bound: evidenced meetings only) and `uncategorized_minutes` (timed intervals the evidence does not classify). An `overloaded_day` issue with `severity: "warning"` SHALL be emitted only when `meeting_minutes` exceeds the budget. When `meeting_minutes` is within the budget but `meeting_minutes + uncategorized_minutes` exceeds it, the radar SHALL emit a possible-overload `overloaded_day` issue with `severity: "info"` whose summary states the uncategorized share, never a warning that asserts meetings.

ID: REQ-calendar-conflict-overcommitment-radar-011
Source: bu-h40h2b.6 acceptance 4; bu-vpn5ij; decision B1 (bu-2o1dn8)
Scope: v1-mandatory

#### Scenario: Holds and travel do not inflate meeting density

- **WHEN** a day holds 3 hours of `meeting`, a 5-hour `hold` and a 4-hour `travel` interval
- **THEN** `meeting_minutes` is 180
- **AND** no `overloaded_day` warning is emitted for a 6-hour budget

#### Scenario: Uncategorized time is a separate, honest bucket

- **WHEN** a day holds 4 hours of `meeting` and 3 hours of `uncategorized` timed intervals
- **THEN** `meeting_minutes` is 240 and `uncategorized_minutes` is 180
- **AND** only an `info` possible-overload issue is emitted, naming the uncategorized share

#### Scenario: No category is guessed from a title

- **WHEN** a timed event is titled "Flight to Singapore" with no structured evidence
- **THEN** it is `uncategorized` and is never excluded or counted as travel by title

### Requirement: Day-Load Read Model With Explicit Coverage

`ConflictScanResponse` SHALL additively carry a `day_load` list with one entry per local day in the scan window: `date` (YYYY-MM-DD in the display timezone), `meeting_minutes`, `uncategorized_minutes`, `hold_minutes`, `travel_minutes`, `day_minutes` (the local day's actual duration), and `coverage` of `complete`, `partial`, `stale` or `unavailable`. Existing fields, `issues_available` semantics and the fail-open HTTP contract SHALL be unchanged.

`coverage` SHALL be `unavailable` for every day when the events fan-out fails entirely, `partial` for every day when a partial per-schema failure occurred or when the day lies beyond a contributing source's `coverage_through`, and `stale` when a contributing source is stale. A day with `coverage` other than `complete` SHALL NOT be presented as quiet or free; consumers SHALL render the coverage state instead of a zero load. The radar remains a pure read: no provider call and no LLM call at request time.

ID: REQ-calendar-conflict-overcommitment-radar-012
Source: bu-h40h2b.6 acceptance 4 and 5; bu-vpn5ij
Scope: v1-mandatory

#### Scenario: Total fan-out failure is unavailable, not zero

- **WHEN** no schema responds
- **THEN** HTTP 200 with `issues: []`, `issues_available: false` and every `day_load.coverage = "unavailable"`

#### Scenario: Partial fan-out failure is partial, not complete

- **WHEN** one targeted schema errors
- **THEN** HTTP 200 and every day reports `coverage = "partial"`

#### Scenario: Day beyond source coverage is partial

- **WHEN** a day lies after a contributing source's `coverage_through`
- **THEN** that day reports `coverage = "partial"` even though no row exists for it

#### Scenario: Healthy empty day is complete

- **WHEN** all sources are fresh and a day has no counted interval
- **THEN** that day reports zero minutes with `coverage = "complete"`

#### Scenario: Additive change keeps existing consumers working

- **WHEN** a consumer ignores `day_load`
- **THEN** `issues`, `scan_window` and `issues_available` behave exactly as before
