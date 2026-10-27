# CI cost baseline: ten exact-main merge-group runs, 2026-10-15

This records ten successful historical merge-group attempts, with complete job
metadata and sanitized per-file JUnit costs. It does **not** establish the current
pipeline's five-minute lanes or seven-minute required-context p95. All ten runs
precede the `bu-q1hzqn` pipeline changes; covered CTracer costs cannot be treated
as savings available in today's untraced jobs. No tests were executed to compile
this report. Tests: +0 ~0 -0.

The machine-readable projection behind these tables (every run/head/tree/attempt,
job/step timestamp, runner identity, selected population, artifact ID/SHA-256 and
per-file cost) is no longer committed: CI evidence lives in the CI run artifacts
of the runs listed below, and the projection itself remains in git history before
bu-29wkwj.1. Test case identifiers, parameter values, raw logs and process traces
were deliberately excluded from it.

The [2026-10-17 integration attribution](integration-cost-attribution-2026-10-17.md) adds ten newer hosted phase observations, explicit missing worker clocks and owner-routed candidates; it does not restamp this historical baseline.

## Source-compatible profiling candidates first

File-byte compatibility alone does not prove equal collection inputs, fixtures,
tracer, pipeline or runtime. The comparison checkpoint is main
`36f0e0eb080c7c78cf55ce54a02fecab1dfd4f8e`. Each row below has ten matching
file blobs unless explicitly stated; seconds are cumulative JUnit testcase work,
including setup/call/teardown, **not** a parallel job's elapsed time.

| Lane / current-source file | Samples | Median seconds | Observed maximum seconds |
| --- | ---: | ---: | ---: |
| unit / `roster/relationship/tests/test_entities_api.py` | 10 | 238.368 | 499.85 |
| unit / `roster/relationship/tests/test_entity_tabs.py` | 10 | 199.957 | 368.029 |
| unit / `tests/api/test_api_health_facts_reads.py` | 10 | 159.268 | 253.361 |
| unit / `roster/education/tests/test_api.py` | 10 | 101.126 | 150.908 |
| unit / `tests/scripts/test_condense_evidence.py` | 2 | 98.136 | 104.02 |
| integration / `tests/api/test_optional_schema_lifecycle_db.py` | 10 | 224.22 | 236.989 |
| integration / `roster/relationship/tests/test_entity_dedup_curation_job.py` | 10 | 194.629 | 208.185 |
| integration / `tests/migrations/test_runtime_attention_outbox_migration.py` | 10 | 184.767 | 194.651 |
| integration / `roster/concierge/tests/test_dashboard_read.py` | 10 | 172.88 | 252.031 |
| integration / `tests/migrations/test_core_255_insight_premise_binding_migration.py` | 10 | 165.339 | 171.282 |
| integration / `tests/config/test_migrations.py` | 10 | 147.177 | 155.976 |

`test_condense_evidence.py` is the instructive exception. Current blob
`7de943d80d640c9864fd2e7f1947927f5fac1014`, SHA-256
`ff16dea36a249cbb510bcae72f2def457bc722bc602ab8fb413065119a5d2d2a`, matches
only **2/10 samples: 92.251 and 104.020 seconds**, each selecting **eight
cases**. This is insufficient for a current ten-run p95. Older blob
`ed11e426a78510c4ca742044195acf1cdf178d90`
appears in eight samples, each selecting **ten cases**, at 651.570–747.312
seconds (median 699.980). This includes selection/quarantine changes and is not
a matched speedup measurement. The mixed historical ten-run median is 682.841 seconds; ranking that as today's cost would
be misleading. The module is unit-marked. Current `tests/quarantine.json` excludes
three owners from ordinary runs, separately owned by `bu-oq6eiu`, `bu-uikmow`
and `bu-xle97o`; `tests/quarantine.py` performs the deselection. Historical and
current registry/plugin/pyproject blobs are retained to expose these selection
changes. Literal skip markers inside generated test fixtures are not module
quarantines. Static function counts cannot explain this timing difference.

