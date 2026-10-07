## Context

Audited base60a2 has CI sources identical to current main0af0e213. The real extracted old shell accepts preflight failure/cancel with successful heavy lanes; a failed unit still rejects. This is a synthetic-verdict real-source counterexample, not a hosted canary.

## Goals / Non-Goals

Retain delivered M1 complete fail-closed needs/static survivors and implement released M2 coverage separation. Preserve closed bu-tt97y: preflight stays independent of heavy-ran classification. Keep required contexts check/guards/frontend, advisory frontend-e2e, test selection/markers/workers/full merge-group population/sanitized evidence. No M3/M4 implementation, whole closure, baseline sync/archive, runtime/SQL/grants or seven-minute target. M1/M2 claim no wall-clock gain.

## Decisions

### Typed actual workflow consumer

An inline stdlib consumer receives `toJSON(needs)` and event/ref. Missing mandatory jobs, duplicate JSON keys, invalid result/output types, failure/cancellation/unknown values and unknown modes fail. Every added needed job is consumed; success can pass, skip defaults to denial. The native spec contains the complete five-mode policy.

Changes/guards must succeed. Backend PR planner succeeds with full/scoped and valid selected-path JSON: full is empty, scoped is a nonempty list of repository-relative test paths. Other modes require skipped plan. Backend PR/merge-group preflight succeeds; docs PR/main push may skip. Heavy jobs all succeed only in full PR/merge-group and all skip in scoped/docs/main push. Affected succeeds only scoped. Invalid classifier outputs cannot authorize skips. Push admits refs/heads/main only. Preserve `shards_ran` for existing downstream coverage; coverage location/behavior is unchanged in M1.

### M2 complete same-run reporting

Required check retains that exact consumer and all fifteen needs, with only its verdict step.
The non-required coverage job directly needs the ten heavy shards and uses always() with
merge_group and ten successful results. It retains checkout, Python, the complete five-attempt
UV installer, frozen dependencies, ten named downloads, combine/report, retry-safe combined
artifact and existing badge action/secret references. No required context awaits reporting.

Heavy jobs supply literal CI_COVERAGE=1 for merge_group and 0 for PRs. The shard runner accepts
only those supplied values and defaults to 1 for standalone compatibility. Enabled calls require
COVERAGE_FILE and retain existing instrumentation; disabled calls omit coverage arguments and
remove inherited COVERAGE_FILE from the pytest child. Every other selector, marker, maxfail,
worker, distribution, JUnit and evidence setting stays unchanged. Raw uploads occur only when
enabled, keep each original distinct name, and add a metadata sidecar alongside the raw database.

After real pytest exits, an existing raw file receives ci-coverage.v1 metadata: actual Git checkout,
workflow run/attempt/event, lane/shard, current lane configuration and selected manifest paths,
complete current src/butlers Python paths, and the raw SHA256. Missing data is never fabricated.
Before combining, the reporter requires the exact ten directories and raw/sidecar pairs, exact
identity/configuration/selection/source/digest metadata, and individually reads every database
with installed CoverageData. Each must contain the complete current measured source population,
executed lines and compatible line/arc/plugin tracing. Missing, empty, extra, corrupt, stale or
mixed evidence fails visibly before report/badge outputs. This is reporting integrity, not an
authorization boundary or a coverage percentage threshold. Existing combine semantics survive;
combine warnings alone cannot establish completeness. No badge credential value is inspected.

### Checker survivors

Copy existing five-attempt UV policy into guards before lock; frozen dev sync precedes lint/format/SQL. Preserve Python/Node/OpenSpec and all current guards. Four moved steps get IDs/finalizer entries; only non-PR session-links skip is permitted. Setup failures remain job failures. Add guards directly to check.needs. Makefile check-lock/check-format/check-guards use identical nonmutating commands/scopes. Exact-once, budget, smoke cmd/SHA/status/duration/skips and sanitized artifacts remain preflight.

### Whole original continues

M2 source controls do not establish actual hosted uploads or timings. M3 later introduces finite pytest/Node/job bounds with full UV recovery/setup headroom and browser TERM110/KILL120 within each120s attempt. M4 keeps two real merge-group reports/~5s check, ten before/after check samples, ten clean merge-group p95/cap comparisons and complete two-week zero>60 audit. Every unfinished source/observation remains mandatory/unchecked; existing faketime75m/3600sABRT+30s cleanup survives. No-gain general alternative does not delete specific observer clauses.

## Risks / Trade-offs

Guards dependency adds setup/delay but makes moved static failures red in both required contexts. Strict output validation must accept actual planner serialization and real scoped success. Provisional metadata cannot become healthy calibration or after proof. Newly declared jobs deny skips until explicit policy review.

## Verification

Use actual YAML run blocks and retain make/cleanup/smoke/manifest/coverage positives. Five literal positive modes; each need failure/cancel/missing/unknown/malformed/default-skip negatives; preflight outside heavy classifier. Neutralize independent preflight/guards/defaultskip/partialmix handling to require reached causal reds. Hosted real format/Ruff canaries and genuine scoped positive are separate. Exact node/file then dirty-planner escalation, tests/roster collection, budget/shards/Ruff/format/guards and one exact source hosted/protected run. No local Docker workaround.

M2 additionally executes real local pytest in both instrumentation modes and the actual reporting
shell with ten independently traced databases. The immutable public M1 shell fixture demonstrates
the nine-valid/one-nonempty-corrupt partial-report defect; the fixed shell rejects that same input.
Missing/empty/extra/mixed-run/stale-head/wrong-shard/wrong-manifest/digest/tracing/source-population
controls have a ten-valid reporting companion. These are local fixture observations, distinct from
real hosted downloads/uploads, badge network execution, full-suite coverage and timing evidence.

## Rollback / Ownership

Revert the reviewed milestone commit through the normal PR route; reverting M2 restores M1's reporting location without changing its verdict policy. No transfer of .2/.5/.6/.9/.12, held gvut0 or foreign deltas/manifests. Serialize exact overlapping hunks. No reverse dependency on terminal .16. Do not synchronize/archive full change while any mandatory implementation or observer task remains unfinished.
