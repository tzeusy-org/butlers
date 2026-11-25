## 1. Dark ledger (slice E2a)

- [x] 1.1 Migration `rel_048`: `relationship.encounters`, role grant, widened debrief state CHECK.
- [x] 1.2 Reducer `butlers.jobs.encounter_ledger`: precedence, replay, failed read, atomic write.
- [x] 1.3 Real-Postgres tests per state, replay, read failure, upgrade and downgrade.

## 2. Readers (slice E2b, not this change)

- [ ] 2.1 `meeting_debrief_answer` outcomes `did_not_happen` and `moved`.
- [ ] 2.2 Register the reducer job; `calendar_prep`, meeting card and `interaction_sync` read it.
- [ ] 2.3 Retraction of existing mutual rows with a recorded verdict; Dunbar and stale weighting.
