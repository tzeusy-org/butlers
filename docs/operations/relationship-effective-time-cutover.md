# Relationship Effective-Time Cutover Packet

> **Status:** draft contract. The verifier, wrapper, fence checks, and rel036 migration named below
> do **not** exist yet. Nothing on this page can be executed today, and nothing here authorizes a
> key, host change, deployment, container stop or start, migration, or temporal activation.
> **Contract:** [`authorize-relationship-effective-time-cutover`](../../openspec/changes/authorize-relationship-effective-time-cutover/design.md)
> (receipt and fence schemas, failure taxonomy, file ownership) on top of
> [`relationship-fact-effective-time`](../../openspec/changes/relationship-fact-effective-time/design.md).
> **Scope:** Docker Compose, project `butlers` (prod) or `butlers-dev` (dev). No other orchestrator.

## What the cutover does

`rel_035` added effective-time columns and the occurrence index but kept
`relationship.uq_ef_spo_active`. While that index exists, every Relationship mutator refuses temporal
intent with `temporal_cutover_pending`. rel036 drops only that index, which is the sole signal that
enables temporal assertions, corrections, and repeated periods.

Old code must be gone first: pre-transition writers and unfenced mutators collapse or overwrite
occurrences once the index is absent. The cutover therefore proves the whole fleet is the exact
target, fences every supported start path, and holds a database exclusion while the index drops.

## Why the image id is not enough

`roster/` is bind-mounted from the host checkout into `butlers-up`, `dashboard-api`, and
`migrations`. Most Relationship mutators live there. A process's code is its image **plus** the
checkout's `roster/` tree, so every check below binds both. Hotreload also mounts `src/` and reloads
live, so a hotreload stack cannot be proven and is refused. Run dev cutovers with `--no-hotreload`.

## Preconditions

All must hold before anyone runs step 1:

1. The owner has adopted the dedicated signer (design "Owner decision"), and the key, keyring,
   wrapper, and sudoers rule are installed by a separate host act.
2. The target SHA contains the verifier, wrapper, fence checks, rel036, and the
   `relationship-fact-effective-time` task 3.5 real-PostgreSQL scenarios.
3. The entity-merge collision wording amendment `bu-ldcp5f` is resolved, so the inventory digest
   attests behavior that matches the adopted contract.
4. A live authorization names: authorization id (`rtc-YYYYMMDD-xxxxxxxx`), environment, target SHA,
   target image id, rollback image id (retained), optional absent services, and maximum window.

## Procedure

Each verb is a fixed wrapper entry point run as
`sudo -n /usr/local/libexec/butlers-relationship-temporal-cutover <verb> --authorization <id>`.

1. **Deploy the target normally.** `butlers deploy` (prod) or `scripts/compose.sh --no-hotreload`
   (dev) at the target SHA. The gated ceiling stops the Relationship chain at `rel_035`, so the fleet
   runs target code with the legacy index still present. Confirm the chain reports
   `temporal_cutover_pending`.
2. **Produce the test receipt.** In a clean checkout at the target SHA:
   `scripts/verify_relationship_temporal_cutover.py test-receipt --target-sha <sha>`. It runs the
   fixed node list through `scripts/pytest_gate.py` and writes the test receipt. Only verdict `PASS`
   is usable.
3. **Prepare** (`--prepare-v1`). The wrapper locks, sets the fence, verifies the checkout, reads the
   image and inventory digests and database target, and inventories every credentialed container.
   Any mismatch aborts and clears the fence with nothing touched. Otherwise it sets each container's
   restart policy to `no`, stops it, proves it stopped, removes it, proves none remain, sets the fence
   to `quiesced`, and writes the signed receipt. Expiry is 15 minutes from here.
4. **Migrate** (`--migrate-v1`). The wrapper runs
   `butlers db relationship-temporal-cutover --receipt <path>` in a fresh target-image `migrations`
   container with the receipt, keyring, and fence mounted read-only. rel036 takes a `NOWAIT`
   access-exclusive lock, proves no other database session, verifies the receipt and fence, checks
   both indexes, and drops only `uq_ef_spo_active`. Any failure changes nothing.
