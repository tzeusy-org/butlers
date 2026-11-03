# Future implementation and verification allocation

This is one implementation plan for the existing whole feature owner
`bu-h3b7t`, not a set of new parallel owners. `bu-2z6jyb` stays the complete
unsigned terminal, and `bu-jnnxtq` joins its isolated runtime. No file below is
implemented or released by this proposal. Exact shared hunks must be allocated
by the coordinator after changed-contract adoption and fresh ownership checks;
existing launcher/backup/capture and active source holds remain intact.

## Cohesive source slices

| Slice / dependency | Future source home | Actual seam and test allocation |
| --- | --- | --- |
| Shared receipt/fence/binding codec, G and unsigned state machine first | Existing planned `src/butlers/relationship_temporal_cutover.py`; `src/butlers/migrations.py`, `alembic/env.py`, `src/butlers/cli.py`; then-free roster G | Extend planned `tests/core/test_relationship_temporal_cutover_receipt.py` with the topology discriminator and every old Compose case; extend existing `tests/config/test_migrations.py` and `tests/daemon/test_butler_migrations.py`; one actual G roster migration species. Do not fork G or add a Kubernetes freshness flag. |
| Read-only Kubernetes collector and exact projection | Proposed NEW `scripts/verify_relationship_temporal_cutover_kubernetes.py`, coupled to existing planned shipped mutator inventory and codec | One new `tests/scripts/test_relationship_temporal_cutover_kubernetes.py` owns fixture API transports and collector-to-admission/release orchestration. Complete controller/Pod/Job/credential closure, real image/source comparisons and refusal/healthy companions; transport models alone do not prove RBAC or cluster enforcement. |
| Adopted custody/controller plus persistent admission | Exact installed collector/controller/installer paths chosen only AFTER custody adoption; shared planned wrapper may gain explicitly versioned Kubernetes verbs | Extend the same new orchestration species and existing planned wrapper tests, then isolated disposable API-server/k3s tests. Negative actual API operations under declared ServiceAccounts must prove denied create/update/exec/ephemeral-container/reconnect; root/admin residual is explicit. No product runtime gets a signing key. |
| Supported lifecycle entrypoints | `scripts/k8s/deploy-dev.sh`, `deploy/helm/butlers/makefile`, `templates/_helpers.tpl`, `dashboard-api.yaml`, `butlers-up.yaml`, `connectors.yaml`, `cronjobs.yaml`, `beads-export.yaml` | Extend existing `tests/scripts/test_k8s_deploy_dev_script.py`, chart tests and the new collector orchestration species. Actual fake executable captures prove launcher pre-act refusal; actual controller/Pod restarts in disposable k3s prove automatic recreation, init/start admission and complete process termination. A text search or Helm render is not that proof. |
| Immutable image/source/dependency target | `scripts/k8s/build-push.sh`, `Dockerfile`, chart helpers/values and runtime fleet command | Resolve full SHA versus projected short tag and runtime `uv sync --dev` before acceptance. Offline OCI/source extraction, mutable-tag/code-overlay/dependency neutralization and restored controls; no generic trust in a baked label. Keep existing build/roster/lock semantics unless exact adoption allocates a change. |
| Isolated self-run tests | Existing planned `scripts/relationship-temporal-cutover-test-runtime.sh` and its planned test species, with `bu-jnnxtq` | Whole fixed task3.5 conformance population plus distinct nonrecursive outer collection/G/resume proof. Actual TCP/AF_UNIX/socket/API/key/output/cross-invocation denial with planted reachable sentinels; full descendant and private-runtime teardown. A disposable VM is eligible only with equivalent actual probes. |
| Restore/bootstrap and operator packet | Existing `scripts/init-db.sql`, `pg_restore.sh`, `pg_verify_restore.sh` only IF separately allocated actual admission requires a hunk; existing operator packet gains Kubernetes section after adopted implementation | Preserve bootstrap/definer ownership/backup coverage and all old controls; real ordinary-role regrant/restore/lifetime proof. No privileged role repair, new grant or restore-drill Kubernetes implementation is inferred. Source-only proposal currently leaves these files unchanged. |

Current native Compose files are reference authority, not proposed duplicate
edits. Future shared codec/admission changes require one serial whole-body
amendment in their existing home; do not copy eight MODIFIED requirements into
another competing carrier. This new ADDED009 composes only after the parent
eight-requirement capability exists; no unsupported archive order or real
baseline application is authorized here.

## Positioned failure and concurrency matrix

Every row needs an executing positive, unsafe neutralization that fails for the
named invariant, restored enforcement, and durable separate-readback where SQL
is involved. Source parsing, mocks, hash equality and unavailable setup do not
earn a row. Proposed codes are future taxonomy, not existing runtime outputs.

