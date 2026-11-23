## Why

The nightly `memory_purge_superseded` job hard-deleted every superseded fact older than the seeded
7-day `kind='fact'` horizon, ignored `retention_class`, fell back to 7 days when the policy could
not be read, and broke successors' `supersedes_id` chains. A superseded fact is a closed validity
interval, the evidence for "what was true on that day". Owner decision run24-keep-history-default
= A (2026-11-22, bead bu-uhbhzq.3): superseded facts are kept forever for automatic expiry;
owner-initiated forget is unchanged.

## What Changes

- Add the requirement "Superseded facts are retained as closed validity intervals" to
  `memory-retention-policy`. The purge fails closed, honours the per-class floor and treats a
  NULL horizon as keep-forever. The existing "Memory maintenance jobs are module-default"
  requirement is not modified, because an open change also edits it.
- A core data migration clears only the seeded 7-day horizon.
- A storage helper reads facts as of a timestamp.

## Impact

Code: `purge_superseded_facts`, `purge_ha_state_facts`, `_run_memory_purge_superseded_job`,
`facts_as_of`, core_358. Rows already deleted are not restored.
