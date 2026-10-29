## Status and retained authority

This document proposes an unadopted Kubernetes topology extension, not a
working cutover or a selected custodian. The actual inspected source is
`77722227d9e9481c282b97dd66a58961a05927dc`. `bu-h3b7t` remains the whole feature
owner, with `bu-2z6jyb` unsigned admission and `bu-jnnxtq` socket isolation
serialized into its same proof surface. Their blocked/frozen ownership is not
transferred. CLOSED owner-A and merge policy are consumed without another vote.

The complete [existing eight requirements](../authorize-relationship-effective-time-cutover/specs/relationship-effective-time-cutover/spec.md),
[design](../authorize-relationship-effective-time-cutover/design.md),
[tasks](../authorize-relationship-effective-time-cutover/tasks.md) and
[Compose operator packet](../../../docs/operations/relationship-effective-time-cutover.md)
remain unchanged. Kubernetes extends the proof; it cannot reinterpret the
Compose v1 fields or turn its file fence into cluster admission. `rel_035`
expand and actual meeting-debrief `rel_036` remain immutable. G is selected
then-free after the actual head by the existing owner, never reserved here.

The mandatory unsigned fresh/disposable outcome, including legitimate committed
partial, interrupted bootstrap and resume, remains UNDELIVERED. A private
initial-state witness is insufficient without complete independently protected
REQ003/004/005-equivalent code/test/instance/target/fence binding. Empty rows,
missing index, caller labels/Config/x-arguments, lowered stamps or restored
scratch state do not establish provenance. No marker is selected here; ordinary
trusted bootstrap authority could suffice only after actual forgery, trigger,
ownership, regrant, restore, lifetime and race proof. Trusted administrative
historical erasure remains outside the current guarantee. Kubernetes must carry
this whole outcome, not permanently require a fresh signer receipt to evade it.

## D1. One authorized target and closed actor set

The future authorization names environment, Kubernetes cluster trust identity,
namespace UID, Helm release identity/revision, ordered chart/values/override
inputs, target full commit/tree/roster tree, immutable OCI image digests for each
executable actor, rollback target, database name/OID/cluster system identifier,
admission installation digest, fence generation and maximum maintenance window.
No endpoint hostname, namespace name, mutable image tag or label suffices.

Use a source-resolved, canonical content-blind projection of the authorized
render, not full values or secret-bearing Helm output. It includes workload
kind/name, owner references, expected replicas, command/args structural digests,
container/init/ephemeral entries, image digest, approved code-volume identity,
credential key NAMES/references, Secret/ConfigMap UID/resourceVersion and the
effective admission/controller configuration digest. Arbitrary scalar values
are hashed in the trusted collector and never emitted; unknown fields affecting
code/credentials/admission refuse until classified. A public reference digest
is not secret authenticity or permission to read a key. No site-env or BWS
helper is executed to manufacture this source proposal's render.

Compare all controller templates and realized Pod/container identities, including
nonready, Pending, terminating, stopped/crashed, completed Job and ownerless
objects. Watch resourceVersion changes and relist from a coherent fence epoch;
expired watches or list permissions lost are refusal. A Lease can serialize
cooperating invokers but cannot prevent a controller or administrator from
creating a Pod. It is not the proof of writer absence. ReplicaSet histories and
CronJob-created Jobs are independent restart capabilities even after scaling or
suspension. The complete inventory rules are in [source-inventory.md](source-inventory.md).

Each application image must be pinned by OCI digest and its complete source
archive, roster, Alembic and shipped mutator inventory verified against the
target Git objects. The chart's `GIT_SHA=image.tag` overrides the Dockerfile's
full SHA and currently may be a 12-character tag; treat both as untrusted
claims until recomputed source/image provenance matches. Mutable tags and
`uv sync --dev` in the fleet startup are explicit unresolved target-identity
discrepancies. Unknown runtime writes to executable paths, hostPath/PVC source
overlays, unpinned init tools or modified dependencies refuse. Do not infer
immutable code merely because this chart has no roster bind mount.

The wider mutator census must include direct/dynamic/helper writes, public entity
cascades, operator cleanup and historical core/Relationship replay. The current
literal guard's scope cannot prove all behavior. Central-writer-only proof,
healthy receivers, deployment ledger rows and equal writer counts are inadequate.

## D2. Fence before inventory, not after observation

Proposed phases are `inventory`, `quiescing`, `quiesced`, `migrated`, `releasing`
and terminal `released`/`aborted`; every transition is generation-bound and
durable. Persist authorization, target, protected issuer identity, controller
digest, phase and session challenge. Compare-and-swap ownership prevents two
invokers from consuming one generation. Expiry never clears a fence or releases
an old writer automatically. A killed coordinator leaves a held, discoverable
fence; resume independently rereads database and control-plane state.

