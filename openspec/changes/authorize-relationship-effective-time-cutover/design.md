## Context

`relationship-fact-effective-time` (design "Migration and Compatibility Plan", steps 3 and 4) makes
the legacy-index drop conditional on proof that every Relationship instance runs the transition
writer plus every inventoried mutator behavior or fence, that old images are absent, that the static
inventory is clean, and that the named real-PostgreSQL tests pass. It names what must be proven, not
how.

The first draft of this design proved it with a signed receipt, a root-held signer, an isolated test
runtime and a Docker Compose lifecycle fence. On 2026-10-20 the owner chose a plain hard cutover
instead (`bu-4awqyt`, choice B). That draft remains in git history before this amendment
(`bu-mw7muo`); nothing in it is carried forward except what this design restates.

Repository facts this design relies on:

- `rel_035` retains `uq_ef_spo_active`. The transition writer and every fenced mutator detect it with
  `legacy_spo_index_present` (`roster/relationship/tools/fact_temporal.py`) and refuse temporal
  intent `temporal_cutover_pending`. Index absence is the only capability signal.
- The static guard `tests/contracts/test_entity_facts_mutator_inventory.py` scans `roster/relationship`
  and `src/butlers` and requires its inventory to equal the found mutators exactly.
- Every writer of `relationship.entity_facts` ships in the Relationship release image.
- Migrations run online from daemon startup, `butlers db migrate`, and the deployment's migration run
  (on k3s, the `dashboard-api` initContainer). Every chain upgrades to `<chain>@head`.

## Goals / Non-Goals

**Goals:**

- Let the database prove no old writer is present while the legacy index changes.
- Keep the compatible-mutator inventory complete and tested in the release that carries G.
- One ordinary migration path for every database, fresh or real.
- The adopted rollback boundary, expressed as a checkable predicate.

**Non-Goals:**

- Writing G or its tests (owned by `bu-h3b7t`).
- Any signer, receipt, receipt verifier, isolated test runtime or orchestrator fence.
- Running any migration, deployment or client stop or start. Rolling out G is an owner act.
- Changing adopted `relationship-facts` requirement bodies.

## Decisions

### D1. The database proves no writer is present

G takes `LOCK TABLE relationship.entity_facts IN ACCESS EXCLUSIVE MODE NOWAIT`, then counts client
backends on `current_database()` other than its own (`pg_stat_activity`), and refuses `active_writer`
if any exists. It runs as the migration login role, never a `butler_*_rw` runtime role, and that
role needs `pg_read_all_stats` (or superuser): without it PostgreSQL masks other roles' sessions, so a
naive count could see zero clients beside live writers. G therefore refuses
`db_activity_unverifiable` when the privilege is missing or any other row's database, user or
backend type is unreadable. It holds the lock through the DDL and commit, so a client that connects after the check
cannot touch the table until G commits. Session counting cannot tell readers from writers, so any
other client refuses G, and the rollout stops every client of the database first. That stops every
butler: whole-assistant downtime for the migration is accepted.

Alternatives rejected: a signed receipt plus fence (the owner's A, never delivered); an advisory lock
alone (old code never takes it).

### D2. The inventory travels with G

The compatible mutators and G ship in one release, from one commit whose static inventory guard is
clean and whose named real-PostgreSQL scenarios pass in required checks. Code identity needs no
runtime proof: after the rollout only the release that carries G starts.

### D3. G is an ordinary revision

G sits in the ordinary Relationship chain with no gated ceiling, explicit argument or fresh-database
exemption. Every online path traverses G through the same lock-and-refuse check. On a fresh or CI
database nothing else is connected and G applies. On a live database an automatic path reaches G
while clients are connected, refuses, and leaves the revision at G's predecessor, which is the
signal to run the hard-cutover rollout. Offline SQL generation refuses G because the check cannot
run offline.

### D4. Reconciling the parent's old-images-absent precondition

The parent requires every old image proven absent and the exact deployed image proven to carry the
complete inventory. Under B, absence is proven at the only moment it matters, the index drop, by D1:
no other client is connected to the database. The inventory is proven by D2 at the commit that
carries G. Starting an image older than G after G commits is outside the supported rollout and is
stated as residual operator risk. A pre-transition writer whose `ON CONFLICT` target is inferred
from the legacy index fails once G drops it, rather than writing.

### D5. Rollback is bounded by the first temporal write

The G downgrade runs under the same stop-every-client rollout and lock-and-refuse check, verifies zero
temporal-bearing rows and zero duplicate active SPOs, and recreates `uq_ef_spo_active` in the same
transaction. Otherwise it refuses `temporal_rollback_prohibited`; recovery rolls forward.

## Refusal codes

| Code | Condition |
| --- | --- |
| `lock_unavailable` | The `NOWAIT` lock on `relationship.entity_facts` fails |
| `active_writer` | Another client backend is connected to the target database |
| `db_activity_unverifiable` | Migrating role lacks `pg_read_all_stats`, or another session row is masked |
| `cutover_index_state_invalid` | The legacy index or occurrence index is missing, changed or invalid |
| `temporal_rollback_prohibited` | Downgrade after the first temporal write |

Every refusal happens before DDL and leaves indexes, data and the applied revision unchanged.

## Rollout (owner act, outside this change)

1. Confirm the release carrying G passed required checks, and that the migration login role has
   `pg_read_all_stats`.
2. Take a backup of the target database and verify it restores, because after G recovery is
   roll-forward only.
3. Stop every client of the target database and keep it stopped: scale every Deployment to 0,
   suspend CronJobs, and pause anything (Helm, ArgoCD) that would restore replicas.
4. Run the migration once, alone, as its own one-shot run.
5. Start the release that carries G and resume what step 3 paused. Never start an older image
   afterward.

`docs/operations/relationship-effective-time-cutover.md` carries the k3s commands.

## Risks / Trade-offs

- Whole-assistant downtime for the migration window: accepted by the owner.
- A forgotten client (an exporter or a stray shell) blocks G: it refuses `active_writer` with no
  change, and the operator stops the client and retries.
- An operator who starts an image older than G after G commits bypasses the rule: unsupported and
  stated in the rollout notes. The pre-cutover central writer fails closed on its missing conflict
  target.
- Automatic migration paths fail at G until the rollout runs: intended, since G must not run beside
  live clients.

## Open Questions

None. This text conforms to the owner's recorded `bu-4awqyt` answer B.
