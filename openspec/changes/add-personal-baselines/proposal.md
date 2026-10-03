## Why

"Unusual for you" is claimed in three places with three definitions: Home stores an n=1 prose energy
baseline, Finance stores a spending baseline fact and compares against a bare 2x average, and Health's
weekly drift scan compares the medians of two thirds of the last twenty readings with no coverage
test. None of them says how many days the claim rests on, none of them stops speaking when the sensor
is dead, and none keeps an anomaly out of its own baseline (bu-q7vx1q.15, JARVIS pursuit run 15).

## What changes

- A pure, deterministic core module (`butlers.core.baselines`) computes a per-metric robust band
  (median and MAD) over a trailing window, with the honest denominator (`n_observed` of
  `n_expected` days), a coverage floor, a minimum-dispersion floor, and the instrument's
  measurability. Every score is one of `within`, `above`, `below`, `insufficient_history`,
  `unmeasurable` or `stale`; only a ready, fresh, measurable band can say `within`.
- Two per-butler core tables (`core_258`): `metric_baselines` and `metric_deviation_episodes`.
  Nothing goes in `public`, so a butler's baselines are readable only through its own schema.
- Deviation episodes: `k` consecutive days outside the band in a concerning direction open one
  episode; a scored return to the band closes it; a day that cannot be scored holds it open. Episode
  days are excluded from later windows, and the window ends before the run being judged, so the band
  never chases the anomaly.
- Slice 1 adopts it in Health: a daily deterministic `baseline_watch` job for resting heart rate,
  heart rate variability and sleep duration. An opened episode is reconciled into the owner-condition
  ledger and proposed as one insight candidate carrying `baseline_evidence` and an `owner_condition`
  premise, so the candidate is withdrawn unsent or amended in place when the metric returns to its
  band. The wording reports the deviation from the owner's own range and the days it rests on; it
  does not interpret or advise.
- The insight broker rejects a `baseline-deviation` candidate that lacks `baseline_evidence`.

## Deferred

- Slice 2: `metric_baseline_get` core tool, `GET /api/health/baselines`, and the measurements-tab
  band with an "n of m days" plaque.
- Slice 3: Home energy on week-bucket baselines; removal of the `energy_baseline` prose facts.
- Slice 4: Finance anomaly detection and the briefing on `metric_baselines` with currency-scoped
  keys; removal of the `spending_baseline` facts and the briefing's 2x average.
- Slice 5: chronicler balance lanes and session spend adopt the scorer.
- The `week` bucket (needs per-week aggregation); slice 1 ships `day` and `dow`.
