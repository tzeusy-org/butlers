## 1. Slice 1: outcome ledger, observe-only (bu-s11n0s.15 S1)

- [x] 1.1 Migration `core_353`: `public.insight_outcomes` and `public.insight_class_efficacy`,
      installed once across the per-schema core chains, Switchboard-only grant and RLS fence,
      grant manifest rows.
- [x] 1.2 `outcomes.py`: exposure taxonomy, enrollment of terminal premised candidates while their
      window is open, and the once-only sweep (exact owner-condition resolution, observed-by probe
      resolution, `unmeasurable` after three unknown reads, `persisted` at window close).
- [x] 1.3 Daily `insight_outcome_sweep` job (`dispatch_mode = "job"`, 05:52 UTC).
- [x] 1.4 Spec delta, schema topology and data retention docs.

## 2. Slice 2: efficacy verdicts, observe-only (bu-s11n0s.15 S2)

- [x] 2.1 Slice 2: verdict rollup and `GET /api/attention/efficacy` (degraded envelope), panel rows,
      and the reading caveats (biased holdout selection, a shorter treated window after a delay)
      in `docs/butlers/switchboard.md`.

## 3. Slices 3-5: owner opt-in shaping (bu-s11n0s.15)

- [x] 3.1 Slice 3: `core_354` `outcome_shaping` (default `observe`, owner-only) and `shaping_state`;
      under `shape`, self-resolving classes are digest-only and effective classes override the
      click proxy; never the urgent band.
- [x] 3.2 Slice 4: one line per class change, at most once per 30 days, with the useful and mute doors.
- [x] 3.3 Slice 5: no engagement row for deliveries of a class with a fresh measured verdict in its band.
- [x] 3.4 Spec requirement 039, RFC 0011 amendment 9, Switchboard and schema docs.
