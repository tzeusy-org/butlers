## Why

PR #4348 ingests Google Health workouts as `workout_session` facts, but nothing answers the
owner's plain question "when did I last run?". Agents otherwise have to page through facts and
guess at activity wording and time zones.

## What Changes

- Add one read-only Health tool, `workout_last(activity?)`, that returns the most recent matching
  recorded workout with its start in the owner's configured time zone, duration, source, and fact id.
- Match activity names through a small documented alias table; no match is an explicit
  "no workout recorded" result.

## Non-goals

- No new ingestion, streak or trend analytics, or dashboard change.

## Impact

- `roster/health/tools/workouts.py`, tool registration beside `measurement_latest`, and the
  tool-presentation inventory.
