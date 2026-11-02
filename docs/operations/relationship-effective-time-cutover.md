# Relationship Effective-Time Cutover

> **Status:** unimplemented. Symbolic migration G does not exist yet (`bu-h3b7t`). Nothing on this
> page can be executed today, and nothing here authorizes a deployment, client stop or start,
> migration, or temporal activation. Rolling out G is an owner act.
> **Contract:** [`authorize-relationship-effective-time-cutover`](../../openspec/changes/authorize-relationship-effective-time-cutover/design.md)
> on top of [`relationship-fact-effective-time`](../../openspec/changes/relationship-fact-effective-time/design.md).
> **Decision:** plain hard cutover, owner answer B on `bu-4awqyt` (2026-10-20). The earlier signed
> receipt, root signer, isolated test runtime, Compose fence and Kubernetes extension are dropped.

Actual `rel_036` is the immutable meeting-debrief migration. G is a then-free cutover revision
after the actual then-current Relationship head, not a reserved number or replacement for `rel_036`.

## What the cutover does

`rel_035` added effective-time columns and the occurrence index but kept
`relationship.uq_ef_spo_active`. While that index exists, every Relationship mutator refuses temporal
intent with `temporal_cutover_pending`. G drops only that index, which is the sole signal that
enables temporal assertions, corrections, and repeated periods.

Old code must be gone first: pre-transition writers and unfenced mutators collapse or overwrite
occurrences once the index is absent. The database proves it: before any DDL, G takes an exclusive
no-wait lock on `relationship.entity_facts` and refuses while any other client is connected to the
database. Every writer ships in the Relationship release, and the release that carries G carries
the complete compatible mutator inventory
(`tests/contracts/test_entity_facts_mutator_inventory.py`).

## Rollout

The rollout costs whole-assistant downtime: stopping every database client stops every butler.

1. Confirm the release carrying G passed its required checks.
2. Stop every client of the target database: every butler, connector, the dashboard API, cron
   jobs, exporters and stray shells.
3. Run the migration once, alone. On k3s, migrations run in the `dashboard-api` initContainer
   (see [Kubernetes Deployment](kubernetes-deployment.md#deploy)).
4. Start the release that carries G. Never start an image older than G afterward: that is
   unsupported and outside what G can prove.

Until the rollout runs, any automatic migration path that reaches G while clients are connected
refuses and leaves the database at G's predecessor. That is expected, not a fault to work around.

## Refusals

| Condition | Code | Effect |
| --- | --- | --- |
| Another session holds a lock on `relationship.entity_facts` | `lock_unavailable` | No DDL; stop the holder and retry. |
| Any other client connected to the database | `active_writer` | No DDL; stop the client and retry. |
| Migration role cannot read session activity | `db_activity_unverifiable` | No DDL. |
| Legacy or occurrence index missing, changed or invalid | `cutover_index_state_invalid` | No DDL. |
| Offline SQL generation reaches G | refusal | No SQL emitted. |

Every refusal leaves indexes, data and the applied revision unchanged.

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