| Boundary | Planted fault / exact comparison | Required outcome and recovery |
| --- | --- | --- |
| Complete discovery | Correct target plus extra ownerless/foreign-labeled Pod, init DB client, manual Job, old ReplicaSet, external/host connection; omitted kind or lost watch/list permission | Refuse `actor_inventory_incomplete`/`actor_extra`; planted healthy authorized set must succeed. Unknown Secret/envFrom authority cannot be classified away. |
| Code identity | Same claimed SHA with wrong OCI bytes, roster/volume overlay, short overridden SHA, unpinned init or runtime dependency change | Refuse `target_code_mismatch`; recomputed immutable source restored positive. |
| Admission before inventory | Real create/update/start request after held fence; mutate the actual admission/first-connect seam to allow it | Unsafe control reaches actor/client and fails absence/exclusion assertion; restored denies without lifecycle act. |
| Automatic restart | Kill actual application container/Pod, liveness replacement, rollout restart and old controller reschedule; CronJob tick/manual Job while held | No old/restarted credentialed client; do not count API deletion as terminated process. Partitioned node remains refused, not elapsed-time PASS. |
| Controller drift | Change desired replicas, template, credential-reference UID/RV, autoscaler or GitOps reconciliation during tests/quiescence | CAS/watch drift refusal with fence retained; exact original desired state saved for safe abort. |
| Concurrent prepare/migrate | Two barrier-synchronized invokers use same target/generation; revoke actual lock/admission guard in control | One authentic generation/consume succeeds at most; loser has no DDL/start and cannot clear the winner fence. |
| Stale/forged/foreign proof | Bad signature/key window/purpose, duplicate field, expiry/future time, mixed code/cluster/namespace/release/DB/fence; old Compose tag replay | Actual parser/G refuses before DDL; valid same-purpose complete current proof reaches G. Retain all existing Compose positives/refusals. |
| Input/output filesystem | FIFO or symlink receipt/error destination, equal-content foreign inode replacement, write/cleanup interruption | Bounded refusal without hang/clobber; exact owned regular path restored; keep meaningful error/status and rollback of owned artifacts. |
| Test isolation | Plant reachable host runtime socket, DB TCP and AF_UNIX socket, API token, key, output and another invocation | Same-runtime attempts denied; removing each boundary proves reachable positive. No test/key provisioning uses live host paths. |
| Test provenance | Omit a fixed collected node, substitute source/locked artifact, pass operator log/CI receipt, keep a child/runtime residue | Collector refuses and does not sign; real full fixed population/PASS and cleanup restored. Synthetic inner claims do not count as outer collection. |
| Quiesce interruption | Stop after each actor/controller removal; process remains after forced API deletion; reconnecting host client | Durable phase remains held; independently verify and resume or pre-G abort exact target. Never clear partial state in an exit trap. |
| Pre-DDL race | Ordinary real migration login versus second fact lock/session; late writer crosses check-to-DDL barrier | NOWAIT or other-client refusal before DDL, late write waits through commit; separate acquisition verifies every fact/evidence/index/stamp unchanged on refusal. |
| DDL failure / lost reply | Fail after consume but before drop/stamp/commit; kill coordinator after commit before phase update | Atomic rollback preserves all state, or independent committed-state read chooses forward release. UNKNOWN cannot choose old-image rollback. |
| Exact release | Start correct target plus wrong init/source/image/extra actor or only partial expected set | Fence retained and unsafe start stopped; only complete verified postcommit target closes normal admission. |
| Restore | Actual bootstrapped restored/clone target and manipulated stamp/index/empty rows with replayed proof | Old authority invalid; actual roles, definer ownership, grant replay and history controls retained. No source-only provenance claim. |
| Before/after first temporal write | Complete fenced real G/downgrade/reupgrade using new rollback proof; any-validity temporal value and duplicate active SPO | Only preboundary original index recreation plus named rollback release; later refuse with all history intact. Wrong-purpose replay refuses. |
| Unsigned pristine and committed resume | Actual trusted target creation, complete collector nonreceipt binding and interrupted/committed bootstrap; planted caller label/Config/stamp/erase/restore | Legitimate full unsigned terminal eventually succeeds without signature, every impostor refuses; initial witness or permanently refused resume earns no completion. |
| Unsupported / privileged bypass | Raw Helm/kubectl/exec, alternate chart/host process, trusted cluster/DB administrator erasure | Explicit unsupported/unverifiable refusal and trusted-admin residual; no promised prevention beyond adopted authority. |

## Future test deltas and delivery

Current proposal Tests: +0 ~0 -0. No pytest behavior test is needed for new
documentation. Future integrated planning estimate is ONE Kubernetes orchestration
species and extension of roughly five existing/planned owning species; function
and parameter deltas remain UNKNOWN until actual collection. This is not a
quota, permission to omit a case, fixed `+a ~b -c` receipt, or budget exception.
The separate existing G/test-runtime species remain required, not counted again
as a newly authorized parallel owner. Register every actual new/moved test file
exactly once in the then-current 5+6 CI shard lanes and report measured growth.

Future code starts exact affected node, then owning file, dirty `make test-plan`,
explicit roster scope and `tests/ roster/` collection for shared topology. Shared
migrations, role/ACL, registry/startup and tool changes require real migrated
PostgreSQL, actual ordinary migration/runtime roles and source-bound controller
integration, not a guessed subset. Run static/format, installed native strict
and authoring trace separately, guards, complete shard/inventory/budgets,
meaningful controls and exact normal CI, distinct HIGH review, protected union
and actual squash. Hosted success is repository delivery evidence; it does not
replace the collector's isolated self-run evidence for a live cutover.

## Adoption and operations are separate

1. Genuine review of THIS exact source proposal and complete private native
   composition. Source PR/normal/protected/squash may deliver the proposal only.
2. Owner adopts the exact Kubernetes changed-contract artifact: wire version,
   collector API/admission authority, selected custody/test-isolation alternative
   and explicit privileged residual. Closed fresh/merge decisions stay closed.
3. Coordinator allocates serial shared hunks to the existing feature owner;
   actual registered source/SQL/role/isolated-provenance proof and delivery occur.
   Full signed/unsigned/disposable/resume and rollback criteria stay open until
   evidenced, without source-only terminal closure.
4. Separate trusted provisioning/install stage establishes the adopted signer,
   admission roles/runtime/cache and probes. This proposal performs none.
5. Separate exact-environment authorization supplies actual identities, target
   and rollback digests, optional absences, authorization id and maximum window.
   Only then may the fixed controller perform the actual controlled cutover.

No step is pre-earned by a reserved identifier, read-only census, design choice,
model control, named parser PASS or proposal merge.