The future deployment path must enforce admission before any create/update that
can start a credentialed actor and before its first target DB connection. This
includes launcher/make targets, controller-created replacement Pods, migrations
initContainers, fleet startup, connector/OAuth startup, CronJob/manual Job,
ad-hoc migration/provision/restore, restart/rollback and runtime descendants.
An unavailable fence/admission controller refuses. RBAC, admission coverage and
process-start binding must be proved against the actual declared roles and API
operations. An initContainer check followed by an unfenced application reconnect
does not maintain exclusion. A copied label or writable ConfigMap cannot confer
the collector's special migration/release authority.

Supported preparation holds persistent admission first, resolves and proves all
actors, runs its own protected fixed tests while the normal fleet is still up,
then records the exact original desired replica/schedule state. It suspends
schedules, disables recreation, quiesces and removes all credentialed Pods/Jobs
and confirms their actual processes and target connections ended. It also
excludes the inventoried external/host/other-namespace clients; if their restart
capability cannot be fenced, preparation refuses. Controller deletion with
orphaned Pods, a force deletion whose process still runs on an unreachable node,
partitioned/killed node, unknown autoscaler or alternate GitOps reconciler is
not absence. No grace-time assumption clears such uncertainty.

Preparation rechecks target image/source/render, object identities, controller
admission, database target and complete absence after tests and quiescence before
issuing proof. Any drift retains the fence and produces a content-blind refusal.
Test failure before destructive quiescence may restore only through a verified
pre-G abort of the same target; no blanket trap clears partially mutated state.

## D3. New topology protocol, same pre-DDL exclusion

Propose a distinct closed wire tag
`butlers.relationship-temporal-cutover-kubernetes-receipt/v1`. It is not a
Compose receipt with fields omitted. Future shared parser dispatch must explicitly
admit this tag only after exact topology/custody adoption, preserve every old
Compose accepted/refused input, and reject cross-topology replay. The document
uses the same JCS/Ed25519, duplicate/unknown-member refusal, canonical base64url,
whole-second UTC, dedicated signer namespace/window, purpose, content-blind
projection, database-time checks and at-most-900-second expiry as REQ001/002.
All signature/key/window/purpose checks remain mandatory for existing real data.

The proposed document binds: authorization and purpose; exact cluster/namespace/
release identity; full target Git/tree/roster and image/code digests; complete
expected and actual actor/controller sets; resolved projection and source
mutator digest; wrapper-produced fixed-node/log/test receipt; DB target; protected
fence issuer/id/generation/phase; issue/expiry and signer id/signature. Actor
entries bind kind/name/UID, owner chain, generation, template/source digest,
container name/OCI digest/container runtime id, actual restart/termination state
and code mounts. All sets are canonically sorted and whole bodies hashed, with
explicitly authorized optional absence, never a caller-selected subset.
Names are restricted to authorized infrastructural names; raw commands/env,
SQL, fact/contact/entity identifiers, log text, credentials and key material
cannot enter evidence. Signature and file/transport custody are independent.
Regular non-symlink bounded nonblocking reads and atomic owned publication must
preserve complete input/output inverses; FIFO/symlink receipts or failure
summaries cannot block or overwrite unrelated files.

G executes exactly once using the existing owner's one registered online
admission/controller path. Before any DDL, on its actual same transaction, it
validates source/code/target/binding, locks `relationship.entity_facts` with
`ACCESS EXCLUSIVE NOWAIT`, verifies full other-client absence and visibility,
validates both index definitions/occurrence validity, and compares the actual
protected quiesced fence generation to the applicable complete proof. A client
connecting after the session check cannot touch the table before commit; the
lifecycle fence must still prevent old code reconnecting after commit. Snapshot,
Lease, advisory lock or network policy alone is insufficient. No grants, role
fallback, hidden migration directory or test bypass compensates for inadequate
catalog/activity visibility. Index drop, consume state and revision stamp commit
atomically; every refusal preserves facts/evidence/indexes/stamp.

Automatic init/fleet/CLI/raw/helper paths retain the ordinary registered gated
ceiling. Existing stamped pre-G databases stop at G's actual predecessor, and
G/descendants may advance. Offline G refuses. No duplicate G or Kubernetes-only
schema chain is introduced. The mandatory unsigned path substitutes only its
independently protected proof binding/provenance for signature/custody, retains
every complete claim, and remains unimplemented until the same owner's whole
fresh/disposable/resume proof is delivered.

## D4. Custody and test-isolation alternatives, none selected