Ownership check: `bu-ly3lv5.11` owns executable condensation-proof work,
`bu-jknlon` owns its hosted timeout diagnosis and `bu-7xrfz2` its bounded token
decoding investigation. Do not start competing cleanup from this baseline.
Integration profiling belongs to `bu-gvut0.4`; migrated-template work is already
active in `bu-ly3lv5.8` / PR #4447. The existing computed partition owns balancing;
these measurements do not justify adding shards or reviving an old manifest.

The smallest next controlled profile is one current-source relationship API
file, on the reference four-CPU runner with the **current ordinary tracer and
worker configuration**: retain complete selected membership, split fixture
setup/call/teardown, and record app construction separately from assertions.
Start with `roster/relationship/tests/test_entities_api.py` (140 cases in each
historical sample). Confirm the existing `bu-ly3lv5.11` owner's chosen cluster
before executing. For integration, coordinate the already-owned optional-schema
profile with PR #4447, splitting container creation, database/migration setup and
retained behavior without changing fixture lifetime or DB roles. These are
proposals, not executed measurements or redundancy/deletion verdicts. No direct
current-runtime saving can be inferred from this covered baseline.

## Cohort and identities

Selection is the first ten successes in the preserved 30-run snapshot, in its
existing newest-first order. Each selected head was verified as an **exact
first-parent commit of main** at the checkpoint above, not merely an ancestor.
All selected attempts are attempt 1. Failures, cancelled runs, retries and
pre-workflow enqueue/requeue waits are outside this success-only cost sample.
This is a rolling, mixed-source historical cohort, not a post-change experiment.

