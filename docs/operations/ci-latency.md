# CI latency objective

The owner adopted an objective of **p95 below 420 seconds to the last required
context across at least 20 clean successful merge-group runs** on 2026-10-15.
This is a measured performance objective, not a kill timeout. `bu-q1hzqn` owns
remediation and remains open until at least 20 natural post-rollout samples
establish the result. The separate five-minute routine-lane targets and the
full merge-queue assurance contract still apply.

Run the maintained timestamp-only observer from a checkout with authenticated
read-only GitHub Actions access:

```bash
python3 scripts/ci_latency.py --output /tmp/ci-latency
# Attribute a cohort only after the complete rollout; use its actual UTC time.
python3 scripts/ci_latency.py --since 2026-10-15T02:00:00Z --output /tmp/ci-latency-since
```

The example timestamp demonstrates syntax; it is not a claim that rollout
completed then. JSON and Markdown reports are written before the command exits.
Exit 0 is PASS; exit 1 is BREACH; exit 2 is INSUFFICIENT_SAMPLES; exit 3 is ERROR.
A small sample may show a provisional observed breach but never a passing
verdict. Any malformed, missing, duplicate, mismatched-attempt or truncated
required input makes the entire report ERROR, including when earlier samples
were valid. A p95 exactly 420 seconds breaches the strictly-below 420 objective.

## Cohort and meaning

The default window indexes the newest 100 CI `merge_group` runs regardless of
outcome, then measures the newest 30 first-attempt successes in that window.
This rolling service window may mix rollouts. A passing rolling window means
the observed service objective was met; it does not attribute improvement to
a particular change. Optional `--since` selects a complete UTC after-since
index of at most 100 runs, measuring its newest 30 eligible successes. A larger
cohort refuses rather than silently truncating; choose a recent bounded window
or retain successive reports. The index total is the API's reported total,
not an assertion of complete repository history.

Failures, cancellations, other terminal outcomes, in-progress runs and retried
runs are counted independently. Counts describe each indexed run's latest
attempt outcome; prior attempts are not fetched or classified. Retries are
excluded from the latency objective, including retries that ultimately pass.
No metric measures time before Actions creates a run, failed-attempt cost or
total developer/merge-queue lead time. A healthy success-only p95 cannot excuse
poor reliability; read the outcome and retry counts alongside it.

Each measured success reads `/runs/{id}/attempts/{attempt}/jobs` and validates
the complete job count and every job's run, attempt and head. One bounded page
admits at most 100 jobs; overflow fails. Reads have 20-second timeouts, a 4 MiB
response limit, and a 240-second observer deadline checked before every call.
The final read may finish at 260 seconds; a six-minute job watchdog also bounds checkout, report
and upload. These provisional bounds make no healthy-job p95 claim.
There is no pagination guess, raw log download, test execution, artifact read,
privilege change or metric-based required-gate decision.

| Metric | Definition |
| --- | --- |
| Required completion | Run `created_at` to max `completed_at` of `check`, `guards`, `frontend` |
| Workflow completion | Run `created_at` to latest job `completed_at`, excluding later API `updated_at` changes |
| Workflow tail | Workflow completion minus required completion, computed per run |
| Execution | Job `completed_at` minus `started_at` |
| Scheduling delay | Successful job `started_at` minus its own `created_at` |

The report retains the maximum scheduling delay across all successful jobs and
across shards, separately from guards and maximum shard execution. Job creation
marks scheduler admission, so this works whether shards follow guards or an
earlier inventory producer. It does not infer predecessor completion from job
names or prove an account concurrency cap. Nonexecuted skipped/neutral jobs
contribute only their terminal completion timestamp: GitHub can synthesize a
skipped start one second after its completion. Executed successes must retain
ordered creation/start/completion timestamps. The p50 is the ordinary median;
p95 is nearest rank, with the clean sample count shown explicitly.

## Scheduled visibility and ownership

The separate nonrequired
[latency workflow](../../.github/workflows/ci-latency.yml) runs one stdlib
timestamp-only observer every six hours and on manual dispatch. Manual dispatch
accepts an optional `since` value. The run summary displays the Markdown
report and the `ci-latency` artifact retains both files for 14 days even on a
breach, insufficient sample or collection error. The existing weight candidate
workflow remains independent: a latency breach cannot invalidate its successful
candidates or stop their consumers refreshing. Required queue checks execute
no extra pipeline or measurement work.

Review reports after rollout using the actual rollout time. Breaches stay with
`bu-q1hzqn`; source work is split across `.1` (DAG/instrumentation), `.2`
(advisory measured weights), `.3` (expensive test-file profiling) and `.4`
(this observer). Missing or insufficient data leaves the measurement obligation
open. The broad merge-group gate remains the authority for complete execution.

## Reviewed baseline

The [committed baseline](ci-latency-baseline.json) retains 30 successful runs
created 2026-10-10T19:04:17Z through2026-10-15T01:41:37Z, before this rollout.
Every run has attempt 1. Required completion matches the independently reviewed
baseline; remaining metrics were recomputed from its stored job timestamps
using the maintained observer. The source baseline digest and individual
run/attempt/head coordinates are retained, without raw logs or test identities.
The source query selected successes, so failure/cancellation counts are
unavailable for this baseline rather than implicitly zero.

| Metric | p50 seconds | p95 seconds |
| --- | ---: | ---: |
| Required completion |1165|1555|
| Workflow completion |1198|1575|
| Workflow tail |19|36|
| Guards execution |353.5|414|
| Slowest shard execution |731|1103|
| Maximum shard scheduling delay |2.5|355|

This baseline breaches the objective. The original prototype's workflow
`updated_at` p95 was 1576 s and post-guards shard-start delay p95 was 358 s.
Those are retained as historical context, not renamed into the corrected
workflow-completion or own-job scheduling metrics. An estimated 20-job account
cap is an inference; this observer supplies no administrative quota proof.

[Bazel documents remote caching](https://bazel.build/versions/7.1.0/remote/caching?hl=en)
for reusing action outputs, while
[Meta describes predictive test selection](https://engineering.fb.com/2018/11/21/developer-tools/predictive-test-selection/)
for selecting tests likely to expose regressions. The applicable lesson is to
measure repeated work and selection economics; neither technique establishes
this repository's latency or justifies weakening its full merge-group population.

## Queue admission and developer lead time

The [bounded admission experiment](ci-queue-admission.md) supplements this
observer with complete enqueue/requeue/merge metadata and throughput. Its
read-only plan does not change live settings. Run-created latency alone cannot
establish the trial's end-to-end benefit, and observed job overlap does not
certify an administrative runner quota.
