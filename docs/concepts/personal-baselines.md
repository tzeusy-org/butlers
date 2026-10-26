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

Home energy and Finance spend are later slices; see
`openspec/changes/add-personal-baselines/proposal.md`.


## Stored reads and Health display (slice 2)

`read_baseline(pool, metric_key, now=...)` reads the caller's schema and returns `None`
for a key with no current-method row. `read_baselines` reads a requested list in one
SELECT; it never falls back to another schema or method version. Both use the stored
counts and the scorer's precedence: unmeasurable, stale, insufficient history, ready.
Only ready, fresh rows expose a band. Freshness is evaluated against injected `now`;
a row older than twice the metric's refresh interval is stale. Reads write nothing.

The registered core tool `metric_baseline_get(metric_key)` is an infrastructure read
on the daemon's captured pool. Its missing result is a typed `no_baseline` response.
`GET /api/health/baselines` reads the three existing Health metrics through Health's
pool. It returns counts, coverage, method version and computation time alongside each
state. A failed store read returns `available: false` with `baselines: null`, rather
than pretending there are no rows. Authentication follows the existing Health API.

Measurements shows resting heart rate (bpm), HRV (ms), and sleep duration (hours)
usual ranges with “n of m days”. HRV's ready band also overlays its matching trend.
The generic heart-rate trend is a different measurement and never uses resting HR's
band; sleep-stage durations in minutes are likewise not given an hours overlay.
Thin history, unavailable instruments, stale rows and missing rows show named states
without ranges. A baseline request failure leaves the existing measurement trends
visible. These are descriptions of the owner's history, not population targets or
medical advice.