| Run (attempt 1) | Exact tested head | Required contexts seconds |
| --- | --- | ---: |
| [38667022310](https://github.com/tzeusy-org/butlers/actions/runs/38667022310) | `e6436fdbc3fad17788df0913d89d373b0d2fb5bf` | 1168 |
| [38667020407](https://github.com/tzeusy-org/butlers/actions/runs/38667020407) | `0b1c2e138ae1277b87fcc7a040385ae1e8156b29` | 1326 |
| [38596379851](https://github.com/tzeusy-org/butlers/actions/runs/38596379851) | `1c4c1714f87eb798032148c56cbf02c977dc8478` | 1534 |
| [38596036090](https://github.com/tzeusy-org/butlers/actions/runs/38596036090) | `f2dba95ef2c0510bee657f912272146c835ad220` | 1580 |
| [38585189553](https://github.com/tzeusy-org/butlers/actions/runs/38585189553) | `ba49fc696cf007c7b1f72cedefd47a7357e09f00` | 1445 |
| [38495102887](https://github.com/tzeusy-org/butlers/actions/runs/38495102887) | `3bde6119914e066414451d3df609a8c20e84fa30` | 1555 |
| [38490828715](https://github.com/tzeusy-org/butlers/actions/runs/38490828715) | `0448b4bafd18ded3287b1e6ea2babcbafbe5bf4e` | 1339 |
| [38461280251](https://github.com/tzeusy-org/butlers/actions/runs/38461280251) | `2917096b5e7f3f46b26f7f4b27aadb6980fd1922` | 1405 |
| [38450273760](https://github.com/tzeusy-org/butlers/actions/runs/38450273760) | `b747a55a530364d987fb37457c343193e9362fe7` | 1275 |
| [38419232190](https://github.com/tzeusy-org/butlers/actions/runs/38419232190) | `fb0dad17316c0b20869e7b4de97ea0ba97fbb1a6` | 1415 |

Runner API rows identify each concrete runner ID/name, group `GitHub Actions`
and `ubuntu-latest` label. The OS image name/version is **UNAVAILABLE** in the
retained API/receipt fields; no raw setup logs were inspected to invent it.
All 110 Python child receipts report Python 3.12.15, four logical CPUs / affinity
four, requested xdist workers three, coverage core `ctrace`, and actual
`CTracer`. Vitest receipts report Node 24.21.0 and available parallelism four.
Runner IDs and exact per-job observations remain in the JSON; labels are not a
hardware or account-quota proof.

Selected installed inventory ranges are unit 17,616–17,634 nodes in 999–1,002
files and integration 5,005 nodes in 405 files. Static AST counts are separately
18,864–18,876 `test_*` definitions in 1,428–1,432 Python files under `tests/` and
`roster/*/tests/`; this deliberately includes nested definitions and is not a
pytest population. At the pinned current-main checkpoint, the same static method
counts 18,903 definitions (14,508 tests / 4,395 roster) across 1,438 Python files;
no fresh pytest collection or test bodies were run. The unfiltered collection total before selectors/quarantine
is **UNAVAILABLE**. The 1,407 lane/file records represent the union across ten
samples; a file selected in both lanes has two records, never merged costs.

## Historical job and required-path costs

All statistics use the ordinary median and nearest-rank p95,
`sorted(values)[ceil(.95*n)-1]`; with ten observations p95 is the observed
maximum. Job wall is start-to-completion. Required-path elapsed is
`run.created_at` to the latest actual completion of `check`, `guards`, and
`frontend`, not `run.updated_at`. The live service budget is 420 seconds at p95 over at
least 20 complete eligible samples; this ten-run descriptive baseline cannot
satisfy that minimum. Its historical p95 of 1,580 seconds exceeds 420 by 1,160
seconds and is not a current-rollout verdict. The `frontend` verdict alone is not all
frontend execution. Workflow last-job elapsed is reported separately in JSON.

| Historical scope | Median seconds | p95 seconds | p95 excess over 300 seconds |
| --- | ---: | ---: | ---: |
| unit: slowest child per run | 955.5 | 1110 | 810 |
| integration: slowest child per run | 664 | 772 | 472 |
| Required contexts from run creation | 1410 | 1580 | Not a lane |

The fastest observed slowest-child wall was 614 seconds for unit and 498 for
integration. These are empirical minima, not irreducible optimized floors.
Largest retained whole-file cumulative costs had historical medians 682.841 /
225.842 seconds for unit / integration; this describes indivisible file work in
the measured loadfile execution, excluding collection and finalization. It is
neither a guaranteed wall-time lower bound under changed concurrency nor a
reason to remove coverage. Current-source and quarantine changes above further
limit its relevance. A strict irreducible lower bound is **UNAVAILABLE**.

The complete decomposition below uses the named primary test/reconciliation
step. Before/after are job-start-to-step-start and step-end-to-job-end. They
include setup, artifact operations and residual job intervals; they are not
fixture setup/teardown. Independently calculated quantiles need not add up.

| Job | Wall median / p95 | Primary step median / p95 | Before median | After median | Eligible-to-start p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `guards` | 353.5 / 414 | 71 / 84 | 150.5 | 133 | 17 |
| `check-preflight` | 32 / 36 | 6 / 7 | 22.5 | 4 | 49 |
| `check-unit (1)` | 945 / 1026 | 917.5 / 1004 | 21.5 | 5 | 260 |
| `check-unit (2)` | 308.5 / 402 | 282.5 / 370 | 20 | 6 | 184 |
| `check-unit (3)` | 346.5 / 1089 | 320 / 1060 | 19.5 | 6 | 60 |
| `check-unit (4)` | 449 / 546 | 418 / 510 | 21.5 | 7.5 | 383 |
| `check-unit (5)` | 515 / 1110 | 486 / 1067 | 22.5 | 6 | 237 |
| `check-integration (1)` | 473 / 772 | 450 / 724 | 20 | 6 | 365 |
| `check-integration (2)` | 499 / 581 | 469 / 486 | 20.5 | 5.5 | 188 |
| `check-integration (3)` | 664 / 694 | 641 / 667 | 18.5 | 5 | 358 |
| `check-integration (4)` | 340 / 387 | 311 / 327 | 23 | 5.5 | 271 |
| `check-integration (5)` | 388 / 460 | 358.5 / 429 | 22.5 | 6.5 | 358 |
| `check-integration (6)` | 401.5 / 450 | 372 / 423 | 20 | 6 | 399 |
| `frontend-vitest (1)` | 476.5 / 528 | 449.5 / 492 | 24.5 | 4 | 13 |
| `frontend-vitest (2)` | 467.5 / 513 | 439.5 / 481 | 23.5 | 3.5 | 347 |
| `frontend` | 11.5 / 15 | 0 / 1 | 8.5 | 3 | 58 |
| `frontend-e2e` | 173.5 / 200 | See named steps | n/a | n/a | 112 |
| `coverage` | 59.5 / 73 | 27.5 / 29 | 30 | 5.5 | 33 |
| `check` | 3 / 4 | See named steps | n/a | n/a | 164 |

Every actual step is retained, including zero-duration and skipped steps. Named
step summaries group matching names; duplicate names within one job yield more
than ten observations (for example Python/frontend dependency installation), so
use each job's ordered raw step rows for attribution. The guards primary step
is inventory collection plus budget admission (median 71, p95 84 seconds), not
all guard work. Its condensation evidence step was median 80 / p95 97 seconds;
frontend build was 40.5 / 43 seconds. The preflight reconciliation step was
6 / 7 seconds; its whole job was 32 / 36 seconds. These are distinct scopes.

Historical Vitest independently collected the full reference population in
both children. Child 1 reference median/p95 was 274.796 / 300.569 seconds,
execution 173.986 / 191.291; child 2 reference 265.636 / 289.377, execution
173.387 / 191.295. Reference file counts were 524–527. This predates the
supported dependency prebundle change and is not a matched hosted speedup claim.
Fresh reference collection, declaration/start/outcome reconciliation and child
isolation remain mandatory.

`created_to_started_s` is retained as the API interval, but is not automatically
scheduler time. The JSON also derives prerequisite eligibility from **each
exact historical workflow's declared needs**, including every matrix sibling;
`eligible_to_started_s` is start minus latest predecessor completion (or run
creation for a root job). This separates dependency wait from the remaining
scheduler/admission interval without assuming guards is forever the only
predecessor. It cannot identify account-cap saturation. Actual enqueue,
requeue and merge timestamps are unavailable in this snapshot; run creation is
not a substitute. No end-to-end throughput or queue-policy conclusion follows.

## Acquisition, reproduction and missing observations

Acquisition reused the preserved 30-run run/job API payloads first. It verified
all 21 job rows per selected attempt against exact run/head/attempt and refused
incomplete bounded populations. Ten artifact-index reads used
`/repos/tzeusy-org/butlers/actions/runs/{run}/artifacts?per_page=100`; indexes
were complete and smaller than 100. Exactly 140 named archives were consumed:
`ci-inventory`, eleven `ci-{unit,integration}-{N}-test-evidence`, and two
`ci-vitest-{N}` per run. Duplicate, expired, truncated, oversized or mixed-source
inputs refuse. Each downloaded ZIP digest matched the GitHub SHA-256 metadata.
All 110 backend receipts were complete exit-zero with matching selected-node /
JUnit counts; all twenty Vitest receipts and references/executions were complete
exit-zero. Acquisition errors: **zero**. API updated timestamps are retained
only as metadata, never used as completion clocks.

To reacquire while artifacts exist, read each retained run ID's exact attempt
jobs endpoint (`/actions/runs/{run}/attempts/1/jobs?per_page=100`) and complete
artifact index, require their advertised total counts, then download only the
retained artifact IDs via `/actions/artifacts/{id}/zip`. Compare each ZIP's
SHA-256 against the retained `archive_sha256` before opening it. Within backend
archives consume only `shard-observation.json`, `runner-observation.json`, and the
existing `scripts/summarize_junit_durations.py`-sanitized `junit.xml`. Map canonical
JUnit classnames to the longest matching dotted Python file path from
`git ls-tree -r <head>`; aggregate finite nonnegative durations and outcomes by
lane/file, discard all testcase names, and require exact selected population
counts. Inventory and Vitest receipt identities must match the same head/tree/
run/attempt/event. Do not print logs or testcase identities. Raw indexes/ZIPs
remain private scratch, not repository contents. Per-job artifact IDs/digests
and raw-metadata hashes authenticate retained identities after GitHub expiry;
full independent reparsing still requires the retained private archives and
metadata. A digest alone does not prove the projection was computed correctly.

Unavailable observations remain first-class gaps: OS image version; pre-run
queue/requeue delay; unfiltered collection total; per-child collection versus
body/finalization; individual fixture/setup/call/teardown clocks; strict
irreducible runtime floor; and ten successful current-pipeline comparable runs.
The historical source population is complete for this cohort, but those gaps
prevent full current-baseline acceptance and any claim of meeting either hard
target. Obtain the missing current observations through the existing owners and
natural hosted runs; do not manufacture them from a single untraced run.
