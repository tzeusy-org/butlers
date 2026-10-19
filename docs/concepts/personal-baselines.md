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

## Reading baselines (slice 2)

The stored band is readable three ways, all from the caller's own schema and all read-only:

- **Core tool `metric_baseline_get(metric_key)`** (`src/butlers/core_tools/_baselines.py`, group
  `baselines`) returns `{state, band, n_observed, n_expected, coverage, computed_at, stale}`.
- **`GET /api/health/baselines`** returns the same for every Health metric, through the Health
  router's own pool. An unreadable store answers `available = false` with `baselines = null`, never
  an empty list.
- **The Health measurements tab** shows the usual range with an "n of m days" plaque, and shades it
  on the HRV trend, only for a `ready` baseline.

`state` follows the scorer's precedence, so the tab and a deviation notice never disagree about the
same row: `unmeasurable`, then `stale` (computed more than twice the refresh interval ago), then
`insufficient_history`, otherwise `ready`. A metric with no stored row reads `no_baseline` (the job
has not run, or the key is unknown). `band` is present only for `ready`; every other state renders
as one sentence, never as a range. Resting heart rate and sleep duration have no trend panel that
charts them, so they show the range and plaque in the "Usual range" panel only.

Home energy, Finance spend and later metrics are later slices; see
`openspec/changes/add-personal-baselines/proposal.md`.
