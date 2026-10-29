# Relationship Effective-Time Cutover Packet

> **Status:** unimplemented source contract. The verifier, wrapper, fence checks, and symbolic G migration named below
> do **not** exist yet. Nothing on this page can be executed today, and nothing here authorizes a
> key, host change, deployment, container stop or start, migration, or temporal activation.
> **Contract:** [`authorize-relationship-effective-time-cutover`](../../openspec/changes/authorize-relationship-effective-time-cutover/design.md)
> (receipt and fence schemas, failure taxonomy, file ownership) on top of
> [`relationship-fact-effective-time`](../../openspec/changes/relationship-fact-effective-time/design.md).
> **Scope:** Draft Docker Compose contract, project `butlers` (prod) or `butlers-dev` (dev). No other orchestrator.

The current live dev fleet runs on k3s in namespace `butlers-dev`; this packet's
`dev-launcher` row is a proposed Compose invocation, not the current dev deployment
path. There is **no Kubernetes cutover equivalent** in the governing contract or
implementation. Its Compose container inventory, restart-policy fence and signed
receipt cannot be replaced with `kubectl` commands. A Kubernetes path needs a
separately designed and implemented fence/receipt contract before temporal
activation can be described as executable. Normal image deployment through
[Kubernetes Deployment](kubernetes-deployment.md#deploy) does not supply that fence
or authorize this cutover.

Actual `rel_036` is the immutable meeting-debrief migration. G is a then-free cutover revision
after the actual then-current head, not a reserved number or replacement for `rel_036`.

## What the cutover does

`rel_035` added effective-time columns and the occurrence index but kept
`relationship.uq_ef_spo_active`. While that index exists, every Relationship mutator refuses temporal
intent with `temporal_cutover_pending`. G drops only that index, which is the sole signal that
enables temporal assertions, corrections, and repeated periods.

Old code must be gone first: pre-transition writers and unfenced mutators collapse or overwrite
occurrences once the index is absent. The cutover therefore proves the whole fleet is the exact
target, fences every supported start path, and holds a database exclusion while the index drops.

## Why the image id is not enough

`roster/` is bind-mounted from the host checkout into `butlers-up`, `dashboard-api`, and
`migrations`. Most Relationship mutators live there. A process's code is its image **plus** the
checkout's `roster/` tree, so every check below binds both. Hotreload also mounts `src/` and reloads
live, so the proposed verifier refuses a hotreload stack. The draft Compose
`dev-launcher` row therefore requires `--no-hotreload`; this is not an instruction
to replace the k3s dev fleet with Compose. The existing launcher guard refuses a
Compose fleet competing for a Kubernetes-owned database.

## Preconditions

All must hold before anyone runs step 1:

1. The owner has adopted the dedicated signer (design "Owner decisions"), and the key, keyring,
   wrapper, and sudoers rule are installed by a separate host act. The same host act prepares the
   isolated test runtime: account `butlers-rtc-test` in no `docker` or `sudo` group, its own rootless
   Docker daemon, and a read-only offline dependency cache for the target `uv.lock`.
2. CLOSED `bu-ftd491` answer A (2026-10-04) requires a non-spoofable unsigned genuinely
   fresh/disposable path while keeping receipts mandatory for existing real-data targets. It is not
   an unanswered permission question. Complete unsigned proof binding and legitimate disposable
   interruption/committed/bootstrap/resume remain UNDELIVERED; the current partial candidate below
   cannot authorize migration. No automatic retirement or lighter real-data path is inferred.
3. The target SHA contains the verifier, wrapper, fence checks, G, and the
   `relationship-fact-effective-time` task 3.5 real-PostgreSQL scenarios.
4. CLOSED `bu-ldcp5f` and PR #4317 have already applied the no-effective-time merge carve-out.
   The actual inventory digest must attest the complete adopted behavior, with discrepancies fixed
   through their owning packets; the closed decision is not requested again.
5. A live authorization names: authorization id (`rtc-YYYYMMDD-xxxxxxxx`), environment, one
   supported invocation row and its flags (below), target SHA, target image id, rollback image id
   (retained), optional-absent services, and maximum window.

## Fresh/disposable admission and interruption recovery: not executable

The smallest source candidate derives a private initial-state witness from the actual online
database/physical connection/backend/transaction/schema/G invocation before Relationship revisions
and its own schema/version preparation. It distinguishes ordinary empty managed schemas and
unrelated core/module bootstrap from applicable public/schema-scoped Relationship stamps and the
reviewed historical footprint. Missing visibility, ambiguous schemas or existing Relationship
history deny it. A caller `fresh`/`disposable` flag, Config/x-argument, schema choice, absent index or
empty rows cannot substitute. It cannot survive consumption, commit, rollback, reconnect, another
target or a reused Config/next-chain invocation. G would independently recheck all-validity
emptiness and both expected valid indexes under required activity exclusion and `ACCESS EXCLUSIVE
NOWAIT`; a rel_028 import revokes emptiness without deleting legacy input.

This witness proves only database state. REQ003/004/005 retain complete independently protected
source/test/instance/target/fence obligations, and REQ005 matches a quiesced fence to a receipt.
There is no non-receipt binding route. A witness alone must therefore refuse unsigned G. Neither
raw Alembic, current migrated helpers nor daemon topology supplies the missing proof. Current
separate chain connections and committed schema preflight must be accounted for in actual future
integration; daemon pools exist before migration, so no safe exclusion or automatic drain is
assumed. The complete unsigned source-owned proof channel remains mandatory UNDELIVERED.

A fully rolled-back from-base traversal may reread actual catalog state and derive a new partial
witness, never replay the old one; missing full proof still denies cutover. Legitimate genuinely
disposable interrupted/committed/bootstrap/resume targets must eventually advance unsigned using
independent target/disposal provenance plus complete non-receipt proof. That obligation is also
mandatory UNDELIVERED, not permanently receipt-bearing. Current stamped-empty, restored,
DELETE/TRUNCATE, downgrade, stamp/index edits and nominal disposable labels cannot acquire partial
fresh authority. Temporary safe fallback is the ordinary receipt route or separately authorized
new-target recreation. This page does not authorize recreation, dropping data or automatic cleanup;
source delivery cannot close the original runtime outcome.

CLOSED A contemplated a non-spoofable marker. A viable source-owned target-bound marker may reuse
existing trusted bootstrap/migration/provisioning authority if actual ownership, ordinary-role
DML/TRIGGER forgery, bootstrap regrants, restore/lifetime, race and restart controls prove it safe.
Forced RLS alone does not prevent a runtime-planted trigger executing during a later trusted update.
No marker/binding is selected or implemented. A genuine new actor, privilege, custody or historical
guarantee outside existing doctrine needs its exact changed-boundary decision; durability by itself
does not require repeat approval. Current catalogs cannot distinguish trusted owner/migration-role
erasure of every footprint or a manipulated restore; that residual remains explicit.

The existing bounded repository implementation release is retained. Actual gate, receipt and
admission work stays cohesive with `bu-h3b7t`; `bu-jnnxtq` joins its same isolated-runtime surface.
Foreign launcher ownership and the unadopted `bu-0kf2fd` Kubernetes proposal require serial source
allocation, not implied transfer or live authority. The signed legacy procedure below retains all
its future operational prerequisites and cannot be executed from this source proposal.

## Supported invocations

The proposed Compose cutover covers exactly the services of one named row. The wrapper resolves
the row from its own launcher at the target SHA and refuses `compose_invocation_mismatch` on any difference, including a
container started with another file set or profile.

| Row | Launcher | Files | Project | Env file | Profiles |
| --- | --- | --- | --- | --- | --- |
| `prod-deploy` | `butlers deploy` | `docker-compose.yml`, `docker-compose.restore-drill.yml` | `butlers` | `.env.prod` | authorized `DeployConfig.profiles`, default none |
| `prod-launcher` | `scripts/compose.sh --prod` [`--observability`] [`--audio`] | `docker-compose.yml`, `docker-compose.restore-drill.yml` | `butlers` | `.env.prod` | `dev` (+ `observability`, `audio`) |
| `dev-launcher` | `scripts/compose.sh --no-hotreload` [`--with-restore-drill`] [`--observability`] [`--audio`] | `docker-compose.yml` (+ `docker-compose.restore-drill.yml`) | `butlers-dev` | `.env.dev` | `dev` (+ `observability`, `audio`) |

Every service the credential rule selects is stopped and removed for the window: any PostgreSQL or
libpq environment key, any `*_DB_HOST`-style or `RESTORE_DRILL_EXECUTOR_`/`RESTORE_DRILL_PROXY_`
key, a database-credential secret, the `db` network, or any `env_file`. That includes `backup-cron`
and, when the row includes it, the restore-drill executor and its proxy.

## Procedure

Each verb is a fixed wrapper entry point run as
`sudo -n /usr/local/libexec/butlers-relationship-temporal-cutover <verb> --authorization <id>`.

1. **Deploy the target normally** through the named row's launcher at the target SHA. The gated ceiling stops an existing pre-cutover Relationship chain at G's actual predecessor, so the fleet
   runs target code with the legacy index still present. Confirm the chain reports
   `temporal_cutover_pending`.
2. **Prepare** (`--prepare-v1`). The wrapper locks, sets the fence, verifies the checkout, resolves
   the row and hashes its configuration, and **runs the required tests itself** in the isolated test
   runtime: `butlers-rtc-test`'s own rootless daemon inside a transient service with no network, no
   access to `/etc/butlers`, `/var/lib/butlers`, or the host Docker socket, the source from a
   `git archive` export, the target image and pinned test PostgreSQL image loaded by id or digest,
   and dependencies from `uv sync --frozen --offline`. Negative probes (host socket, signing key, log
   path, database endpoint) must all fail first. The fleet is still up during the tests. After
   quiesce, the wrapper re-verifies checkout, image, and row immediately before signing. It then
   reads the image and inventory digests and database target and inventories every credentialed
   container. Any mismatch or a non-`PASS` verdict aborts and clears the fence with nothing touched.
   There is no operator test step: do not run or supply a `pytest_gate.py` log, test receipt, or CI
   result; no wrapper verb accepts one. Otherwise it sets each container's
   restart policy to `no`, stops it, proves it stopped, removes it, proves none remain, sets the fence
   to `quiesced`, and writes the signed receipt. Expiry is 15 minutes from here.
3. **Migrate** (`--migrate-v1`). The wrapper runs
   `butlers db relationship-temporal-cutover --receipt <path>` in a fresh target-image `migrations`
   container with the receipt, keyring, and fence mounted read-only. G takes a `NOWAIT`
   access-exclusive lock, proves no other database session, verifies the receipt and fence, checks
   both indexes, and drops only `uq_ef_spo_active`. Any failure changes nothing.
4. **Release** (`--release-v1`). The wrapper starts the row's services through the row's own launcher
   in fence-release mode (no build, restore-drill preparation included, `butlers-app` pinned to the
   target image id), verifies every container against the instance rules and a start time after the
   G commit, proves no extra container, and removes the fence. A mismatch keeps the fence held.

**Abort** (`--abort-v1`) is allowed only while the Relationship chain is still at G's actual predecessor. It releases
the same target image through the step 4 checks and clears the fence. After G commits, the only
path is forward through release.

## During the window

While the fence is held:

- `scripts/compose.sh` and `butlers deploy` refuse `cutover_fence_held`. Do not work around them.
- Do not use `docker start`, `docker restart`, `docker run`, raw `docker compose`, GUI container
  tools, `compose.sh` from any other checkout, or host `uv run` against this database.
- Do not edit, pull, or check out anything in the deploy checkout.

**Residual risk.** Those unsupported paths bypass the refusal layer. The wrapper removes old
containers and G refuses any live session, so a bypass that is running at the DDL check is
caught. Nothing stops a root-capable operator from starting old non-central code after release, and
that code can collapse temporal occurrences. The prohibition on those paths continues after the
window.

## Abort matrix

| Condition | Code | Effect |
| --- | --- | --- |
| Checkout not at target, wrong roster tree, or dirty | `checkout_mismatch` / `checkout_dirty` | Abort at prepare; nothing touched. |
| Hotreload container present | `hotreload_unverifiable` | Abort at prepare. |
| Non-target image or roster tree on any container | `instance_unknown_image` / `instance_mixed` | Abort at prepare. |
| Launcher resolution or container labels differ from the named row | `compose_invocation_mismatch` | Abort at prepare. |
| Extra or missing credentialed service | `instance_extra` / `instance_missing` | Abort at prepare. |
| Container will not stop or accept restart `no` | `instance_running` / `restart_capable` | Abort; use `--abort-v1`. |
| Inventory digest or static guard differs | `mutator_inventory_mismatch` | Abort at prepare or migrate. |
| Test runtime isolation not established, or a probe reached the host socket, key, log, or database | `test_isolation_invalid` | Abort at prepare; no fallback to the host daemon. |
| Locked dependency or pinned image unavailable offline | `test_dependencies_unavailable` | Abort at prepare. |
| Test runtime state left after teardown | `test_runtime_residue` | Abort at prepare. |
| Wrapper's own test run collected the wrong nodes or tree, or verdict not `PASS` | `test_receipt_mismatch` / `test_receipt_not_pass` | Abort at prepare. |
| Receipt missing, mis-owned, forged, unknown signer, expired, wrong purpose | `receipt_*` | Migrate aborts; no DDL. |
| Database, OID, or cluster identifier differs or unreadable | `db_target_mismatch` / `db_target_unverifiable` | Abort; no DDL. |
| Fence absent, mismatched, wrong phase, or mis-owned | `fence_*` | Migrate aborts; no DDL. |
| Lock held elsewhere | `lock_unavailable` | Migrate aborts; no DDL. |
| Any other database session | `active_writer` / `db_activity_unverifiable` | Migrate aborts; no DDL. |
| Occurrence index absent or invalid | `occurrence_index_invalid` | Migrate aborts; no DDL. |
| Released container not exactly target, or extra | `release_instance_mismatch` / `release_instance_extra` | Fence stays held; fix and rerun release. |

## Rollback

- **Before G commits:** `--abort-v1`. Earlier transition-image rollback follows the existing
  `rel_035` rules in [Relationship Butler](../butlers/relationship.md#effective-time-on-structural-facts).
- **After G, before the first temporal write:** a new authorization with purpose
  `rollback_before_first_temporal_write`, prepared through the same fence. G's downgrade
  recreates `uq_ef_spo_active` only if no row in any validity carries a temporal value and no SPO has
  two active rows, then release starts the named rollback image.
- **After the first temporal write:** downgrade and old-image release are refused
  (`temporal_rollback_prohibited`). Roll forward, or follow a separately reviewed data-preserving
  plan. Never delete, supersede, collapse, or clear rows to make the legacy index buildable.

## Evidence

Keep only the receipt, the wrapper-produced test receipt, the wrapper's exit codes, and its content-blind output:
codes, counts, digests, SHAs, tree and image ids, container ids, service names, timestamps, database
name and OID, cluster identifier, fence and authorization ids. Never record fact, contact, or entity
content, SQL text, container environments, credentials, or key material.

## Related

- [Core 208 fleet rollout packet](../plans/2026-09-05-core-208-conversation-anchor-fleet-rollout.md): evidence
  template this packet extends with signing, tree identity, and an enforced fence.
- [Runtime-Probe Control Keys](runtime-probe-control-keys.md): signing and keyring document shapes
  this signer reuses with a separate key.
- [Docker Deployment](docker-deployment.md)
