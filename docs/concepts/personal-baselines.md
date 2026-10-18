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

## Finance category spend (slice 4)

Finance judges a category's "unusual spending" against a weekly personal baseline per category and
currency (`spend.category:<category>:<CCY>`; `src/butlers/jobs/finance_spend_baselines.py`). The daily
`anomaly-insight-scan` job stores the median and MAD band of up to 26 complete owner-local weeks
(Monday start) in the finance schema's `metric_baselines`; a week with no spend is not a sample, and
a category needs 4 weeks of spend before it has a band. The `anomaly_scan` tool and the daily
briefing score the spend of the week so far against the stored band and report only an `above` state
with the usual range and the weeks it is drawn from; thin, stale or missing history is never called
an anomaly, and nothing ever reports spending as normal or within range. Transactions have no single
connector producer identity, so Finance does not use the expected-signal measurability check; coverage,
the 4-week floor and the staleness rule stand in for it. Currencies are never mixed. Merchant amount anomalies and new-merchant detection keep
their inline computation, and the old write-only `spending_baseline` memory facts are retracted
(rows kept) by the same job.

Home energy and the dashboard band are later slices; see
`openspec/changes/add-personal-baselines/proposal.md`.
