## Context

`relationship-fact-effective-time` (design "Migration and Compatibility Plan", steps 3 and 4) makes
the legacy-index drop conditional on proof that every Relationship instance runs the transition
writer plus every inventoried mutator behavior or fence, that old images are absent, that the static
inventory is clean, and that the named real-PostgreSQL tests pass. It names what must be proven, not
how. This change retains the unimplemented Docker Compose proof proposal, projects `butlers`
(prod) and `butlers-dev` (dev), each against its own database. The current live dev fleet uses k3s;
there is no implemented Kubernetes cutover equivalent. The separate `bu-0kf2fd` source proposal
does not adopt that topology.

Actual `rel_036` creates meeting debriefs and remains untouched. Symbolic G is the then-free cutover
revision selected after the actual then-current head, never a reserved migration number.

Repository facts this design relies on:

- `rel_035` retains `uq_ef_spo_active`. The transition writer and every fenced mutator detect it with
  `legacy_spo_index_present` (`roster/relationship/tools/fact_temporal.py`) and refuse temporal
  intent `temporal_cutover_pending`. Index absence is the only capability signal.
- The static guard `tests/contracts/test_entity_facts_mutator_inventory.py` scans `roster/relationship`
  and `src/butlers` and requires its `_INVENTORY` to equal the found mutators exactly.
- `docker-compose.yml` bind-mounts `./roster:/app/roster:ro` into `butlers-up`, `dashboard-api`,
  `migrations`, and both hotreload variants. Hotreload variants also mount `./src`. The image bakes
  `src/`, `alembic/`, `scripts/`, and a `roster/` copy that those mounts shadow.
- Every credentialed long-lived service is `restart: unless-stopped`. `migrations` is a one-shot
  `db migrate` run.
- Automatic migration runs from daemon startup (`src/butlers/lifecycle.py`, core then butler chain),
  from `butlers db migrate` (`src/butlers/cli.py`), and from `butlers deploy`
  (`src/butlers/core/deploy.py`, `compose run --rm migrations`). `_upgrade_chain` always targets
  `<chain>@head`. `_build_alembic_config` includes every chain's version directory so any applied
  revision resolves.
- `scripts/compose.sh` always runs `down --remove-orphans` then `up -d`. `butlers deploy` builds,
  migrates, and runs `up -d --remove-orphans`.
- The restore-drill firewall already uses a fixed root-owned wrapper under `/usr/local/libexec/`, a
  sudoers fragment, and a generation-bound nonce checked before protected containers start. The
  runtime-probe control keys already define Ed25519 signing and keyring document shapes.

## Goals / Non-Goals

**Goals:**

- A receipt a migration can verify offline, whose claims were collected by the signer itself.
- Code identity that covers both the image and the bind-mounted `roster/` tree.
- An enforced fence, not an observation: supported start paths refuse while it is held, old
  containers cannot restart, and the DDL cannot overlap any live session.
- G present for revision resolution but excluded from automatic advancement of existing pre-cutover branches.
- The adopted rollback boundary, expressed as a checkable predicate.

**Non-Goals:**

- Writing G, the verifier, the wrapper, or any fence code.
- Provisioning keys, installing the wrapper, editing sudoers, or touching a host.
- Running any migration, deployment, or container lifecycle act.
- k3s, Kubernetes, or a generic orchestrator abstraction.
- Changing adopted `relationship-facts` requirement bodies, including the entity-merge collision
  wording already adopted through CLOSED `bu-ldcp5f` and PR #4317.

## Why existing patterns are insufficient

**Core 208 rollout packet** (`docs/plans/2026-09-05-core-208-conversation-anchor-fleet-rollout.md`).
It is a strong content-blind evidence template: immutable prerequisite SHA, image id plus projected
`GIT_SHA`, per-instance before/after identity, an abort matrix, and rollback order. It is
insufficient here for four reasons:

1. Its verdict is assembled and judged by an operator; nothing signs it and nothing in the database
   path checks it. A migration cannot distinguish a real packet from a typed one.
2. It treats image id plus `GIT_SHA` as code identity. That was sound for core 208, whose writers live
   in `src/`. Relationship mutators live mostly in bind-mounted `roster/`, which an image id does not
   cover.
3. It observes the fleet but does not fence it. Between observation and the next act, a restart
   policy, a `compose.sh` run, or a deploy can start any image.
4. Its window is a rollout, not a DDL. Nothing holds a database exclusion while the schema changes.