| Candidate | Concrete trust and feasibility conditions | Adoption delta / failure |
| --- | --- | --- |
| Existing trusted host-root fixed collector, Kubernetes read/admission channel | Pinned installed collector; dedicated `rtc-*` signer outside workload reach; minimal API authority bound to the exact environment; no generic sign verb or operator evidence. Public verifier/proof projection goes into only migration/release consumers. Host-root test sandbox or disposable VM runs isolated fixed tests with offline immutable inputs. | Must adopt exact Kubernetes API authority and admission topology plus the dedicated signer artifact. Current Compose sudoers/file path does not supply them. No host key, API credential or cluster installation provisioned here. |
| Dedicated control-plane collector/signer process | Dedicated file-backed process-bound deployment-control key, outside fleet/API/model/test mounts, logs and generic Secrets APIs; explicit RBAC/admission/rotation/restart/descendant isolation and independent evidence collection. | A namespace Secret plus ordinary ServiceAccount is not adequate custody, especially with privileged API Pods and cluster-admin reach. A new process/custody boundary needs concrete adoption and proof; not selected or silently authorized. |
| External isolated runner/controller | Authenticated collector-to-admission channel; dedicated signer and pinned source/OCI/test inputs; deny live target, Kubernetes API, keys, host sockets and collector artifacts to test children; safe atomic result capture. | New transport/trust root must be reviewed and adopted; hosted CI result handed to a signer remains prohibited. No external service chosen. |

For all alternatives the collector runs the exact required rel035/G/static and
every adopted task3.5 conformance node itself, then an independent outer
collector-to-binding-to-G/resume proof demonstrates provenance without recursive
self-selection. Synthetic inner keys/claims prove conformance only. Operator
logs, hosted CI, healthy Pods or an injected receipt do not prove collection.
Use frozen offline `git archive`/OCI/dependency/test-PG inputs and exact node
population, bounded gate verdict, log digest and cleanup as in REQ004.

A fresh Kubernetes namespace alone is not isolation: it shares a cluster control
plane, network/storage/node interfaces and can inherit ServiceAccount/Secret
authority. Test children must be unable to reach live DB over TCP AND PostgreSQL
Unix sockets, cluster API/token, host/container runtime sockets, signer, fence,
receipt, log/output paths or another invocation. Same-runtime named negative
probes and planted reachable positive companions must demonstrate each boundary;
the whole descendant domain and private test DB/runtime must terminate and
leave no residue. `bu-jnnxtq` joins this exact runtime, not a separate owner.
No test network/privilege/daemon fallback is permitted when isolation is unavailable.

## D5. Release, interruption, restore and rollback

After verified atomic G commit, keep admission held and move to `migrated`, then
`releasing`. The fixed controller invokes only the exact adopted launcher and
resolved immutable templates, without rebuild/mutable tag. Its special authority
is invocation/phase/target-bound and not a replayable service-account label.
Verify every realized controller/Pod/init container, source/image/roster, restart
generation and connection, start after commit, exact expected set and no extra
credentialed actor. Restore the exact recorded desired replicas/schedules only
for that target; keep the fence held on any partial release/drift. A complete
verified target set precedes release of normal admission. No healthy receiver
stands in for identity or closure.

Crash before quiescence, after individual actor removal, after signing, during
DDL or after commit/release requires durable phase and source/DB reread. A lost
receipt expires without relaxing the fence; obtain newly collected proof, not a
timestamp edit. A lost DDL reply is an uncertain outcome: read actual stamp,
consume state and both indexes from a fresh authorized connection before choosing
abort versus forward release. A second invoker must lose CAS or refuse, never
overwrite generation, replay proof or start its own cohort. Quiescence failure
restores only through verified pre-G abort, never unexamined rollback commands.

Before G commits, abort releases the same compatible transition target through
complete release checks. After G commits, ordinary recovery is forward release.
Before the first committed temporal-bearing value in ANY validity or duplicate
active SPO, a NEW `rollback_before_first_temporal_write` authorization/proof and
held fence permit the original index recreation under the same transaction,
NOWAIT/session proof and then named compatible rollback release. After that first
write, downgrade/old-image release refuse; never flatten/delete/supersede/collapse
history to make the legacy index buildable. Purpose cannot cross-replay.

Restore or clone changes database identity/provenance and invalidates the old
receipt/fence; ordinary dump replay does not mint birth or trusted proof, refresh
lifetime or reset the first-write boundary. Legitimate truly disposable committed
resume still requires the existing owner's full protected source-born mechanism,
not a caller designation or a destructive fallback. Bootstrap replay/roles and
backup omission/definer ownership contracts remain intact. Compose restore-drill
executor has no Kubernetes equivalent today, and this proposal supplies none.

## D6. Gates and source ownership

Source-only proposal can merge after genuine exact-head security/deployment/
PostgreSQL design review, native composition, link and guards checks. That is
not contract adoption or implementation. Later adoption must identify the exact
changed Kubernetes protocol, admission and custody alternative plus its immutable
source artifact; CLOSED A/merge policy are not asked again. `bu-h3b7t` then owns
one cohesive implementation with exact overlap serialization in
[verification-and-allocation.md](verification-and-allocation.md), leaving current
owners and launcher holds intact. Source delivery requires genuine current
registered real-seam proof, current normal, full protected queue and squash.

Separate operational release must identify exact environment/cluster/namespace/
release/DB, authorization id, target/rollback images, optional absences and
maintenance window; only after signer/admission/test-runtime provisioning and
all actual feasibility and data-preservation proof. Neither a docs merge nor
CI authorizes those operations or closes the original whole feature.
