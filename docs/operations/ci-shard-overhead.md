# CI shard overhead and evidence

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
there is no independent healthy inventory p95 yet, and all existing job caps and
UV recovery reserves remain.

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
obsolete WhatsApp extra or UV_TORCH_BACKEND selector. Route A retains its
required digest-bound offline dependency cache and its existing admission gates.

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

The `CI coverage assurance` workflow in `.github/workflows/ci-coverage.yml`
executes daily at 19:37 UTC, supports manual invocation, and validates coverage
pipeline changes through an explicit PR path filter. Its job names are distinct
from the protected CI contexts. Each run records its actual schedule, manual or
PR event throughout inventory, execution and coverage metadata. No merge-group
identity is borrowed to authorize a different event.

Four job stages preserve the complete proof: one fresh installed inventory and
both budgets; an explicit five-unit/six-integration covered matrix; independent
eleven-child execution reconciliation with the existing real smoke fallback;
and strict eleven-input CoverageData validation before combination, report
upload or badge output. Failed, skipped, cancelled, missing or unknown
prerequisites fail the always-running preflight/report gates. Every input keeps
its current checkout, assignment, run and attempt binding. A genuine pipeline
PR produces a full report but cannot update the main badge. Only a successful
scheduled or manual main run can publish the badge.

The local `ci-python-environment` composite retains the existing exact Python
identity, bounded UV installer, advisory cache key and mandatory frozen editable
repair. Test consumers request the same file-access observer. Unit and
integration rows retain their complete 28/36-minute watchdogs; inventory,
preflight and report retain 35/27/16 minutes. New-workflow bounds are provisional
until separately observed. The covered matrix has eleven concurrent children;
its concurrency group does not cancel the protected CI run. The one validation
PR may therefore consume extra runner capacity and is not a natural timing
sample for the queue optimization.

Replacement validation is staged. The queue and its existing reporter remain
covered while the owned workflow obtains an actual successful eleven-input
hosted result and independent source review. Only then may a subsequent change
set the queue to untraced execution and retire its advisory reporter. Local
standalone calls retain covered defaults throughout. No replacement hosted
success, cadence cutover or wall-clock gain is claimed by this source addition.

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
comparison normalizes complete filenames/lines/branches/contexts. The existing
coverage validator still requires ten unique current source/attempt/lane shards,
rejects population/identity drift and governs report/upload/badge. CI_COVERAGE
remains exactly 0/1, ordinary PRs remain uncovered, merge-group remains covered,
and append across independent jobs remains forbidden. Nightly and other
covered consumers keep their own existing settings; this issue does not adopt
the future matrix/partition/planner/sixth-shard work.

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

The existing Migration Chain Integrity workflow has an optional boolean
`image-size-diagnostic` dispatch input, default false. Push and ordinary manual
migration checks retain their original job/command. An explicit true dispatch
runs only the bounded diagnostic job at the exact dispatched SHA, comparing the
fixed original e7b7812a3fa65c80f3f070d38ee43f7fa6474881 checkout with that SHA.
Root dispatches it after source review. It never pushes an image, uses registry
credentials, runs a full pytest suite, changes a deployment or invokes a model
provider. The original 25 caps remain; its separate provisional 60-minute cap
covers the existing two 640-second installer envelopes, a 1800-second diagnostic
and 520 seconds of checkout/cleanup/upload reserve. No healthy p95 is claimed.

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
