## Context

`relationship-fact-effective-time` (design "Migration and Compatibility Plan", steps 3 and 4) makes
the legacy-index drop conditional on proof that every Relationship instance runs the transition
writer plus every inventoried mutator behavior or fence, that old images are absent, that the static
inventory is clean, and that the named real-PostgreSQL tests pass. It names what must be proven, not
how. This change specifies the mechanism for the only current deployment shape: Docker Compose,
projects `butlers` (prod) and `butlers-dev` (dev), each against its own database.

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
- rel036 present for revision resolution but absent from every automatic path.
- The adopted rollback boundary, expressed as a checkable predicate.

**Non-Goals:**

- Writing rel036, the verifier, the wrapper, or any fence code.
- Provisioning keys, installing the wrapper, editing sudoers, or touching a host.
- Running any migration, deployment, or container lifecycle act.
- k3s, Kubernetes, or a generic orchestrator abstraction.
- Changing adopted `relationship-facts` requirement bodies, including the entity-merge collision
  wording owned by `bu-ldcp5f`.

## Why existing patterns are insufficient

**Core 208 rollout packet** (`docs/operations/2026-09-05-core-208-conversation-anchor-fleet-rollout.md`).
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
quiesce, and removal itself, and signs only what it observed in the same invocation.

Alternatives rejected: an operator-run verifier whose output is signed afterwards (signs whatever it
is handed); an unsigned root-owned file (any root container with a host mount could write one); reuse
of the runtime-probe key (Dashboard holds that signer, so a container compromise would forge
receipts).

### D2. Code identity is image plus checkout tree

An instance matches the target only when its image id equals the target image id, that image's
projected `GIT_SHA` equals the target SHA, its `roster` mount source equals `<working_dir>/roster`
of the canonical checkout, and that checkout is at the target SHA, has the target roster tree id,
and is clean under `roster`, `src`, and `alembic`. The checkout's `src` and `alembic` are included
because the target image is built from it and a dirty build tree would make `GIT_SHA` a lie.
Hotreload is refused outright, because live-reloaded source changes without a process start.

### D3. The fence is enforced by removal, refusal, and exclusion

Three independent layers:

1. **Removal.** Preparation sets every credentialed container's restart policy to `no`, stops it,
   proves it stopped, and removes it. With no container, neither a restart policy nor
   `docker start <id>` can revive old code.
2. **Refusal.** `scripts/compose.sh` (before its `down`) and `butlers deploy` (before build) read the
   fence and refuse `cutover_fence_held`. Only the wrapper's migration run and release verb start
   containers while the fence is held.
3. **Exclusion.** rel036 takes `ACCESS EXCLUSIVE` with `NOWAIT` on `relationship.entity_facts`, then
   proves zero other client backends on the target database, then checks the fence. The lock makes
   any later connection unable to touch the table until commit; the session count proves no earlier
   one exists.

Quiesce covers every credentialed service, not only the Relationship writers, because the session
count cannot tell a connector's session from a writer's and every `butlers-app` container carries the
Relationship code. The cutover is therefore a short full-application maintenance window.

### D4. rel036 is a gated revision, not a separate chain

rel036 lives in `roster/relationship/migrations/` with `down_revision = "rel_035"`, so every image
built after it can resolve `alembic_version = rel_036`. Putting it in a separate directory would
leave post-cutover daemons unable to locate the applied revision and fail every boot.

`src/butlers/migrations.py` gains a registry `GATED_REVISIONS = {"relationship": "rel_036"}` and a
resolver used by `_upgrade_chain`:

- gate applied (the schema's `alembic_version` for that chain is the gate or a descendant): target
  `head`;
- gate unapplied: target the gate's `down_revision`, and log `temporal_cutover_pending` with the
  count of revisions waiting.

`get_chain_head` keeps returning the true head; a new `get_automatic_ceiling(chain, applied)` is what
automatic callers and tests of automatic behavior use. rel036's own `upgrade()` refuses without the
x-argument, so a raw `alembic upgrade relationship@head` also fails closed.

The explicit path is `butlers db relationship-temporal-cutover --receipt <path>` (future CLI in
`src/butlers/cli.py`), which builds the Alembic config with `cmd_opts.x =
["relationship_temporal_cutover_receipt=<path>"]`, uses one `NullPool` connection, runs only the
Relationship chain, and upgrades to exactly `rel_036`, never `head`. The equivalent raw form
`alembic -x relationship_temporal_cutover_receipt=<path> upgrade rel_036` is also valid.

### D5. Test and inventory evidence are digests of exact artifacts

The inventory moves from test-only data into `src/butlers/relationship_temporal_cutover.py`
(`MUTATOR_INVENTORY`), so the image carries the list. The static guard test imports it. The digest
covers each entry's path, qualname, classification, and file bytes, so a changed fence body changes
the digest even when the list does not. The test receipt binds the fixed node list and the
`scripts/pytest_gate.py` log, whose verdict the wrapper recomputes.

### D6. Rollback is a second purpose, not a flag

A `rollback_before_first_temporal_write` receipt, collected under the same fence, is the only input
that lets rel036's `downgrade()` run. Keeping purposes distinct stops a cutover receipt from being
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
  "expected_services": ["butlers-up", "dashboard-api", "migrations"],
  "optional_services_absent": [],
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
  encoding. `expected_services` lists every credentialed service in the target Compose configuration
  with the active profiles; `optional_services_absent` may name only services the live authorization
  lists as optional (for example `connector-live-listener` without the `audio` profile).
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
  "finished_at": "2026-09-30T18:00:00Z"
}
```

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
  "generation": 1,
  "phase": "quiesced",
  "set_at": "2026-10-01T02:00:00Z"
}
```

Path: `/var/lib/butlers/relationship-temporal-cutover/<compose_project>.fence.json`. Phases advance
`inventory` then `quiesced` (receipt signed) then `migrated` (rel036 committed) then `releasing`, and
the file is removed on verified release. `generation` increments on every write. Readers require
`root:root`, not group- or world-writable, a regular non-symlink file, and a root-owned
non-writable parent. The wrapper serializes itself with `flock` on
`/var/lib/butlers/relationship-temporal-cutover/.lock`.

The cutover migration run mounts, read-only, exactly three host paths into its container: the one
receipt file, the verifier keyring, and the fence file.

## Sequence

1. **Authorize** (live, separate): authorization id, environment, target SHA, target and rollback
   image ids, optional services, and maximum window.
2. **Deploy the target normally.** Build and deploy the target image through the ordinary path. The
   gate stops the chain at `rel_035`; the fleet runs target code with the legacy index present, so
   temporal intent is still refused.
3. **Produce the test receipt** at the target SHA in a clean checkout.
4. **Prepare** (`--prepare-v1`): lock; set fence phase `inventory`; verify checkout; read the image
   and inventory digests; read the database target; inventory containers; abort and clear the fence
   on any mismatch (nothing has been touched yet); set restart policy `no`; stop; prove stopped;
   remove; prove zero credentialed containers; set phase `quiesced`; sign and write the receipt.
5. **Migrate** (`--migrate-v1`): run the explicit rel036 path in a fresh target-image `migrations`
   container with the three read-only mounts. On success set phase `migrated`.
6. **Release** (`--release-v1`): set phase `releasing`; `compose up -d` with the target image only;
   verify every started container's image id, `GIT_SHA`, roster mount, and `StartedAt` after the
   rel036 commit; prove no extra container; remove the fence.
