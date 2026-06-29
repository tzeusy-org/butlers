# Chronicler API — Spec delta for chronicler-intent-evidence-activity

## MODIFIED Requirements

### Requirement: Chronicler Aggregations

`GET /api/chronicler/aggregate/by-category` SHALL count the `activity` layer only
and roll up into the Activity lane taxonomy (`sleep`, `exercise`, `work`,
`play`, `social`, `travel`, `eat`, `rest`). `intent` and `evidence` layers are
excluded. No "calendar" lane is returned; calendar time appears only via the
activity lane it corroborates. `by-day` follows the same counting rule.

#### Scenario: By-category excludes intent and evidence layers

- **WHEN** a window contains a 5-hour uncorroborated calendar block plus inferred
  activities
- **THEN** the calendar block contributes 0 seconds to every lane
- **AND** the returned buckets use Activity lane names only

#### Scenario: Lane buckets carry confidence breakdown

- **WHEN** by-category is requested
- **THEN** each lane bucket reports how much of its time is low-confidence
- **AND** lanes are returned sorted by total time

## ADDED Requirements

### Requirement: Daily Balance Endpoint

A read endpoint SHALL return the day's per-lane balance annotated against the
owner's rolling baseline ("vs usual").

#### Scenario: Balance returns deltas vs usual

- **WHEN** a client requests the daily balance for a date
- **THEN** each lane returns the day's total and a signed delta vs the owner's
  rolling baseline
- **AND** a lane with no activity returns zero with its baseline for context

### Requirement: Trends Endpoint

A read endpoint SHALL return week- and month-grained balance trends, streaks, and
anomalies derived from the chronicler's own synthesized baselines.

#### Scenario: Week trends return per-lane series

- **WHEN** a client requests trends for a week window
- **THEN** a per-lane time series is returned
- **AND** notable streaks/anomalies (e.g. consecutive work days) are reported

### Requirement: Who-You-Were-With Endpoint

A read endpoint SHALL return the resolved people the owner spent time with in a
window, with co-present time and channel, resolving identity via
`relationship.entity_facts`.

#### Scenario: Returns resolved companions for a day

- **WHEN** a client requests who-you-were-with for a date
- **THEN** each entry names a resolved entity, the co-present duration, and the
  channel (in-person vs a comms channel)
- **AND** unresolved participants are returned as unattributed rather than
  dropped

### Requirement: Activity Evidence Chain Endpoint

A read endpoint SHALL return the evidence chain for an activity — each
corroborating signal with its source — so a client can answer "why?".

#### Scenario: Evidence chain returned for an activity

- **WHEN** a client requests the evidence chain for an activity id
- **THEN** the response lists each `evidence_ref` with its source name and a
  human-readable descriptor
- **AND** the activity's confidence is included

### Requirement: Low-Confidence Correction Prompts

A read endpoint SHALL return the day's low-confidence activities as correction
prompts the owner can confirm or relabel, reusing the existing corrections
overlay for writes.

#### Scenario: Low-confidence blocks surfaced as prompts

- **WHEN** a client requests correction prompts for a date
- **THEN** low-confidence activities are returned with their best-guess lane and
  evidence
- **AND** confirming or relabeling writes a non-destructive correction overlay

## Source References

- `chronicler-api/spec.md` §5.5 (Chronicler Aggregations), §5.3 (Chronicler
  Corrections), §5.8 (Episode Participant Resolution Read Path).
- `butler-chronicler/spec.md` §4.15 (Calendar Scheduled Blocks Are Not
  Attendance Assertions).
- RFC 0014 (Chronicler Time Butler).
