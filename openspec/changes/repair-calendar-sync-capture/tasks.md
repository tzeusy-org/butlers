## 1. Sync repair (bu-uhbhzq.1 S1)

- [x] 1.1 Drop `orderBy`; share one filter set between the full and incremental passes.
- [x] 1.2 A missing sync token is degraded health with no "saved" log and no immediate repeat.
- [x] 1.3 Regression tests for token persistence, param parity and honest absence.

## 2. Revision journal (bu-uhbhzq.1 S2)

- [x] 2.1 core_366 `connectors.calendar_event_revisions`: append-only triggers, writer and Chronicler grants, init-db revokes.
- [x] 2.2 `record_calendar_revisions` as the commit point in the baseline, incremental and 410 passes.
- [x] 2.3 Verbatim title, location and attendees; scrubbed descriptions; policy-blocked rows keep no raw.
- [ ] 2.4 Chronicler `plan_changed` adapter (slice 3).
