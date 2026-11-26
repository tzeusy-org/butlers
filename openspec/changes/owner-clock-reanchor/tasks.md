## 1. Re-anchor on an owner.clock transition

- [x] 1.1 Pure `plan_reanchor` and `evidence_from_legs`, with the `leg_is_confirmed` rule for confirmation
- [x] 1.2 Compare-and-set `apply_reanchor` and the gated `run_clock_transition`
- [x] 1.3 Real-Postgres test: only owner-local rows move; a lost race is not overwritten
- [x] 1.4 One owner-zone resolver shared by the scheduler tick and the re-anchor; per-schema completeness
- [ ] 1.5 P6: replace `is_owner_local`'s sentinel predicate with `anchor_kind = 'owner_local'`