7. **Abort before migrate** (`--abort-v1`): allowed only while `alembic_version` is `rel_035`; releases
   the same target image through the step 6 checks and clears the fence. After rel036 commits, the
   only forward path is release.

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
| `test_receipt_mismatch` / `test_receipt_not_pass` | wrapper | Test receipt bound elsewhere, incomplete, or not `PASS`. |
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
| `schema_state_unexpected` | migration | `uq_ef_spo_active` already absent while rel036 is unapplied. |
| `temporal_rollback_prohibited` | rel036 downgrade | Temporal-bearing row or duplicate active SPO exists. |
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
| Old process still connected when rel036 runs | `active_writer`, no DDL. |
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

- **Before step 5 commits:** `--abort-v1` releases the same target image. The target code is valid
  with the legacy index, so this is not a code rollback at all. Ordinary rollback to an earlier
  transition image remains the existing `rel_035` rule.
- **After rel036, before the first temporal write:** a new authorization and a
  `rollback_before_first_temporal_write` receipt, prepared through the same fence. rel036
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
| Read-only verifier (inventory, test receipt, release check) | `scripts/verify_relationship_temporal_cutover.py` | `tests/scripts/test_verify_relationship_temporal_cutover.py` |
| Root wrapper, sudoers, installer | `scripts/relationship-temporal-cutover.sh`, `scripts/relationship-temporal-cutover.sudoers`, `scripts/install_relationship_temporal_cutover_wrapper.sh` | `tests/scripts/test_relationship_temporal_cutover_wrapper.py` |
| Compose launcher fence | `scripts/compose.sh` | `tests/scripts/test_compose_relationship_cutover_fence.py` |
| Deploy fence | `src/butlers/core/deploy.py` | `tests/core/test_deploy.py` |
| Receipt, fence, inventory parser and digests | `src/butlers/relationship_temporal_cutover.py` | `tests/core/test_relationship_temporal_cutover_receipt.py` |
| Gated ceiling and x-argument plumbing | `src/butlers/migrations.py`, `src/butlers/cli.py` | `tests/core/test_migration_gated_revision.py`, `tests/config/test_migrations.py` |
| Static inventory guard (imports shipped inventory) | `tests/contracts/test_entity_facts_mutator_inventory.py` | same |
| rel036 migration | `roster/relationship/migrations/036_entity_fact_effective_time_cutover.py` | `roster/relationship/tests/test_rel_036_cutover_migration.py` |
| Operator packet | `docs/operations/relationship-effective-time-cutover.md` | reviewed with the wrapper |

New test files are registered in `.github/ci-test-shards` in the same PR that adds them. rel036 tests
use test-only keys, a temporary fence directory, and testcontainers PostgreSQL; they never read host
paths.

## Owner decision

One artifact choice is required before any live cutover:

> Adopt a dedicated host-root Ed25519 signing key, `kid` namespace `rtc-*`, with the private
> document at `/etc/butlers/relationship-temporal-cutover/signing-key.json` (`root:root`, `0400`)
> readable only by the installed wrapper
> `/usr/local/libexec/butlers-relationship-temporal-cutover`, and the public keyring at
> `/etc/butlers/relationship-temporal-cutover/verifiers.json` (`root:root`, `0444`).

Declining leaves rel036 unrunnable, which is the safe default. This change provisions nothing.

## Risks / Trade-offs

- Full-application downtime for the window: accepted, because session counting cannot distinguish
  readers from writers and every app container carries the Relationship code.
- `pg_control_system()` or other sessions' `pg_stat_activity` rows may be invisible to the migration
  role: the migration fails closed (`db_target_unverifiable`, `db_activity_unverifiable`); tests must
  prove visibility under the role Compose uses.
- A gated revision delays every later Relationship revision on databases that have not cut over:
  accepted and logged; later revisions that do not depend on the drop should land before the gate.
- Root wrapper complexity: bounded by fixed verbs, no generic signing, pinned verifier digest, and
  the restore-drill wrapper as precedent.

## Open Questions

None within the proposal. Every decision is subject to exact owner acceptance, and the signer is the
single owner artifact choice above.
