# CI shard overhead and evidence

> **Superseded (bu-29wkwj.2).** The backend lanes now run as pytest-split groups over the
> committed `.test_durations` (`scripts/ci_lanes.py`). The `inventory` and `coverage-inventory`
> jobs, the weight observer (`ci_weight_candidates.py`, `ci-shard-weights.yml`) and the preflight
> receipt reconciler are retired; check-preflight compares each lane's group counts with one fresh
> `--collect-only` count instead. This page records the retired computed-partition design and
> its measurements; `ci_partition.py` survives only for the manual watchdog diagnostic.

The backend has five unit and six integration computed file shards, independent
preflight and affected paths, and the always-running fail-closed fan-in. All
existing watchdogs and healthy-workload floors remain. This change claims no
wall-clock gain. Ten genuine merge-group observations per original slice,
installation and image measurements, full normal/protected gates, and the hard
setup/first-result/tail targets remain required before whole closure.

## Early inventory and concurrent required guards

`route` starts `inventory`, `guards` and the two applicable Vitest children.
The bounded inventory job uses the same Python identity, exact advisory environment
key, mandatory frozen editable repair and existing `ci_partition.py` command as
the former guards producer. It performs one fresh installed full collection,
enforces both unchanged lane budgets and uploads the same exact-attempt
`ci-inventory` artifact. Five unit and six integration children depend on route
and successful inventory, so unrelated static checks and the frontend build no
longer serialize their start. The scoped affected job also starts after route.

`check` remains checkout-free and verdict-only. Its mode table requires successful
inventory for full PR/merge-group and main push, and a skipped inventory only for
scoped/docs PRs. Missing, failed, cancelled, malformed or wrong-mode inventory
results fail. Guards stays mandatory in `check` and required `frontend`; browser
still waits for guards and consumes its one source-bound build. Preflight retains
its guards and matrix prerequisites and all eleven complete-identity, smoke and
release-evidence checks. Coverage cadence, instrumentation, membership and weights
are unchanged.

The actual expanded DAG has 22 rows (20 before the two final required fan-ins,
including the mutually exclusive affected row). Its maximum weighted antichain
is 14: eleven backend children plus guards plus two Vitest children. Once guards
finishes, browser can replace its one slot. The workflow contract enumerates the
actual DAG rather than assuming matrix declarations each cost one runner.
Inventory retains the former producer's complete 35-minute cap provisionally;
the dated inventory measurement below does not supply multi-week timeout
calibration, and all existing job caps and UV recovery reserves remain.

