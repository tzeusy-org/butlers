# Relationship Effective-Time Cutover

> `rel_047` is the cutover revision G, after `rel_046` (`bu-h3b7t`). Rolling out a release
> carrying G is an owner act.
> **Contract:** [`authorize-relationship-effective-time-cutover`](../../openspec/changes/authorize-relationship-effective-time-cutover/design.md)
> on top of [`relationship-fact-effective-time`](../../openspec/changes/relationship-fact-effective-time/design.md).
> **Decision:** plain hard cutover, owner answer B on `bu-4awqyt` (2026-10-20). The earlier signed
> receipt, root signer, isolated test runtime, Compose fence and Kubernetes extension are dropped.

## What the cutover does

`rel_035` added effective-time columns and the occurrence index but kept
`relationship.uq_ef_spo_active`. While that index exists, every Relationship mutator refuses temporal
intent with `temporal_cutover_pending`. G drops only that index, which is the sole signal that
enables temporal assertions, corrections, and repeated periods.

Old code must be gone first: pre-transition writers and unfenced mutators collapse or overwrite
occurrences once the index is absent. The database proves it: before any DDL, G takes an exclusive
no-wait lock on `relationship.entity_facts` and refuses while any other client is connected to the
database. The check accepts readable clients of other databases and the identified PostgreSQL
cluster-global processes whose rows have no target database or client socket. The release
carrying G includes the complete compatible mutator inventory
(`tests/contracts/test_entity_facts_mutator_inventory.py`).

## Rollout

The rollout costs whole-assistant downtime: stopping every database client stops every butler.
On k3s the chart's `suspend` value scales every Deployment to 0 and suspends every CronJob, and
because it is the chart's desired state, Helm keeps it that way until it is turned off.

1. **Check the release.** The release carrying G passed its required checks, and the migration
   login role (the database user of the migration connection, never a `butler_*_rw` role) has
   effective `pg_read_all_stats` access, either through an inherited grant or as superuser.
2. **Take and verify a backup.** Take a fresh backup of the
   target database and prove it restores, per [Backup and Restore](backup-restore.md) (the
   [managed restore drill](backup-restore.md#managed-restore-drill) or an equivalent scratch
   restore). Do not continue on an unverified backup.
3. **Stop every client and keep it stopped.** Deploy the release carrying G with `suspend=true`
   (for example `helm upgrade` with `--set suspend=true` at the new image tag). This scales
   `butlers-up`, `butlers-control`, `dashboard-api`, the frontend and every connector to 0 and
   suspends the backup and other CronJobs. Pause any automation that would re-apply the chart
   without `suspend=true` (a scheduled deploy, a GitOps controller). Wait until no pod and no
   running Job is left in the namespace, and stop any exporter, shell or tool connected from
   outside it.
4. **Run the migration once, alone.** Nothing in the chart runs while suspended, including the
   `dashboard-api` `migrations` initContainer. Run `butlers db migrate` once as a one-shot pod or
   Job using the same image, environment and Secrets as that initContainer. A daemon can open its
   runtime pool before fallback migration, so use the one-shot command without retained target
   connections. If G refuses, address the reported code and rerun.
5. **Start the release.** Deploy the same release with `suspend=false` and resume what step 3
   paused. The `dashboard-api` initContainer's `butlers db migrate` is now a no-op at head. Never
   start an image older than G afterward: that is unsupported and outside what G can prove.

An automatic migration path that reaches G while clients are connected refuses and leaves the
database at `rel_046`.

## Refusals

| Condition | Code | Effect |
| --- | --- | --- |
| Another session holds a lock on `relationship.entity_facts` | `lock_unavailable` | No DDL; stop the holder and retry. |
| Any other client connected to the database | `active_writer` | No DDL; stop the client and retry. |
| Migration login lacks effective statistics access, or an activity row is masked, incomplete or unknown | `db_activity_unverifiable` | Check the login's access and resolve unidentified activity. |
| Legacy or occurrence index missing, changed or invalid | `cutover_index_state_invalid` | No DDL. |
| Offline SQL generation reaches G | `offline_cutover_unsupported` | The cutover requires an online migration. |

Every refusal leaves indexes, data and the applied revision unchanged.

A database-scoped autovacuum worker can report a database without a login identity. G refuses
that incomplete row as `db_activity_unverifiable`. Retry the one-shot command after the worker
finishes.

## Rollback

- **Before G commits:** earlier transition-release rollback follows the existing `rel_035` rules in
  [Relationship Butler](../butlers/relationship.md#effective-time-on-structural-facts).
- **After G, before the first temporal write:** stop every client again and run G's downgrade. It
  recreates `uq_ef_spo_active` only if no row in any validity carries a temporal value and no SPO
  has two active rows, then the earlier release may start.
- **After the first temporal write:** downgrade and old-release rollback are refused
  (`temporal_rollback_prohibited`). Roll forward, or follow a separately reviewed data-preserving
  plan. Never delete, supersede, collapse, or clear rows to make the legacy index buildable.

## Related

- [Relationship Butler](../butlers/relationship.md#effective-time-on-structural-facts)
- [Kubernetes Deployment](kubernetes-deployment.md)
- [Backup and Restore](backup-restore.md)
