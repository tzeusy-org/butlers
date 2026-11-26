## 1. Dark ledger (slice E2a)

- [x] 1.1 Migration `rel_048`: `relationship.encounters`, role grant, widened debrief state CHECK.
- [x] 1.2 Reducer `butlers.jobs.encounter_ledger`: precedence, replay, failed read, atomic write.
- [x] 1.3 Real-Postgres tests per state, replay, read failure, upgrade and downgrade.

## 2. Readers (slice E2b)

- [x] 2.1 Register the reducer job (hourly); `calendar_prep`, meeting card and prep rail read it.
- [ ] 2.2 `meeting_debrief_answer` outcomes `did_not_happen` and `moved` (owner wording pending).
- [ ] 2.3 `interaction_sync` retraction of existing mutual rows with a recorded verdict; Dunbar and
  stale-contact weighting (owner decisions).