The independent 30-run before observation on 2026-10-15 measured required-path
p50/p95 at 1165s/1555s and guards at 353.5s/414s; scheduling delay was 3s/358s.
The sample contains the latest 30 successful merge-group runs from
[38078327862](https://github.com/tzeusy-org/butlers/actions/runs/38078327862)
(2026-10-10T19:04:17Z) through
[38667022310](https://github.com/tzeusy-org/butlers/actions/runs/38667022310)
(2026-10-15T01:41:37Z). Required-path duration spans run creation to the latest
completion of check, guards and frontend; p95 uses nearest rank. Failed and
retried runs are excluded, so this is not an all-run service SLO.
The source graph removes guards from backend/Vitest start prerequisites but
adds a separately scheduled environment setup. A named hosted after observation
is still required to measure the result; a single run cannot establish the
parent 20-run objective, a seven-minute bound, or the original five-minute lane
promises. No wall-clock gain is claimed from the graph alone.

## Inventory measurement, 2026-10-16

The ten most recent successful first-attempt `CI` `merge_group` runs on the
`main` queue after 2026-10-15T09:32:15Z give inventory producer p50/p95 of
**74s/93s** and inventory whole-job p50/p95 of **115s/143s**. The producer
p95 <=90s and whole-job p95 <=120s targets are both missed. This is a
measurement-only update: no workflow or producer optimization is claimed.

The read-only `GET /repos/tzeusy-org/butlers/actions/workflows/ci.yml/runs`
query used `event=merge_group`, `status=success`,
`created=>2026-10-15T09:32:15Z`, `per_page=100`, and page 1. Its complete response
contained 20 runs. All had attempt 1 and `gh-readonly-queue/main/` branches;
selecting the latest ten by creation time produced the following cohort without
replacing any missing observation. Each full head is on the first-parent history
of main at `27ba30d28a8dc6d907aa0791751dfe7afb86d58f`. Measurements were captured
at 2026-10-16T17:42Z from `GET /repos/tzeusy-org/butlers/actions/runs/<id>/attempts/1/jobs`
responses, with run/head/attempt,
complete pagination and the unique successful `inventory` job checked.

Queue wait is the inventory job's own `created_at` to `started_at`; it excludes
its wait for `route`. Whole-job wall is `started_at` to `completed_at`. Step
wall is that step's `started_at` to `completed_at`. API timestamps have one-second
resolution, so an observed 0s is not a claim of zero work. Missing timestamps,
steps, identities or cache evidence would be `UNKNOWN`, never inferred from
neighboring steps. All timing observations below are present. Step sums need
not equal job wall because runner gaps are not assigned to a step.

| Ref | Run / inventory job | Full head SHA | Queue (s) | Job (s) | `.venv` cache hit |
| --- | --- | --- | ---: | ---: | --- |
| R1 | [38927012323](https://github.com/tzeusy-org/butlers/actions/runs/38927012323/job/117061137781) | `27ba30d28a8dc6d907aa0791751dfe7afb86d58f` | 2 | 81 | yes |
| R2 | [38924036334](https://github.com/tzeusy-org/butlers/actions/runs/38924036334/job/117050984330) | `d8599adf529da7ee43a2996ebf26325291ebfbce` | 2 | 115 | no |
| R3 | [38832816064](https://github.com/tzeusy-org/butlers/actions/runs/38832816064/job/116755235083) | `51ec60a37b9ab2544fb172d80b989e34f9875349` | 2 | 128 | yes |
| R4 | [38828475429](https://github.com/tzeusy-org/butlers/actions/runs/38828475429/job/116741498754) | `633075b4443cdf59115de95e9704b6f2e4bdadd3` | 2 | 143 | no |
| R5 | [38828416026](https://github.com/tzeusy-org/butlers/actions/runs/38828416026/job/116741304842) | `27694036608432b9bfd18e6d70964a6142b9c0a4` | 2 | 113 | no |
| R6 | [38820543948](https://github.com/tzeusy-org/butlers/actions/runs/38820543948/job/116715865797) | `cb2b4a7981b82f61d7b182b434edf23330153db7` | 2 | 135 | no |
| R7 | [38814969781](https://github.com/tzeusy-org/butlers/actions/runs/38814969781/job/116697323646) | `a5ab052d3b6458e0071283834a42129f0d698ebd` | 2 | 126 | no |
| R8 | [38812590287](https://github.com/tzeusy-org/butlers/actions/runs/38812590287/job/116689325431) | `3f184e63a0817ae92087b7b50bd98c94f41b3659` | 2 | 133 | no |
| R9 | [38798033562](https://github.com/tzeusy-org/butlers/actions/runs/38798033562/job/116639585275) | `7759a9da4f55a8aa9ed17674fe054033594191fe` | 2 | 112 | no |
| R10 | [38786791398](https://github.com/tzeusy-org/butlers/actions/runs/38786791398/job/116600843276) | `656d545b3f5cac40ca0419ff8e0b8e1a49c65735` | 2 | 104 | yes |

The cache column comes from the exact inventory job log's explicit cache
hit/restored or cache-not-found messages (`GET /repos/tzeusy-org/butlers/actions/jobs/<id>/logs`):
three hits, seven misses, no unknowns.
Every sampled head has the same inventory workflow block, with one
`actions/cache` step for `.venv`, an exact environment key and no restore prefixes.
A cache hit still performs mandatory frozen editable repair. Cache state is not
inferred from step duration or from a successful job.

All durations below are seconds. R1-R10 refer to the exact run/head rows above.
Percentiles use nearest rank `sorted[ceil(p * 10) - 1]`: p50 is the fifth ordered
observation and p95 is the maximum. Every row has ten observations; no missing
value is dropped to manufacture a percentile.

| Step or job metric | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9 | R10 | p50 | p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Set up job | 1 | 1 | 1 | 1 | 2 | 1 | 0 | 1 | 0 | 1 | 1 | 2 |
| Run actions/checkout@v6 | 6 | 8 | 9 | 8 | 6 | 8 | 8 | 8 | 8 | 9 | 8 | 9 |
| Set up Python 3.12 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 1 |
| Install uv | 2 | 1 | 2 | 0 | 2 | 1 | 1 | 0 | 0 | 1 | 1 | 2 |
| Compute exact environment cache key | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 1 |
| Restore advisory environment cache | 7 | 1 | 11 | 0 | 0 | 0 | 0 | 1 | 0 | 8 | 0 | 11 |
| Install dependencies | 1 | 20 | 3 | 26 | 19 | 23 | 25 | 24 | 21 | 3 | 20 | 26 |
| Restore compatible advisory file weights | 2 | 2 | 2 | 2 | 3 | 4 | 1 | 2 | 1 | 2 | 2 | 4 |
| Collect one fresh installed pytest inventory and enforce both budgets | 57 | 71 | 93 | 90 | 71 | 87 | 81 | 83 | 72 | 74 | 74 | 93 |
| Upload exact attempt inventory and assignment | 2 | 1 | 2 | 2 | 1 | 1 | 1 | 2 | 1 | 2 | 1 | 2 |
| Post Restore advisory environment cache | 0 | 6 | 0 | 8 | 5 | 6 | 6 | 7 | 5 | 0 | 5 | 8 |
| Post Set up Python 3.12 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 1 |
| Post Run actions/checkout@v6 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 1 |
| Complete job | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Inventory scheduling delay | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| Inventory job wall | 81 | 115 | 128 | 143 | 113 | 135 | 126 | 133 | 112 | 104 | 115 | 143 |

The material measured steps are fresh inventory collection (74s p50) and
mandatory dependency preparation (20s p50). Collection already uses the one fresh
installed pytest collection delivered by #4415/#4530, with both lane populations,
whole-file assignment and budget checks. It has no hosted before/after evidence
for a further bounded optimization here. Dependency preparation remains owned by
`bu-ly3lv5.5`; this measurement adds no install or cache layer.

Checkout is 8s p50, below the 10s threshold for an optimization in this task.
Tracing its actual consumers finds only `git rev-parse HEAD` and `HEAD^{tree}`
in `ci_partition.py`; source hashing reads current checkout files, and advisory
weight restoration reads the GitHub observer API/artifact. None of these
consumers needs Git history. This establishes a possible history-depth question,
not a measured material gain, so `fetch-depth: 0` is unchanged.

Hand the two missed targets to existing `bu-gvut0.5.5`: collection is the largest
step (74s/93s p50/p95), while environment preparation adds 20s/26s and whole-job
p95 is 143s. These ten successful first-attempt observations satisfy this
inventory measurement packet, but do not establish the parent twenty-run
required-path objective, a global seven-minute gate, causal speedup, or a
multi-week timeout calibration. Historical populations, child recollection,
complete-identity reconciliation, budgets, selectors, markers, privacy and
watchdogs remain unchanged.

## Dependencies and database targets

The twelve jobs (preflight, unit1–5, integration1–5 and affected) no longer launch
the unused PostgreSQL 16 service or inject its DATABASE_URL. Production URL/SSL
parsing is unchanged. DB tests provision their own pgvector PostgreSQL 17
container and pass its actual endpoint explicitly. The real DB lifecycle test
also plants an unreachable ambient endpoint, migrates/bootstrap stages its own
target, writes as the existing ordinary general role and reads its committed
sentinel through a separate checkout. It proves the server vector extension;
Python `pgvector` is not that extension.

Torch is a direct project dependency from the explicit PyTorch CPU index.
`uv lock --check` and frozen sync are mandatory. `uv tree --frozen --invert
--package torch` must succeed. Zero `nvidia-` matches in uv.lock is a separate
observation: `rg` normally exits 1 for no matches. Actual CPU tensor, embedding
normalization/dimensions/first-use and both application-image builds/sizes are
separate witnesses; a lock or ENV label cannot supply them. The retired Python
pgvector and qrcode packages have no Python runtime/entrypoint consumers. The
PostgreSQL vector extension, Pillow, Go WhatsApp QR encoder/bridge and unrelated
extras remain. Both Dockerfiles install this same frozen project without the
obsolete WhatsApp extra or UV_TORCH_BACKEND selector. (The separate Route A
meeting-prep evidence image and its launcher were deleted in bu-99t5s3.2.)

## Advisory environment and duration caches

`python3 scripts/ci_environment.py key` binds OS/architecture, Python ABI and
micro-version, uv version, lock/project/helper bytes and the dev/bytecode mode.
There are no loose restore prefixes. `prepare` accepts only this checkout's real
.venv, checks lock/project agreement and performs bounded frozen sync/bytecode compilation and reinstalls the
editable package on every hit, then validates its actual isolated interpreter
and import against this checkout/src. Invalid cached metadata rebuilds only that
job-owned directory; a top-level symlink is refused. Cache outage is advisory;
installer/import failure fails the job. Never run it against a shared venv.

`check_ci_test_shards.py timing-key` supplies an exact-compatible weekly key.
The optional CI_SHARD_TIMINGS input must have complete phase/node identity,
source/run/attempt and no more than fourteen days of age, exact files/current
Python-source+dependency topology, and an independently re-collected selected
population. Duplicate, malformed, negative/nonfinite, missing or mismatched data
retains the lexical schedule and an explicit UNKNOWN. Completion must be a JSON
boolean, and exit, selected-count and shard fields must be JSON integers;
booleans or floats cannot stand in for those integers. A passed setup requires
a call phase; a skipped setup must have no call phase. A skipped call after a
passed setup remains valid. Finite integer and float durations remain valid.
Compatible data schedules
the same files by descending total setup/call/teardown duration with lexical ties.
Pytest always receives lexical file argv: installed pytest 9.1 can orphan
directory-scoped fixtures when explicit package paths are interleaved. The
worker-visible final collection hook applies the validated schedule after
fixture discovery, marker deselection and fixture reordering. Receipts distinguish
`collection_order: lexical` from the actual advisory schedule in `files` and
`ordering`; no old receipt is restamped as current evidence.
`--dist loadfile --no-loadscope-reorder` keeps module fixtures whole and prevents
xdist's inherited item-count sort from overriding that order. Manifests and
within-file collection order remain unchanged.

The plugin writes only node hashes, repository file paths, closed phase/outcome
fields, worker resource counters, tracer names and timestamps. It records actual
selected identities before JUnit sanitization. Completeness requires agreeing
worker collections, every selected identity and exactly one teardown; early
maxfail/cancellation/absent observation leaves completeness and tail UNKNOWN.
`first_one_percent_s` and `last_five_percent_s` use controller completion times,
including wrapper preparation. `setup_complete_s` is first item setup completion,
not a fabricated job setup boundary. Combine `test_step_started_at` with actual
job metadata to measure job start to setup/first result. The job setup target is
20 seconds, first result about 25 seconds, first 1% and tail each 30 seconds;
installation about 5 seconds and container absence/under 3 seconds remain
unmet until genuine named hosted observations demonstrate them.

## Owned full coverage assurance

Full coverage runs in the nightly workflow (`.github/workflows/nightly.yml`), on
its 02:00 UTC schedule and on manual dispatch (owner decision bu-w72da2 = A; the
former `ci-coverage.yml` and its pull-request path filter are retired). Its job
names are distinct from the protected CI contexts. Each run records its actual
schedule or manual event throughout inventory, execution and coverage metadata.
No merge-group identity is borrowed to authorize a different event.

Four job stages preserve the complete proof: one fresh installed inventory and
both budgets; an explicit five-unit/six-integration covered matrix; independent
eleven-child execution reconciliation with the existing real smoke fallback;
and strict eleven-input CoverageData validation before combination, report
upload (the `ci-combined-coverage-report` artifact) or badge output. Failed,
skipped, cancelled, missing or unknown prerequisites fail the always-running
preflight/report gates. Every input keeps its current checkout, assignment, run
and attempt binding. Only a successful scheduled or manual main run can publish
the badge; dispatching the nightly workflow on a branch validates a coverage
pipeline change without updating it.

The local `ci-python-environment` composite retains the existing exact Python
identity, bounded UV installer, advisory cache key and mandatory frozen editable
repair. Unit and integration rows retain their complete 28/36-minute watchdogs;
inventory, preflight and report retain 35/27/16 minutes. These bounds are
provisional until separately observed.

Required CI runs all eleven protected queue shards without coverage instrumentation
(`CI_COVERAGE=0`), retaining the same selected tests, workers, markers, timeouts,
execution receipts, smoke reconciliation and mandatory fan-in. Full coverage
belongs to the nightly coverage jobs (`CI_COVERAGE=1`); standalone calls still
default to covered execution.

[Covered PR validation run 38672598762](https://github.com/tzeusy-org/butlers/actions/runs/38672598762)
passed all eleven covered children, independent complete reconciliation with real
smoke coverage and strict eleven-input reporting before the source cutover. It
used genuine pull_request checkout 0f29a2d99b5c46213773dac0d0a6e3e1e2d8d9bb from
branch revision bf1094a0725b8d5c4a76aeedc0163a013cb67a1d, not the later cutover head.
Maximum shard job wall times were 442s unit and 475s integration under concurrent
unrelated work. These are covered PR validation observations, not queue
before/after measurements, final-source evidence or seven-minute proof. Existing
historical M1/M2 observation obligations remain separately unearned where noted.

The weights observer currently refreshes merge-group profiles only. After the
cutover it can refresh untraced queue costs; covered profiles may age past
fourteen days and use the ordinary truthful deterministic fallback. Genuine
covered-workflow candidate refresh is deferred; no event identity is borrowed.

## Independent experiments and coverage

Ordinary/local unit defaults remain three workers, integration remains bounded
auto and local addopts remain unchanged. For a genuine CI-shaped comparison:

```sh
CI_UNIT_WORKERS=4 CI_COVERAGE=1 CI_COVERAGE_CORE=ctrace \
  uv run --no-sync python scripts/check_ci_test_shards.py run --lane unit --shard 1
```

Repeat independently for unit3/fixed4/bounded auto and ctrace/sysmon against the
same selected corpus and covered source. Only 3, 4 or auto and ctrace or sysmon
are accepted. Actual ready worker IDs, CPU allocation, peak RSS/CPU and actual
installed tracer accompany each receipt. A unsupported/fallback sysmon request
refuses rather than reporting ctrace as sysmon. The default remains 3/ctrace
until genuine complete comparable hosted/no-OOM results select another setting.
The miniature real subprocess controls prove mechanism conformance only.

`ci_shard_comparison.py before.json after.json` compares complete identity and
phase outcomes without count-only or incomplete gain claims. Its coverage
comparison normalizes complete filenames/lines/branches/contexts. At the original experiment stage, the coverage validator required ten unique
current source/attempt/lane shards and merge-group coverage remained enabled.
Those historical comparison controls remain preserved. The current adopted
five-unit/six-integration producer above requires eleven inputs in owned
coverage assurance, while protected queue execution is untraced. CI_COVERAGE
remains exactly 0/1 and append across independent jobs remains forbidden.
Worker/core comparisons and their original evidence obligations remain separate.

## Completion and rollback

Preserve all seven original outcomes and twelve folded moves; no slice is
deferred. Retain before/current source, complete original/native/assertion maps
and every enforcing survivor. Revert each reviewed ordering/cache/CPU/worker/core
unit without narrowing membership, coverage, timers or verdicts. Genuine PG17,
both image before/after, full hosted identity/coverage, ten-run hard targets and
independent/protected evidence cannot be replaced by software fixtures or source
presence. Source preparation, normal own validated sync/archive, exact final
normal/nonself/protected gates and elapsed observations have separate receipts.

## Hosted image preparation

All ordinary, covered, nightly and migration uv installers call
`python3 scripts/install_ci_uv.py`. Each attempt owns a fresh process session;
the supervisor keeps its leader PID reserved until it has killed that group,
including after a successful or failed early shell exit. The 120-second attempt
envelope reserves its final second for forced cleanup and reaping, leaving at
most 119 seconds for installation. Five attempts and four ten-second backoffs
retain the 640-second setup envelope. Cleanup failure aborts immediately without
another retry. This removes dependence on the runner's `timeout` implementation;
the subsequent frozen environment repair and real-environment checks remain
mandatory. These are execution bounds, not a diagnosis of a particular hosted
network stall or a claim of healthy setup latency.

The image-size diagnostic was a manual `image-size-diagnostic` dispatch on the
retired Migration Chain Integrity workflow, comparing the fixed original
e7b7812a3fa65c80f3f070d38ee43f7fa6474881 checkout with a dispatched SHA. That
workflow was deleted by bu-29wkwj.1 with no remaining caller, and its script,
the Route A launcher and the Route A Dockerfiles were deleted by bu-99t5s3.2
(recoverable from git history). The record below describes what it measured.

The diagnostic builds both real normal and Route A application Dockerfiles,
records actual Docker image bytes and source stamps, and runs CPU tensor,
CUDA/NVIDIA negatives, the actual embedding engine with an explicitly synthetic
offline model fixture, Go bridge help and the PostgreSQL client. The real vector
server/runtime-role/commit proof stays in its genuine hosted DB node; client
presence and an offline model fixture cannot replace that proof or trained-model
execution. Both image builds and all runtime witnesses must actually succeed.

Route A cache preparation uses the owning launcher's exact lock/Go hashes and
cache labels. Actual Docker-save config/layers are converted into local OCI
layouts; original diff IDs, actual engine config ID and cache labels are checked,
and named contexts use immutable manifest digests. The current Route A app
recipe builds with network disabled and preloaded inputs. These OCI build
receipts do **not** establish the separate launcher's RepoDigest admission,
seven-service/browser lifecycle or its database/teardown proof. That launcher
and its assertions remain unchanged. Build failures, missing inputs, identity
mismatch or cleanup failure produce categorical failure/partial receipts, not
mock build credit. No raw build/runtime errors, environment or provider text is
exported. Only the diagnostic's own disposable tags/builder are removed.

The immutable e7 Route A recipe uses unsupported variable `COPY --from`
sources. The before comparison keeps that literal recipe and every original
Git input intact. It generates a separate Dockerfile outside the before
checkout: global cache arguments and two named cache stages replace only the
three `COPY` source tokens. An exact inverse must restore the pinned original
recipe bytes. The diagnostic checks every original application/cache input
against its Git blob, rejects extra untracked inputs, and binds the original
source tree, project, lock, Go manifests and actual sealed cache labels.

With those same original inputs, the real literal build must first fail with
the source-defined variable-COPY compiler category. The subsequent before
Route A build uses the generated representation and is labelled
`representation_adapted_original`. The normal before build still uses its
literal Dockerfile. This establishes the intended before recipe only when
the actual build and runtime probes pass; it never rewrites history or claims
the literal broken recipe succeeded. The old image diagnostic's discarded
stderr leaves its precise failure cause unknown. The current launcher's
complete three-recipe validation and offline proof job stay intact. Manual
image-size and offline-proof inputs are mutually exclusive; their original
60-minute and 45-minute caps remain unchanged. The source mechanism alone proves neither actual image result nor
trained-model execution or a wall-clock gain; dated observations below credit
only their actual bounded scope.

## Source preparation readback (2026-10-09)

Root-authenticated normal37799700172 at source9b3fa6dcb728a2c661854ae450d985af0143c4ef finished with all11 official XML artifacts and zero failures/errors. The actual PG17 bootstrap/ordinary-role/vector/commit/separate-readback node passed4.678s without skip. Manual image37800940231 at that source completed all four before/current normal and RouteA builds and bounded CPU/offline-input/runtime controls in1029.347s within1800, cleanup0. Historical1690 input bodies and the reversible representation adapter remain explicitly bound; the unsupported literal-old recipe is a separately retained compiler negative. These observations establish preparation, not trained-model/provider/launcher admission, timing gain or whole delivery.

Following independent HIGH preparation review and root completion-scope approval, normal validated own archive automatically synchronized the five046–050 requirements and32 scenarios into testing. Complete public38-requirement/153-scenario bytes and all foreign native/tasks were preserved; baseline now has43 requirements/185 scenarios. No product, metric, default or watchdog changed in this native successor. Fresh final-head normal, independent/protected union and every original hard timing, ten-run and supported full worker/core obligation remain separately mandatory.

## Measured file weights across source changes

`.github/ci-test-weights.json` contains reviewed `ci-weights.v2` profiles for
`none`, `CTracer` or `SysMonitor`. The inventory job passes the same `CI_COVERAGE`
and, when overridden, `CI_COVERAGE_CORE` as its test children. Local commands
retain covered defaults; PR inventories default to untraced costs. A missing
matching profile uses deterministic fallback, never a different tracer's costs.

Each profile retains the original run/attempt/head, collection timestamp,
complete file durations and source-file SHA-256 digests. Eligibility requires
matching Python and pytest versions, dependency lock and lane selectors, with
an age of at most fourteen days. The current fresh inventory includes each
selected test file's digest. Only an unchanged file can reuse its measured cost;
changed or new files use the valid lane median, or one when no valid costs exist.
An advisory file cost cannot exceed one day, a generous ceiling above every
finite hosted shard cap. This also keeps median and shard-sum arithmetic finite;
positive but implausibly large numeric inputs receive the same fallback.
Product-source changes do not discard all costs. A fixture or implementation
change can still change an untouched test's runtime, so these are scheduling
hints, not a wall-time guarantee. The original full source/config/run/attempt
identity continues to bind inventory, assignment and every execution receipt.

The observer emits one matching profile from complete actual shard receipts.
An empty actual tracer set records `none`; mixed or unsupported tracers refuse
candidate publication. Its timestamp comes from the original inventory, so
reading an old run does not renew its cost lifetime. The inventory restores the
latest successful main observer's profile into a temporary file, retaining the
committed seed's other modes. This uses three GitHub reads, each bounded to five
seconds. It checks the observer workflow/ref, artifact ownership, matching
candidate receipt/source, runtime, timestamp, finite costs and file digests.
An absent, expired, malformed or incompatible candidate leaves the reviewed
seed; an older candidate cannot replace a newer compatible seed. Both restore
and fallback always write a usable weights file, plus a closed diagnostic
receipt. No observer or reader commits source or admits test membership.

The pipeline wiring uses its read-only Actions token and the installed Python:
Both the early required-CI inventory and the owned coverage inventory restore
after frozen environment repair, before fresh collection. Only these inventory
jobs gain `actions: read`; their restore steps alone receive `GH_TOKEN`. The
covered producer retains `CI_COVERAGE=1` and selects covered costs independently
of the required workflow's current event policy.

```sh
uv run --no-sync python scripts/ci_weight_candidates.py --restore --output "$RUNNER_TEMP/ci-weights"
uv run --no-sync python scripts/ci_partition.py --weights "$RUNNER_TEMP/ci-weights/weights.json" --output "$RUNNER_TEMP/ci-inventory"
```

The six-hour observer owns refresh; CI reads its latest successful main run.
Static seeds remain the outage fallback and can be refreshed by a reviewed PR.
A seed older than fourteen days still receives the ordinary deterministic
fallback; stale data never gets a renewed measurement timestamp.

The first two profiles were independently reconciled from all eleven actual
receipts in [PR run 38664906626](https://github.com/tzeusy-org/butlers/actions/runs/38664906626)
(`none`) and [queue run 38667022310](https://github.com/tzeusy-org/butlers/actions/runs/38667022310)
(`CTracer`). Their older inventories predate file digests: the seed digests were
read from Git blobs at each inventory's exact `origin.head`, preserving the
original artifacts and their identities. This is historical measurement
provenance, not a claim of current execution. Both seeds keep their actual
runtime and timestamp; incompatible or stale environments fall back normally.

Whole-file LPT can improve aggregate balance, but a single long file still runs
on one xdist worker. The forecast is bounded by its measured duration and by
setup, collection and runner wait. New hosted runs must demonstrate actual
latency before any hard timing target is credited.

A simulation on those two seeds gives the following largest shard sums. These
are setup/call/teardown phase seconds, not predicted job or queue wall time:

| Measured mode | Lane | Existing assignment | Weighted LPT |
| --- | --- | ---: | ---: |
| Untraced PR | Unit | 594 | 451 |
| Untraced PR | Integration | 1079 | 803 |
| Covered queue | Unit | 1446 | 793 |
| Covered queue | Integration | 1536 | 916 |

The slowest whole unit file in those samples was 139s untraced and 242s covered;
older observations were much slower. This variation is why the measured profile,
trace mode and ongoing refresh matter, and why a balanced sum is not a latency
claim.
