## 1. Slice 1: outcome ledger, observe-only (bu-s11n0s.15 S1)

- [x] 1.1 Migration `core_353`: `public.insight_outcomes` and `public.insight_class_efficacy`,
      installed once across the per-schema core chains, Switchboard-only grant and RLS fence,
      grant manifest rows.
- [x] 1.2 `outcomes.py`: exposure taxonomy, enrollment of terminal premised candidates while their
      window is open, and the once-only sweep (exact owner-condition resolution, observed-by probe
      resolution, `unmeasurable` after three unknown reads, `persisted` at window close).
- [x] 1.3 Daily `insight_outcome_sweep` job (`dispatch_mode = "job"`, 05:52 UTC).
- [x] 1.4 Spec delta, schema topology and data retention docs.

## 2. Deferred slices

- [ ] 2.1 Slice 2: verdict rollup and `GET /api/attention/efficacy` (degraded envelope), panel rows.
- [ ] 2.2 Slice 3: owner opt-in `outcome_shaping` (owner decision first).
- [ ] 2.3 Slice 4: owner digest lines with keep/quiet doors.
- [ ] 2.4 Slice 5: retire the 60-minute engagement mark per covered category.
