## 1. Counting held candidates (slice E1)

- [x] 1.1 `interaction_sync` reads participants; owner-accepted, non-declined, exact/manual only.
- [x] 1.2 Unresolved participants hold the checkpoint; a failed read counts an error.
- [x] 1.3 Prep "last met" skips reminders, declined and unaccepted events.

## 2. Later (not this change)

- [ ] 2.1 `relationship.encounters` and the reducer; debrief `did_not_happen` and `moved`.
- [ ] 2.2 Retraction of existing mutual rows with a recorded verdict.
- [ ] 2.3 "Last scheduled", series memory, Dunbar and stale-contact weighting (owner decision).
