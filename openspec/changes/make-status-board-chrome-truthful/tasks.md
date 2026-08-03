## 1. Focused regression coverage

- [x] 1.1 Add RED API coverage for canonical cadence labels, including a
  two-hour noncanonical interval.
- [x] 1.2 Add RED frontend coverage for activity-derived unknown health
  arithmetic and for initial-error versus cached-refresh chrome composition.

## 2. Truthful status-board implementation

- [x] 2.1 Make cadence labels exact canonical intervals and expose all other
  positive intervals as `custom` without changing cadence-overdue behavior.
- [x] 2.2 Expose an activity-derived unknown aggregate and make header health
  exclude offline, quarantined, overdue, and unknown rows.
- [x] 2.3 Gate Page header/footer slots only on the existing no-cache
  full-page-error state, preserving cached-refresh chrome and normal empty
  behavior.

## 3. Verification and handoff

- [x] 3.1 Run strict OpenSpec validation, focused Python/Vitest suites, and
  proportionate frontend lint/build checks; review the scoped diff.
- [ ] 3.2 Commit the scoped change, push the dedicated branch, and open a
  draft PR for exact-head independent review without requesting a merge.
