## 1. Re-anchor on an owner.clock transition

- [x] 1.1 Pure `plan_reanchor` and `evidence_from_legs`, with the `leg_is_confirmed` rule for confirmation
- [x] 1.2 Compare-and-set `apply_reanchor` and the gated `run_clock_transition`
- [x] 1.3 Real-Postgres test: only owner-local rows move; a lost race is not overwritten
- [x] 1.4 One owner-zone resolver shared by the scheduler tick and the re-anchor; per-schema completeness
- [x] 1.5 Owner card composer (REQ-core-scheduler-007), dark, doors per item; ledger delivery is a later flag-gated step
- [x] 1.6 P6 additive: core_384 columns (no backfill; writers clear the kind on change), CHECKs; one `effective_anchor` predicate; optional kind on `schedule_create`
- [ ] 1.7 Later: required kind, sentinel retirement, ambiguous-row review, TOML defaults, `calendar_events` columns, UTC-pinned job rows
