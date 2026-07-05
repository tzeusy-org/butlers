# Proactive Insight Engine — Hourly Urgent Sub-Cycle

## ADDED Requirements

### Requirement: Hourly Urgent Sub-Cycle
`delivery_cycle()` SHALL accept an `urgent_only` mode used by a dedicated hourly schedule (distinct from the existing daily schedule), so a candidate at or above `URGENT_PRIORITY_THRESHOLD` (90) is delivered within the hour rather than waiting for the next daily cycle.

In this mode:
- candidate selection is narrowed to `priority >= URGENT_PRIORITY_THRESHOLD`
  from the start — routine (sub-threshold) candidates are never selected,
  filtered, deduplicated, or otherwise touched by this cycle;
- the quiet-hours/context-bus consult is skipped outright (not merely
  bypassed after being computed) — an urgent candidate is never suppressed by
  either, so querying them is unnecessary;
- the daily adaptive budget cap does not apply — every eligible urgent
  candidate is delivered (or folded into one digest) this cycle, not just the
  top-B;
- end-of-cycle maintenance (`cleanup_old_rows`, disengagement auto-off) is
  skipped — these are daily-cadence concerns the regular cycle already
  covers once a day.

An explicit `verbosity=off` configuration SHALL still suppress delivery in
`urgent_only` mode exactly as it does in the regular cycle — this is a hard
user opt-out, not a time-based deferral the urgent bypass is meant to
override.

#### Scenario: Urgent candidates delivered hourly, routine candidates untouched
- **WHEN** the hourly urgent sub-cycle runs with one candidate at
  `priority=95` and another at `priority=70` both pending
- **THEN** the `priority=95` candidate is delivered
- **AND** the `priority=70` candidate's status remains `'pending'`,
  untouched — it is neither delivered, filtered, nor deduplicated by this
  cycle

#### Scenario: No daily budget cap in urgent_only mode
- **WHEN** the hourly urgent sub-cycle runs with 3 eligible urgent candidates
  pending and the configured daily verbosity budget is 1
- **THEN** all 3 are delivered this cycle, composed into one digest message
  (not capped to 1 by the daily budget)

#### Scenario: Quiet hours and the context bus are bypassed without being queried
- **WHEN** the hourly urgent sub-cycle runs during active quiet hours with an
  eligible urgent candidate pending
- **THEN** the candidate is delivered
- **AND** the context-bus consult is never invoked (the urgent_only path
  narrows to urgent candidates first and skips both suppression checks
  entirely, rather than computing and then ignoring them)

#### Scenario: verbosity=off still suppresses urgent candidates
- **WHEN** `insight_settings.verbosity = 'off'`
- **AND** the hourly urgent sub-cycle runs with an urgent candidate pending
- **THEN** the cycle is skipped and the candidate is marked `filtered`,
  exactly as the regular daily cycle already behaves under `verbosity=off`

#### Scenario: A candidate the urgent sub-cycle already delivered is never re-sent
- **WHEN** the hourly urgent sub-cycle delivers a `priority=95` candidate
- **AND** the next daily cycle runs afterward
- **THEN** the daily cycle's `pending`-status fetch does not include that
  candidate — it was already transitioned to `status='delivered'` by the
  urgent sub-cycle, which is the same row-status guard against double-send
  the daily cycle already relies on for its own deliveries