5. **Release** (`--release-v1`). The wrapper starts the target image only, verifies every container's
   image id, `GIT_SHA`, roster mount, and start time after the rel036 commit, proves no extra
   container, and removes the fence. A mismatch keeps the fence held.

**Abort** (`--abort-v1`) is allowed only while the Relationship chain is still `rel_035`. It releases
the same target image through the step 5 checks and clears the fence. After rel036 commits, the only
path is forward through release.

## During the window

While the fence is held:

- `scripts/compose.sh` and `butlers deploy` refuse `cutover_fence_held`. Do not work around them.
- Do not use `docker start`, `docker restart`, `docker run`, raw `docker compose`, GUI container
  tools, `compose.sh` from any other checkout, or host `uv run` against this database.
- Do not edit, pull, or check out anything in the deploy checkout.

**Residual risk.** Those unsupported paths bypass the refusal layer. The wrapper removes old
containers and rel036 refuses any live session, so a bypass that is running at the DDL check is
caught. Nothing stops a root-capable operator from starting old non-central code after release, and
that code can collapse temporal occurrences. The prohibition on those paths continues after the
window.

## Abort matrix

| Condition | Code | Effect |
| --- | --- | --- |
| Checkout not at target, wrong roster tree, or dirty | `checkout_mismatch` / `checkout_dirty` | Abort at prepare; nothing touched. |
| Hotreload container present | `hotreload_unverifiable` | Abort at prepare. |
| Non-target image or roster tree on any container | `instance_unknown_image` / `instance_mixed` | Abort at prepare. |
| Extra or missing credentialed service | `instance_extra` / `instance_missing` | Abort at prepare. |
| Container will not stop or accept restart `no` | `instance_running` / `restart_capable` | Abort; use `--abort-v1`. |
| Inventory digest or static guard differs | `mutator_inventory_mismatch` | Abort at prepare or migrate. |
| Test receipt for another SHA, incomplete, not `PASS` | `test_receipt_mismatch` / `test_receipt_not_pass` | Abort at prepare. |
| Receipt missing, mis-owned, forged, unknown signer, expired, wrong purpose | `receipt_*` | Migrate aborts; no DDL. |
| Database, OID, or cluster identifier differs or unreadable | `db_target_mismatch` / `db_target_unverifiable` | Abort; no DDL. |
| Fence absent, mismatched, wrong phase, or mis-owned | `fence_*` | Migrate aborts; no DDL. |
| Lock held elsewhere | `lock_unavailable` | Migrate aborts; no DDL. |
| Any other database session | `active_writer` / `db_activity_unverifiable` | Migrate aborts; no DDL. |
| Occurrence index absent or invalid | `occurrence_index_invalid` | Migrate aborts; no DDL. |
| Released container not exactly target, or extra | `release_instance_mismatch` / `release_instance_extra` | Fence stays held; fix and rerun release. |

## Rollback

- **Before rel036 commits:** `--abort-v1`. Earlier transition-image rollback follows the existing
  `rel_035` rules in [Relationship Butler](../butlers/relationship.md#effective-time-on-structural-facts).
- **After rel036, before the first temporal write:** a new authorization with purpose
  `rollback_before_first_temporal_write`, prepared through the same fence. rel036's downgrade
  recreates `uq_ef_spo_active` only if no row in any validity carries a temporal value and no SPO has
  two active rows, then release starts the named rollback image.
- **After the first temporal write:** downgrade and old-image release are refused
  (`temporal_rollback_prohibited`). Roll forward, or follow a separately reviewed data-preserving
  plan. Never delete, supersede, collapse, or clear rows to make the legacy index buildable.

## Evidence

Keep only the receipt, the test receipt, the wrapper's exit codes, and its content-blind output:
codes, counts, digests, SHAs, tree and image ids, container ids, service names, timestamps, database
name and OID, cluster identifier, fence and authorization ids. Never record fact, contact, or entity
content, SQL text, container environments, credentials, or key material.

## Related

- [Core 208 fleet rollout packet](2026-09-05-core-208-conversation-anchor-fleet-rollout.md): evidence
  template this packet extends with signing, tree identity, and an enforced fence.
- [Runtime-Probe Control Keys](runtime-probe-control-keys.md): signing and keyring document shapes
  this signer reuses with a separate key.
- [Docker Deployment](docker-deployment.md)