**Boot registrations** (`public.deployments`, `source='boot'`, written by `butlers.cli._start_all`).
Insufficient because only `butlers up` records one, once per process; Dashboard API, `migrations`,
and ad-hoc runs never do; the `GIT_SHA` is self-reported from an environment variable; there is no
image id and no `roster/` identity; rows are append-only history, so a stale process that never
reboots is invisible; and any database writer can insert a row.

**Signed snapshot plus advisory lock.** Rejected by the packet review and restated as a requirement.
A snapshot is true only at its instant. `pg_advisory_lock` is cooperative, and pre-transition code
never takes it. Neither stops Docker from restarting an old container.

## Decisions

### D1. The signer collects its own evidence

The wrapper (`/usr/local/libexec/butlers-relationship-temporal-cutover`, installed from
`scripts/relationship-temporal-cutover.sh`) is the only holder of the signing key and runs as root
via one sudoers rule. It invokes a root-owned installed copy of
`scripts/verify_relationship_temporal_cutover.py` whose SHA-256 it pins, performs the inventory,
test run, quiesce, and removal itself, and signs only what it observed in the same invocation.

That includes test evidence. The wrapper runs the fixed node list (a constant of the installed
wrapper), captures the `pytest_gate.py` log through a pipe into a root-only file, and recomputes the
verdict. No verb accepts a test receipt, gate log, CI result, or digest as input, so an
operator-written PASS log has no path to the signer. The tests run while the fleet is still up,
after the fence is set and before quiesce, so their duration does not lengthen the downtime or
consume the 15-minute receipt expiry.

### D1a. Tests run in an isolated runtime, never on the host daemon

The real-PostgreSQL scenarios need a container runtime. Host-daemon access is root-equivalent: it
could bind-mount `/` to read the signing key, write the captured log, start old or arbitrarily
labeled containers in the fenced project, and reach the cutover database. Target-commit code and its
dependencies would then sit one step from a long-lived root key. The contract therefore takes the
conservative option and specifies isolation rather than a trust statement:

| Property | Enforcement | Probe that must fail |
| --- | --- | --- |
| No host daemon | Account `butlers-rtc-test` in no `docker`/`sudo`/`wheel`/`adm`/`systemd-journal` group; its own rootless Docker daemon; `InaccessiblePaths` covers `/var/run/docker.sock` | open host socket |
| No key, fence, receipt, or log access | Transient systemd service with `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, `InaccessiblePaths=/etc/butlers /var/lib/butlers /run/secrets`; key directory `0700` root; log captured by the wrapper through a pipe | read key; write log path |
| No route to the target | `PrivateNetwork=yes` for the whole service, including the rootless daemon; fixed environment allowlist with no database keys; no readable env file | TCP connect to the cutover database endpoint |
| Frozen offline inputs | Source from `git archive <sha>` of the verified object store, tree id checked; toolchain is the target image moved by `docker save`/`docker load` and id-checked; test PostgreSQL image pinned by digest and moved the same way; `uv sync --frozen --offline` from a root-prepared read-only cache, hash-checked by `uv.lock` | none needed: no network exists |
| No residue | Service, rootless daemon, export, and data removed before inventory | residue check |

A disposable VM may replace the rootless daemon only if it satisfies the same properties and probes.
There is no host-daemon fallback: a host that cannot provide the runtime cannot run the managed
cutover. The account, rootless daemon, and cache are host preparation outside this change.

Because the run takes minutes, the wrapper re-verifies checkout `HEAD`, roster tree, cleanliness,
target image, and the resolved Compose row immediately before signing.

Alternatives rejected: an operator-run verifier or test run whose output is signed afterwards (signs
whatever it is handed); a hosted CI attestation (adds a network trust root and a second signer the
owner has not adopted); an unsigned root-owned file (any root container with a host mount could write one); reuse
of the runtime-probe key (Dashboard holds that signer, so a container compromise would forge
receipts).

### D2. Code identity is image plus checkout tree

An instance matches the target only when its image id equals the target image id, that image's
projected `GIT_SHA` equals the target SHA, its `roster` mount source equals `<working_dir>/roster`
of the canonical checkout, and that checkout is at the target SHA, has the target roster tree id,
and is clean under `roster`, `src`, and `alembic`. The checkout's `src` and `alembic` are included
because the target image is built from it and a dirty build tree would make `GIT_SHA` a lie.
Hotreload is refused outright, because live-reloaded source changes without a process start.

### D2a. One named Compose invocation, one mechanical credential rule

The effective service set depends on the launcher and its flags, so the authorization names exactly
one supported invocation row (spec, instance proof requirement): `prod-deploy` (`butlers deploy`),
`prod-launcher` (`scripts/compose.sh --prod`), or `dev-launcher`
(`scripts/compose.sh --no-hotreload`), each with fixed files, project, env file, and profiles. The
wrapper resolves the row from the launcher itself at the target SHA and hashes
`docker compose ... config --no-interpolate --format json`. `--no-interpolate` leaves every `${...}`
literal, so the digest is deterministic per SHA and row and never includes a credential value.

A service is database-credentialed when its non-interpolated configuration shows a PostgreSQL or
libpq environment key, a `*_DB_(HOST|PORT|USER|PASSWORD|PASSWORD_FILE)` key or a
`RESTORE_DRILL_EXECUTOR_`/`RESTORE_DRILL_PROXY_` key, a secret whose name looks like a database
credential, an attachment to the `db` network, or any `env_file`. The rule is deliberately
over-inclusive: a false positive only stops one more container during the window, while a false
negative leaves a live session that G would then refuse. On the current files it classifies
`backup-cron` (rules 1 and 4), `restore-drill-executor` (rules 2 and 3), and
`restore-drill-postgres-proxy` (rule 2) as credentialed, which a rule keyed on `x-postgres-env`
alone would miss.

Non-`butlers-app` credentialed services such as `backup-cron` are held to the image id their
configured reference resolves to at inventory rather than to the target image.

### D3. The fence is enforced by removal, refusal, and exclusion

Three independent layers:

1. **Removal.** Preparation sets every credentialed container's restart policy to `no`, stops it,
   proves it stopped, and removes it. With no container, neither a restart policy nor
   `docker start <id>` can revive old code.
2. **Refusal.** `scripts/compose.sh` (before its `down`) and `butlers deploy` (before build) read the
   fence and refuse `cutover_fence_held`. Only the wrapper's migration run and release verb start
   containers while the fence is held.
3. **Exclusion.** G takes `ACCESS EXCLUSIVE` with `NOWAIT` on `relationship.entity_facts`, then
   proves zero other client backends on the target database, then checks the fence. The lock makes
   any later connection unable to touch the table until commit; the session count proves no earlier
   one exists.

Quiesce covers every credentialed service, not only the Relationship writers, because the session
count cannot tell a connector's session from a writer's and every `butlers-app` container carries the
Relationship code. The cutover is therefore a short full-application maintenance window.

### D4. G is a gated revision, not a separate chain

G belongs in `roster/relationship/migrations/` with the actual then-current head as its predecessor.
Every later image must retain it for applied-revision resolution; a separate unregistered directory
would leave post-cutover daemons unable to locate the applied revision. Existing meeting-debrief
`rel_036` and its actual predecessor stay immutable. G and its filename are symbolic until allocated
against the current migration census.

The future `src/butlers/migrations.py` registry and `_upgrade_chain` resolver must use that actual
revision and predecessor:

- gate applied (the actual chain stamp is G or a descendant): target `head`;
- gate unapplied on an existing pre-cutover branch: target G's actual predecessor and log
  `temporal_cutover_pending` with the count of revisions waiting;
- genuine fresh/disposable traversal: advance unsigned only after the complete independent
  non-receipt proof binding and source-owned provenance required by REQ006 are implemented.

`get_chain_head` continues to return the true head. A future automatic-ceiling selector is distinct
from that answer. Source-derived admission must begin inside the actual online connection before
Relationship revisions and the invocation's own schema/version preparation. No resolver Boolean,
Config value or x-argument is freshness authority. Raw online Alembic and migrated helpers must use
the same protected seam; offline or unsupported paths refuse before cutover SQL/DDL. The current
runner/environment implement none of this, and the partial state witness below is insufficient
for unsigned G because the retained proof/fence contract has no non-receipt binding.

The future explicit legacy path is `butlers db relationship-temporal-cutover --receipt <path>`
(`src/butlers/cli.py`), using `cmd_opts.x =
["relationship_temporal_cutover_receipt=<path>"]`, one `NullPool` connection, only the Relationship
chain, and exactly the resolved G rather than `head`. A raw receipt-bearing Alembic invocation must
target that same actual G. These are future interfaces, not commands usable from this draft.

### D5. Test and inventory evidence are digests of exact artifacts

The inventory moves from test-only data into `src/butlers/relationship_temporal_cutover.py`
(`MUTATOR_INVENTORY`), so the image carries the list. The static guard test imports it. The digest
covers each entry's path, qualname, classification, and file bytes, so a changed fence body changes
the digest even when the list does not. The test receipt binds the fixed node list and the
`scripts/pytest_gate.py` log of the wrapper's own run (D1), whose verdict the wrapper recomputes.

### D6. Rollback is a second purpose, not a flag

A `rollback_before_first_temporal_write` receipt, collected under the same fence, is the only input
that lets G's `downgrade()` run. Keeping purposes distinct stops a cutover receipt from being
replayed as a rollback authority, or the reverse.

## Receipt schema

```json
{
  "schema": "butlers.relationship-temporal-cutover-receipt/v1",
  "authorization_id": "rtc-20261001-3f9a2c1d",
  "purpose": "cutover",
  "environment": "prod",
  "compose_project": "butlers",
  "target": {
    "git_sha": "<40 hex>",
    "image_id": "sha256:<64 hex>",
    "roster_tree": "<40 hex git tree id>"
  },
  "instances": [
    {
      "service": "butlers-up",
      "container_id": "<64 hex>",
      "image_id": "sha256:<64 hex>",
      "roster_mount": "target",
      "final_state": "removed"
    }
  ],
  "instance_set_digest": "<64 hex>",
  "compose": {
    "row": "prod-deploy",
    "files": ["docker-compose.yml", "docker-compose.restore-drill.yml"],
    "env_file": ".env.prod",
    "profiles": [],
    "config_digest": "<64 hex>",
    "credentialed_services": ["backup-cron", "butlers-up", "dashboard-api", "migrations"],
    "optional_services_absent": []
  },
  "mutator_inventory_digest": "<64 hex>",
  "test_receipt_digest": "<64 hex>",
  "db_target": {
    "database": "butlers",
    "database_oid": 16384,
    "system_identifier": "<decimal string>"
  },
  "fence": {"fence_id": "<uuid>", "generation": 1, "set_at": "2026-10-01T02:00:00Z"},
  "issued_at": "2026-10-01T02:04:00Z",
  "expires_at": "2026-10-01T02:19:00Z",
  "signer": {"kid": "rtc-2026-10a"},
  "signature": "<86-character unpadded base64url Ed25519 signature>"
}
```

Rules:

- `authorization_id` matches `rtc-[0-9]{8}-[0-9a-f]{8}` and is supplied by the separate live
  authorization. `environment` is `prod` or `dev` and must agree with `compose_project`.
- `instances` is sorted by `(service, container_id)`. `instance_set_digest` is the SHA-256 of its JCS
  encoding. `compose.credentialed_services` lists every service the credential rule selects in the
  resolved configuration; `compose.optional_services_absent` may name only services the live
  authorization lists as optional (for example `connector-live-listener` without the `audio`
  profile).
- `db_target.system_identifier` comes from `pg_control_system()` and is read by the wrapper through a
  single read-only query in a throwaway target-image container before quiesce. It pins the cluster,
  not just a name.
- `expires_at - issued_at` is at most 900 seconds. The migration compares against database `now()`,
  and also rejects `issued_at` more than 60 seconds in the future.
- The signature is Ed25519 over the JCS bytes of the document without `signature`.

The test receipt:

```json
{
  "schema": "butlers.relationship-temporal-cutover-tests/v1",
  "git_sha": "<40 hex>",
  "node_set_digest": "<64 hex>",
  "gate_log_sha256": "<64 hex>",
  "verdict": "PASS",
  "authorization_id": "rtc-20261001-3f9a2c1d",
  "fence_id": "<uuid>",
  "finished_at": "2026-10-01T01:58:00Z"
}
```

The wrapper writes it root-owned to
`/var/lib/butlers/relationship-temporal-cutover/tests/<authorization_id>.json` in the same
invocation that signs the receipt; it is never an input.

`test_receipt_digest` is the SHA-256 of the test receipt's JCS bytes.

## Fence schema

```json
{
  "schema": "butlers.relationship-temporal-cutover-fence/v1",
  "fence_id": "<uuid>",
  "authorization_id": "rtc-20261001-3f9a2c1d",
  "compose_project": "butlers",
  "target_git_sha": "<40 hex>",
  "target_image_id": "sha256:<64 hex>",
  "compose_row": "prod-deploy",
  "compose_config_digest": "<64 hex>",
  "generation": 1,
  "phase": "quiesced",
  "set_at": "2026-10-01T02:00:00Z"
}
```

Path: `/var/lib/butlers/relationship-temporal-cutover/<compose_project>.fence.json`. Phases advance
`inventory` then `quiesced` (receipt signed) then `migrated` (G committed) then `releasing`, and
the file is removed on verified release. `generation` increments on every write. Readers require
`root:root`, not group- or world-writable, a regular non-symlink file, and a root-owned
non-writable parent. The wrapper serializes itself with `flock` on
`/var/lib/butlers/relationship-temporal-cutover/.lock`.

The cutover migration run mounts, read-only, exactly three host paths into its container: the one
receipt file, the verifier keyring, and the fence file.

## Sequence

1. **Authorize** (live, separate): authorization id, environment, supported invocation row and
   flags, target SHA, target and rollback image ids, optional-absent services, and maximum window.
2. **Deploy the target normally.** Build and deploy the target image through the named row's
   ordinary launcher. The gate stops an existing pre-cutover chain at G's actual predecessor; the fleet runs target code with the
   legacy index present, so temporal intent is still refused.
3. **Prepare** (`--prepare-v1`): lock; set fence phase `inventory`; verify the checkout; resolve the
   named row from its launcher and hash the non-interpolated configuration; run the isolation probes
   and then the fixed test node list in the isolated test runtime (D1a), tear it down, and write the
   test receipt; read the image and inventory
   digests; read the database target; inventory containers. On any mismatch, abort and clear the
   fence (nothing has been touched yet). Otherwise set restart policy `no` on every credentialed
   container; stop; prove stopped; remove; prove zero credentialed containers; re-verify checkout,
   image, and Compose row; set phase `quiesced`; sign and write the receipt.
4. **Migrate** (`--migrate-v1`): run the explicit G path in a fresh target-image `migrations`
   container with the three read-only mounts. On success set phase `migrated`.
5. **Release** (`--release-v1`): set phase `releasing`; start the row's services through its own
   launcher in fence-release mode (no build, restore-drill preparation included, `butlers-app`
   pinned to the target image id); verify every started container against the instance rules with
   `StartedAt` after the G commit; prove no extra container; remove the fence.
6. **Abort before migrate** (`--abort-v1`): allowed only while the Relationship stamp is G's actual predecessor;
   releases the same target image through the step 5 checks and clears the fence. After G
   commits, the only forward path is release.

## Failure taxonomy

| Code | Raised by | Meaning |
| --- | --- | --- |
| `cutover_fence_held` | compose.sh, deploy | A fence exists; supported start refused. |
| `fence_custody_invalid` | wrapper, migration, compose.sh, deploy | Fence file or directory ownership or mode is wrong. |
| `fence_absent` / `fence_mismatch` / `fence_phase_invalid` | migration, release | Fence missing, bound to another authorization or target, or in the wrong phase. |
| `checkout_mismatch` / `checkout_dirty` | wrapper | `HEAD` or roster tree differs, or tree is dirty. |
| `hotreload_unverifiable` | wrapper | A hotreload container exists. |
| `instance_unknown_image` / `instance_mixed` | wrapper | A container uses a non-target image or roster tree. |
| `instance_extra` / `instance_missing` | wrapper | Set differs from the target Compose configuration. |
| `instance_running` / `restart_capable` | wrapper | A container failed to stop, or its policy could not be set to `no`. |
| `mutator_inventory_mismatch` | wrapper, migration | Inventory digest differs, or the static guard is not clean. |
| `test_receipt_mismatch` / `test_receipt_not_pass` | wrapper | The wrapper's own test run collected the wrong node set or tree, or its verdict is not `PASS`. |
| `test_isolation_invalid` | wrapper | A test-runtime isolation property cannot be established, or a negative probe succeeded. |
| `test_dependencies_unavailable` | wrapper | A locked dependency, the target image, or the pinned test PostgreSQL image is unavailable offline. |
| `test_runtime_residue` | wrapper | The test service, rootless daemon, export, or data survived teardown. |
| `compose_invocation_mismatch` | wrapper, release | Launcher resolution differs from the named row, or a container's config-files or env-file label differs. |
| `receipt_missing` / `receipt_unreadable` / `receipt_schema_invalid` | migration | Receipt absent, unreadable, or malformed. |
| `receipt_custody_invalid` | migration | Receipt not root-owned, writable, or a symlink. |
| `receipt_signature_invalid` / `receipt_signer_unknown` | migration | Signature fails, or `kid` unknown or outside its window. |
| `receipt_expired` / `receipt_not_yet_valid` | migration | Outside the validity window. |
| `receipt_purpose_mismatch` | migration | Cutover receipt used for rollback or the reverse. |
| `target_sha_mismatch` | migration | Receipt SHA differs from the migration image `GIT_SHA`. |
| `db_target_mismatch` / `db_target_unverifiable` | wrapper, migration | Database name, OID, or system identifier differs or cannot be read. |
| `lock_unavailable` | migration | `NOWAIT` access-exclusive lock failed. |
| `active_writer` / `db_activity_unverifiable` | migration | Another client backend exists, or the role cannot see `pg_stat_activity` rows. |
| `occurrence_index_invalid` | migration | `uq_ef_spo_occurrence_active` absent or not `indisvalid`. |
| `schema_state_unexpected` | migration | `uq_ef_spo_active` already absent while G is unapplied. |
| `temporal_rollback_prohibited` | G downgrade | Temporal-bearing row or duplicate active SPO exists. |
| `release_instance_mismatch` / `release_instance_extra` | release | Released container is not exactly the target, or an extra exists; fence stays held. |

Every code above that the migration raises occurs before its first DDL statement. All output is
limited to the categories in the receipt requirement.

## Adversarial cases

| Attempt | Outcome |
| --- | --- |
| `scripts/compose.sh` or `--prod` during the window | Refused `cutover_fence_held` before `down`. |
| `butlers deploy` during the window | Refused before build. |
| Docker daemon restart revives `unless-stopped` containers | Nothing to revive: policy set to `no`, stopped, removed. |
| `docker start <old id>` | Fails: container removed. |
| Old process still connected when G runs | `active_writer`, no DDL. |
| Connection opened after the session check | Blocks on the access-exclusive lock until commit. |
| Old writer released after cutover by an unsupported path | Central insert fails for lack of a matching `ON CONFLICT` target; non-central old mutators do not, which is why the managed path must not be bypassed. |
| Receipt copied from another cutover | Fence id, database target, or expiry mismatch. |
| Receipt edited to widen expiry | Signature fails. |
| Hotreload dev stack | `hotreload_unverifiable`; dev cutover uses `--no-hotreload`. |
| Operator edits `roster/` after release | Out of scope: the next process start runs the edit. The release check verifies `StartedAt`, image, mount, and a clean tree immediately after start. |

**Residual risk, stated plainly.** Raw `docker run`, `docker compose` from another checkout or with a
hand-set project name, GUI tools, and host `uv run` against the database bypass the refusal layer.
Removal and the exclusion check catch any such process that is alive at the DDL check, and the
central old-writer statement fails after the drop, but nothing in this design can stop a root-capable
operator from starting old non-central code after release. The packet must say so and must forbid
those paths during and after the window.

## Rollback

- **Before step 4 commits:** `--abort-v1` releases the same target image. The target code is valid
  with the legacy index, so this is not a code rollback at all. Ordinary rollback to an earlier
  transition image remains the existing `rel_035` rule.
- **After G, before the first temporal write:** a new authorization and a
  `rollback_before_first_temporal_write` receipt, prepared through the same fence. G
  `downgrade()` takes the same lock and session proof, then checks:

  ```sql
  SELECT EXISTS (
    SELECT 1 FROM relationship.entity_facts
    WHERE effective_from IS NOT NULL OR effective_to IS NOT NULL
       OR effective_from_precision IS NOT NULL OR effective_to_precision IS NOT NULL
       OR effective_period_id IS NOT NULL
  ) OR EXISTS (
    SELECT 1 FROM relationship.entity_facts
    WHERE validity = 'active'
    GROUP BY subject, predicate, object HAVING count(*) > 1
  )
  ```

  If true it aborts `temporal_rollback_prohibited`. Otherwise it creates `uq_ef_spo_active` with its
  original definition in the same transaction. The release verb then starts the named rollback image
  under the same instance checks against the rollback receipt's target.
- **After the first temporal write:** downgrade and old-image release are refused. Recovery rolls
  forward or follows a separately reviewed data-preserving plan. No path deletes, supersedes,
  collapses, or clears rows to make the index buildable.

## Future source and test ownership

| Seam | Source | Tests |
| --- | --- | --- |
| Read-only verifier (row resolution, config digest, credential rule, inventory, release check), invoked only by the wrapper | `scripts/verify_relationship_temporal_cutover.py` | `tests/scripts/test_verify_relationship_temporal_cutover.py` |
| Isolated test runtime (transient service definition, isolation probes, offline cache layout) | `scripts/relationship-temporal-cutover-test-runtime.sh` | `tests/scripts/test_relationship_temporal_cutover_test_runtime.py` |
| Root wrapper, sudoers, installer | `scripts/relationship-temporal-cutover.sh`, `scripts/relationship-temporal-cutover.sudoers`, `scripts/install_relationship_temporal_cutover_wrapper.sh` | `tests/scripts/test_relationship_temporal_cutover_wrapper.py` |
| Compose launcher fence, non-mutating row resolution, fence-release mode | `scripts/compose.sh` | `tests/scripts/test_compose_relationship_cutover_fence.py` |
| Deploy fence, row resolution, fence-release mode | `src/butlers/core/deploy.py` | `tests/core/test_deploy.py` |
| Receipt, fence, inventory parser and digests | `src/butlers/relationship_temporal_cutover.py` | `tests/core/test_relationship_temporal_cutover_receipt.py` |
| Gated ceiling, actual online admission and legacy x-argument plumbing | `src/butlers/migrations.py`, `alembic/env.py`, `src/butlers/cli.py` | Nearest existing `tests/config/test_migrations.py`, `tests/daemon/test_butler_migrations.py`; `tests/core/test_migration_gated_revision.py` only for a distinct selector seam |
| Static inventory guard (imports shipped inventory) | `tests/contracts/test_entity_facts_mutator_inventory.py` | same |
| Then-free cutover migration G | Actual then-allocated file in `roster/relationship/migrations/` | One then-named roster migration test species; preserve rel_035 old/new writer controls |
| Operator packet | `docs/operations/relationship-effective-time-cutover.md` | reviewed with the wrapper |

New test files are registered in `.github/ci-test-shards` in the same PR that adds them. G tests
use test-only keys, a temporary fence directory, and testcontainers PostgreSQL; they never read host
paths.

## Owner decisions

The dedicated signer/proof artifact and separate exact-environment authorization govern live acts.
CLOSED gate-policy and merge decisions are consumed below; the existing bounded repository
implementation release is not reopened. This draft does not adopt a new live artifact.

### Signer artifact

One artifact choice:

> Adopt a dedicated host-root Ed25519 signing key, `kid` namespace `rtc-*`, with the private
> document at `/etc/butlers/relationship-temporal-cutover/signing-key.json` (`root:root`, `0400`)
> readable only by the installed wrapper
> `/usr/local/libexec/butlers-relationship-temporal-cutover`, and the public keyring at
> `/etc/butlers/relationship-temporal-cutover/verifiers.json` (`root:root`, `0444`).

Declining leaves the signed legacy-receipt path unrunnable. The separate mandatory unsigned path
also remains unavailable until its complete proof and admission are implemented. This change
provisions nothing.

### Gate lifecycle and non-production policy: CLOSED A, mandatory source gaps

Owner decision bu-ftd491 is CLOSED with answer A. Its non-spoofable requirement excludes a
caller-chosen `fresh`/`disposable` flag and excludes absent index plus currently empty rows as
sufficient historical proof. bu-ldcp5f is CLOSED and its no-effective-time merge carve-out was
applied by PR4317. These facts consume earlier authority rather than request it again.

The smallest candidate adds no durable custody. At the actual online environment connection, before
any Relationship revision or its own schema/version preparation, read every applicable
public/schema-scoped branch stamp and the reviewed source-derived historical Relationship footprint
manifest. Empty managed bootstrap schemas and unrelated core/module objects may exist; existing
Relationship objects or stamps, hidden/ambiguous schemas or insufficient metadata visibility deny
the exception. Known shared public or memory objects alone are not Relationship history, but a
caller cannot label unknown objects as bootstrap to exempt them. The exact resolved manifest needs
actual migrated positive and planted historical controls, not a grep-only authority.

Only a private non-serialized invocation witness derived there may cross to G. Bind actual database
name/OID/cluster, backend/physical connection, actual transaction, schema and G traversal; never
accept a Config Boolean, x-argument, environment flag, caller DTO or persistent disposable marker.
Preserve the initial snapshot through the same traversal's intermediate stamp; do not mistake the
predecessor stamp created inside that transaction for history at admission. Invalidate on
consumption, commit, rollback, reconnect, different target, Config reuse/next-chain call and every
exit.

G independently validates the binding, complete compatible-mutator/old-writer-absence and lifecycle
proof, required other-client exclusion and `ACCESS EXCLUSIVE NOWAIT`. Under that exclusion, require
zero `entity_facts` in every validity, validate both original legacy and occurrence index
definitions/validity, and drop only the legacy index plus stamp atomically. A first-run rel028
import from public data revokes empty admission; rollback cannot delete legacy input to qualify. The
mandatory completed unsigned outcome may waive the signed cutover receipt only, never whole
writer/lifecycle proof. The present private state witness cannot do so: REQ005 must compare a
quiesced fence to the receipt, and no protected non-receipt binding exists for REQ003/004/005. G
must refuse witness-only unsigned authority. Full equivalent source/test/instance/target/fence
binding remains UNDELIVERED and must be explicitly composed into the complete proof contract, not
inferred. Any direct raw online caller must use this same protected admission. Offline/unsupported
invocation denies before cutover SQL/DDL. The normal stamped/legacy receipt route retains its
independent custody/schema/signature/purpose/time/image/code/inventory/database/fence/exclusion
checks and the first-temporal-write rollback boundary.

Current `_upgrade_chain` blindly upgrades head; the reusable Config and each `env.py` NullPool
connection do not derive this witness. Environment schema preflight currently commits before Alembic
migration transaction; the future refactor must start actual admission in the same transaction that
performs Relationship traversal. CLI/core/module and test helper chains are separate traversals.
Daemon pools and logging connect before migrations, so unsupported other-client/exclusion topology
may correctly deny; no production drain/reorder or grants are inferred. Both public-version-table
and schema-scoped migrated helpers must be proved using real normal migration login, not a test-only
fresh signal or skipped gate.

A fully rolled-back from-base traversal can retry by rereading catalog and minting a new private
witness; no cached old authority survives. A legitimate committed pre-G bootstrap/bounded stage,
restored or stamped scratch, DELETE/TRUNCATE, ordinary downgrade, stamp edit or missing index cannot
obtain this exception from emptiness. That unsigned stamped-disposable resume remains unsolved in
this candidate and remains part of the original implementation outcome assessment. The original
terminal nevertheless requires genuine disposable interrupted/committed/bootstrap/resume to advance
unsigned using valid non-spoofable provenance plus complete non-receipt proof. That outcome is
UNDELIVERED, not permanently redefined as receipt-bearing. The temporary safety fallback is normal
receipt or separately authorized recreation, never automatic destruction or terminal closure. A
complete target-bound source-owned marker could reuse existing trusted
bootstrap/migration/provisioning authority under CLOSED A if actual RLS/object
ownership/trigger/default-grant/bootstrap reapply/restore/lifetime/race checks prove it; durability
alone does not invent a new owner decision. A label is not provenance.

The security doctrine trusts the owner/host/migration role. That actor can remove every historical
stamp and footprint or restore a manipulated pristine-looking dump; current catalogs cannot
distinguish its past. State the residual explicitly, without promising refusal. Defending that past
against this actor requires a new durable authority/custody boundary; it is not implied by CLOSED A.
Existing bounded repository implementation release is retained. Dedicated signer/proof adoption and
exact environment authorization continue to gate live acts; no blanket new source permission is
invented.

Allocate receipt and fresh admission together under bu-h3b7t's state machine/DDL/stamp/migrated
fixture/security review, carrying bu-2z6jyb's full runtime terminal acceptance. Source-stage
clarification is independent and countable, but it cannot close the original. Consume the source
artifact without a parent/original dependency cycle; an independently released exact hook is the
only alternative serial source ownership. Fold filesystem PostgreSQL Unix-socket isolation bu-jnnxtq
into the same actual sandbox. Serialize bu-0kf2fd temporal draft author, preserve the full Compose
contract and its source-only unadopted Kubernetes alternative.

## Risks / Trade-offs

- Full-application downtime for the window: accepted, because session counting cannot distinguish
  readers from writers and every app container carries the Relationship code.
- `pg_control_system()` or other sessions' `pg_stat_activity` rows may be invisible to the migration
  role: the migration fails closed (`db_target_unverifiable`, `db_activity_unverifiable`); tests must
  prove visibility under the role Compose uses.
- A linear gate blocks later revisions for existing pre-cutover branches until the applicable proof
  succeeds. CLOSED A requires unsigned genuinely fresh/disposable advancement, including legitimate
  interruption and committed resume. Its complete non-receipt binding and resume provenance are
  UNDELIVERED; current witness-only refusal is temporary safety, not accepted terminal downscope.
  No automatic gate retirement or lighter real-data route is inferred.
- The isolated test runtime needs host preparation (account, rootless daemon, offline cache) and may
  be unavailable on some hosts: accepted; preparation fails closed with `test_isolation_invalid`
  rather than falling back to the host daemon.
- Root wrapper complexity: bounded by fixed verbs, no generic signing, pinned verifier digest, and
  the restore-drill wrapper as precedent.

## Open Questions

The exact dedicated signer/proof artifact and live environment authorization remain distinct live
gates. CLOSED `bu-ftd491` answer A and CLOSED/applied `bu-ldcp5f` are not unanswered questions.
Source design must still deliver equivalent protected REQ003/004/005 unsigned binding and legitimate
disposable interrupted/committed/bootstrap/resume provenance. Existing trusted actors may suffice
under the adopted security doctrine; an actual new actor, privilege, custody or stronger historical
guarantee needs its precise changed-boundary decision, not blanket source permission. This source
proposal chooses no marker/binding implementation and supplies no runtime proof.
