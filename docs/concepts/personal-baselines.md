# Personal baselines

A personal baseline is the owner's own recent band for one metric. Any claim of the form
"unusual for you" is made from one, through `butlers.core.baselines`, and from nothing else.

- **Band:** the median and a MAD-derived dispersion over a trailing window of per-day values
  (`mad-v1`). The window ends a few days before the day being judged and leaves out days that
  belong to a deviation episode, so the band does not chase the anomaly it is meant to detect.
- **Honest denominator:** every band carries `n_observed` of `n_expected` days and the coverage
  between them. A band below the metric's minimum count or coverage is `insufficient_history` and
  has no center.
- **Measurability:** the producer's liveness comes from the shared expected-signal evaluation
  ([Expected signals](expected-signals.md)). A dead instrument makes the band `unmeasurable`.
- **Six states:** a value scores `within`, `above`, `below`, `insufficient_history`,
  `unmeasurable` or `stale`. Absence of evidence is never reported as `within`.
- **Episodes:** `k` consecutive concerning days open one deviation episode; a scored return closes
  it; a day that cannot be scored holds it open.
- **Storage:** `metric_baselines` and `metric_deviation_episodes` are per-butler core tables
  (`core_258`) in the butler's own schema (never a shared cross-butler table).

## Health (slice 1)

The Health `baseline_watch` job (daily, deterministic, no LLM; `src/butlers/jobs/health_baselines.py`)
baselines resting heart rate, heart rate variability and sleep duration. An opened episode is
reconciled into the owner-condition ledger (`health:baseline-deviation`) and proposed as one
`baseline-deviation` insight candidate with `baseline_evidence` and an `owner_condition` premise; the
broker refuses that category without evidence. The wording reports the deviation from the owner's
own range and the days it rests on, without interpretation or advice. The job logs no measured
values.

## Chronicles lane balance (slice 5)

The Chronicler `/balance` and `/trends` endpoints score each lane's day against the same scorer
(`src/butlers/chronicler/balance.py`). The band is the median and MAD of the lane's per-day seconds over
the lookback days before the scored day (28 by default); a materialized rollup day is an observed day,
and a lane with fewer than seven of them reads `insufficient_history` with no center and no anomaly.
The band is the median plus or minus three scaled MADs, but never narrower than one hour on each side
(`LANE_MIN_BAND_HALF_WIDTH_SECONDS`, the same for every lane), so a lane that is flat or short does not
flag minute-level noise. A lookback also needs a quarter of its days materialized before it is scored.
The center is returned as `baseline_seconds`, with `state`, `band_low`, `band_high` and
`baseline_sample_days` beside it. It computes per request and persists nothing. Session spend is not
scored.

Home energy, Finance spend and the dashboard band are later slices; see
`openspec/changes/add-personal-baselines/proposal.md`.
