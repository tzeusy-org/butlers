# Public MCP Read Surface: Setup and Upkeep

> **Purpose:** Set up the public MCP read surface on a fresh environment, keep it in sync, and
> handle its incidents: the second database `butlers_public`, its replication from the primary,
> and the public server.
> **Audience:** The owner, and agents preparing steps for the owner. On dev, `make deploy-dev`
> runs the two superuser steps (`init-db.sql` and the owner step) itself (owner decision OD20).
> Agents never run a superuser step by hand; they prepare and verify it.
> **Design:** [RFC 0039](../../about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md) is
> normative. This page orders the work and says how to check each step. Where the two disagree,
> the RFC wins and this page is fixed.

---

## Status: primary foundation; subscriber and public runtime pending

The source now provides the canonical full-v1
`src/butlers/knowledge_read/projection.toml`, its closed immutable model, catalog validation,
and explicit `butlers public-mcp reconcile --database butlers`. No command runs automatically
at daemon startup, during migrations, or in Compose. This foundation implements only the primary
part of `bu-y66o00.4` and part of `bu-y66o00.3`; neither issue is complete.

With all five CNPG-managed roles and `butlers_public` already present, the privileged
`init-db.sql` bootstrap supplies `mcp_surface.controls` and `manifest_state`, owned by the
configured migration principal, and its primary database CREATE privilege. Absent those exact
prerequisites, projection additions are skipped. Existing foreign owners, unsupported shapes and
unrecognized ACLs refuse; runtime/PUBLIC metadata grants are revoked on rerun, with Relationship
retaining USAGE and SELECT on controls only. Managed role attributes are untouched by these additions.

Run reconcile explicitly as that ordinary migration principal **after all source chains**
(core, domain, memory) have completed. It validates every source before publication DDL, takes a
transaction-scoped advisory lock with a five-second lock timeout, and limits each statement to
60 seconds. It converges only `butlers_public_v1` and `mcp_surface.manifest_state` in one
transaction. Readback checks action flags, exact table membership, explicit finite `prattrs`
column lists and closed row filters; all-table and schema wildcards refuse. A matching digest
alone never qualifies as a no-op. Failure rolls back publication and digest together. No slot,
subscription, subscriber connection or grant/owner step is performed by reconcile.

The manifest is the single exposure decision: 46 entries declare 469 columns, including
controls/state. Registry validation cannot add a predicate; future columns stay private until
an explicit edit. Finance/Health JSONB arrives through the existing credential-filtered scalar
materializations, never as JSONB columns. Graph keys derive from the same fact predicate lists.
The packaged resource is read without a mutable external manifest override.

**Still pending:** publisher grant synchronization, subscriber/owner-sql, request/canary/digest
serving semantics, actual named read-consumer and manifested subscriber comparisons, public
server/chart, deployed privileges/network and live owner steps. The deployed API and migrations
currently share a configured database login and the API does not SET ROLE. SQL ACLs cannot
establish controls-only API writes while that effective identity also owns manifest state;
this remains an explicit required runtime identity boundary. Synthetic bootstrap/catalog tests
prove their source scope only. No deployment or actual owner step is established by this change.

The remaining sections retain the full target procedure and its pending ownership below.

Every step below carries one of two tags:

- **Now.** Runnable today.
- **Lands with `bu-y66o00.N`.** The design's command or object, which does not exist until that
  bead merges. Until then, do not improvise a substitute (for example, a hand-written
  subscription): the design depends on exact step order and on the guard checks inside the
  generated file. The implementing bead updates this page in the same PR.

**Environments.**

| Environment | Butlers namespace | CNPG cluster | Public namespace | State |
|---|---|---|---|---|
| dev (the live system) | `butlers-dev` | `postgres-dev` | `butlers-public-dev` (created by the public chart) | Homelab acts 2 and 3 applied (Tzeusy/homelab#40); Butlers side not built |
| prod | `butlers` | [Unknown] | not named | Deferred by owner decision OD18; nothing on this page runs against prod |

[Observed] The prod database location is unresolved: `deploy/helm/butlers/values.prod.yaml:1-4`
points the prod release at `postgresql-cloudnativepg-rw.postgres.svc.cluster.local`, while RFC
0039 D3 records facts for a cluster named `postgres-prod`. Resolve that before any prod plan.

**Rules for every step.**

- Every step is safe to rerun. A step that stops midway is fixed and rerun, never patched by hand.
- Secrets are handled by name only. No step prints a password, and no check reads Secret data in
  `postgres-<env>`.
- The superuser path is `kubectl exec` into the CNPG primary pod with peer authentication.
  Whoever can do that already holds superuser-equivalent access (RFC 0039 D9, residual risk).

## What a fresh environment needs

In dependency order. Each row is one section below.

| # | Element | Owner | Declared in | Tag |
|---|---|---|---|---|
| 1 | Preflight: cluster facts the design assumes (before act 2), then the act 2 result (after) | owner (read-only) | none | Now |
| 2 | `butlers_public`, five roles, `pg_hba`, `pg_ident`, slot WAL cap, password Secrets (owner act 2) | homelab | CNPG chart values | Now |
| 3 | NetworkPolicy check on `postgres-<env>` (owner act 3) | homelab | CNPG namespace | Now |
| 4 | Publisher grants and `statement_timeout` in `init-db.sql` (owner act 9) | Butlers, run as superuser | `scripts/init-db.sql` | Lands with `bu-y66o00.4` |
| 5 | Publication `butlers_public_v1` and `mcp_surface.manifest_state` (reconcile) | Butlers, migrations | `projection.toml` | Lands with `bu-y66o00.4` |
| 6 | Slot, subscription, subscriber DDL, applied digest (owner act 10, the owner step) | superuser: `make deploy-dev` on dev (OD20), the owner elsewhere | generated by `owner-sql` | Lands with `bu-y66o00.4` |
| 7 | Public chart: namespace, ExternalSecret, bearer digest, keyring | Butlers | `deploy/helm/butlers-public-mcp` | Lands with `bu-y66o00.6` |
| 8 | Alert rules and the two Uptime Kuma monitors (owner act 7) | Butlers rules, homelab monitors | rules: [Unknown] chart; monitors: homelab | Lands with `bu-y66o00.7` |
| 9 | Gateway: Worker key and signing, filter-proxy release, `MCP_SERVERS`, client consent (owner acts 4-6, 8) | homelab | homelab repo | Owner, after 7 |

## 1. Preflight (read-only)

**Now.** Run the "Before act 2" rows before the homelab acts on a new cluster. Run the "After act 2"
rows once section 2 is applied. Run both again after a CNPG upgrade or a node change, because the
design rests on CNPG internals and on the node's pod range. On a fresh cluster the "After act 2"
rows fail by design until section 2 is done.

Open a superuser `psql` the way `scripts/k8s/bootstrap-db.sh:40-54` does, selecting the primary by
label rather than by a fixed pod name:

```bash
primary="$(kubectl -n postgres-dev get pod \
  -l cnpg.io/cluster=postgres-dev,cnpg.io/instanceRole=primary -o name)"
kubectl -n postgres-dev exec -i "$primary" -c postgres -- psql -X -d butlers -c "<query>"
```

**Before act 2.** Cluster facts the design assumes; nothing here depends on the homelab acts.

| Check | Query or command | Expected on dev | Design reference |
|---|---|---|---|
| Version and WAL level | `SHOW server_version;` `SHOW wal_level;` | 17.x; `logical` | D3 |
| Slot and sender headroom | `SHOW max_replication_slots;` `SHOW max_wal_senders;` and `SELECT count(*) FROM pg_replication_slots;` | 32; 10; well under 32 in use | D9 slot safety |
| Socket directory | `SHOW unix_socket_directories;` | includes `/controller/run` (the subscription's conninfo) | D3 step 8 |
| Logical slots before first sync | `SELECT slot_name, slot_type, plugin FROM pg_replication_slots;` | no logical slot | D9 control 3 |
| Pod range and node count | `kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.podCIDR}{"\n"}{end}'` | one node, `10.42.0.0/24` | D3 item 1 |

**After act 2.** The state section 2 declares. Section 2's confirmation reruns these rows.

| Check | Query or command | Expected on dev | Design reference |
|---|---|---|---|
| Slot WAL cap | `SHOW max_slot_wal_keep_size;` | `10GB` | D9 slot safety, control 4 |
| `pg_ident` `local` map | `SELECT sys_name, pg_username FROM pg_ident_file_mappings WHERE map_name = 'local' ORDER BY 2;` | exactly `postgres postgres` and `postgres butlers_projection_publisher` | D9 control 1 |
| `pg_hba` order | `SELECT line_number, type, database, user_name, address, auth_method FROM pg_hba_file_rules ORDER BY line_number;` | the D3 listing: the two `hostssl butlers_public` allows, then the three `reject` lines, before the `scram-sha-256` catch-all | D3 item 1 |
| Roles | `SELECT rolname, rolcanlogin, rolinherit, rolreplication, rolsuper, rolcreatedb, rolcreaterole, rolconnlimit FROM pg_roles WHERE rolname LIKE 'mcp\_%' OR rolname = 'butlers_projection_publisher' ORDER BY 1;` | the five roles with the attributes in RFC 0039 owner act 2; none superuser, `CREATEDB` or `CREATEROLE`; `mcp_public_ro` limit 10 | D9 |
| Publisher has no credential | `SELECT rolpassword IS NULL FROM pg_authid WHERE rolname = 'butlers_projection_publisher';` and `SELECT count(*) FROM pg_auth_members WHERE roleid = 'butlers_projection_publisher'::regrole;` | `t`; `0` | D9 control 2 |
| Projection database | `SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'butlers_public';` | `mcp_projection_owner` | D3 |
| Password Secrets, by status only | `kubectl -n postgres-dev get externalsecret` and `kubectl -n butlers-dev get externalsecret` | the role ExternalSecrets `Ready`; key names `username`, `password` (D9 custody table) | D9 credential custody |

A second node gets a pod range outside the `pg_hba` allow, so projection logins from it fail
closed until the owner widens the rule (D3 item 1). Any other mismatch goes back to section 2.

## 2. Homelab side (owner, declarative)

**Now.** These acts belong to the homelab repository, not Butlers (OD5). They are per
environment: a new cluster declares them again with its own parameters.

**Owner act 2: CNPG.** [Observed: Tzeusy/homelab#40 file list] Declared in
`helm/postgresql_cloudnativepg/values.dev.yaml` of the homelab repo, rendered by the chart's
`templates/cluster.yaml`, `databases.yaml` and `external-secrets.yaml`, and applied with
`make helm_install_dev` (the chart README, "Standalone roles and databases"). The keys:

| Key | Holds | Per-environment parameter |
|---|---|---|
| `cluster.postgresql.parameters.max_slot_wal_keep_size` | `10GB` | size against the host disk's free space (D3, shared disk) |
| `cluster.postgresql.pgHba` | the ordered allow and reject lines of D3 item 1 | the node pod range (`10.42.0.0/24` on dev) |
| `cluster.postgresql.pgIdent` | `local postgres butlers_projection_publisher` | none |
| `extraDatabases` | `butlers_public`, owner `mcp_projection_owner` | none |
| `managedRoles` | the five roles, with `externalSecret.targets` for the three password roles | target namespaces (`postgres-<env>`, `butlers-<env>`) and the Bitwarden project |

[Observed: Tzeusy/homelab#41] The `butlers` role itself is no longer a CNPG managed role on
`postgres-dev`; do not add it back on a new cluster.

**Owner act 3: NetworkPolicy.** Add the one allow for the public pod on 5432 only if a
default-deny ingress policy already selects the instance pods (D8 item 3). Check with
`kubectl -n postgres-dev get networkpolicy`. [Observed: homelab report, 2026-11-04] None selects
the `postgres-dev` pods, so dev needs no policy.

**Confirm from the Butlers side** by rerunning the "After act 2" rows of section 1, and the
"Before act 2" rows to catch a change the acts caused. Every row must match.

**Gateway acts 4-6 and 8** (Worker key and assertion signing, the `mcp` filter-proxy release on
the dev store, `MCP_SERVERS`, client consent) and **act 7** (the two Kuma monitors) come after
the public chart is up: see sections 3d and 3e and RFC 0039
[Rollout and owner acts](../../about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md#rollout-and-owner-acts).

## 3. Butlers side, in order

### 3a. Bootstrap: `init-db.sql` (owner act 9)

**Now** for the script as it is; **lands with `bu-y66o00.4`** (tasks 1.6 and 1.7) for its
projection grants: the publisher's `CONNECT` on `butlers` and its `USAGE` and `SELECT` sync, and
`ALTER ROLE mcp_public_ro SET statement_timeout = '2s'` (RFC 0039 D9).

- **On dev it is automatic.** `make deploy-dev` first runs `bootstrap-dev`, which pipes the
  deployed tag's `scripts/init-db.sql` through the superuser path
  (`deploy/helm/butlers/makefile` `deploy-%: bootstrap-%`;
  [Kubernetes Deployment](kubernetes-deployment.md#iterating-on-a-change)).
- **Elsewhere it is manual**, per [Reviewed bootstrap](../data_and_storage/reviewed-bootstrap.md).
- **Where the CNPG objects are absent** (Compose, testcontainers, a cluster before section 2),
  the projection statements must do nothing, so the same script keeps working everywhere it runs
  today (tasks 1.6 acceptance).
- **On a first deploy** the publication does not exist yet, so the publisher sync grants nothing.
  The owner step repeats the sync (D3 step 6), so nothing is lost.

Verify, as superuser:

```sql
SELECT has_database_privilege('butlers_projection_publisher', 'butlers', 'CONNECT');  -- t
SELECT rolconfig FROM pg_roles WHERE rolname = 'mcp_public_ro';  -- {statement_timeout=2s}
```

### 3b. Deploy the `butlers` chart: reconcile

**Lands with `bu-y66o00.4`** (task 1.4). The migrations initContainer of `dashboard-api` (what
RFC 0039 calls the migration Job) runs Alembic, then `butlers public-mcp reconcile` as the
migration user. Reconcile makes publication `butlers_public_v1` equal the manifest and writes the
digest into `mcp_surface.manifest_state` in one transaction. It touches no slot, subscription or
`butlers_public` object (D4, OD14). Deploy as usual
([Kubernetes Deployment](kubernetes-deployment.md#deploy)).

Verify, in `butlers`:

```sql
SELECT schemaname, tablename FROM pg_publication_tables
 WHERE pubname = 'butlers_public_v1' ORDER BY 1, 2;  -- the manifest's tables
TABLE mcp_surface.manifest_state;                    -- one digest row
```

The current foundation is explicit-only: Compose and ordinary migration fixtures do not
automatically reconcile. Future initContainer integration remains part of `bu-y66o00.4`.

### 3c. Owner step (owner act 10)

**Lands with `bu-y66o00.4`** (task 1.11). The file is computed from the manifest alone, holds no
secret, and stops on the first failure. Its guard refuses to run until 3b has reconciled the
publication for this manifest (D3 step 1). What the file does, in order, is RFC 0039 D3 "The
owner step".

**On dev it is automatic (OD20).** `make deploy-dev` (and `ship-dev`, and
`scripts/k8s/deploy-dev.sh`, which call it) runs, in order:

1. `bootstrap-dev`: `init-db.sql` as superuser (3a).
2. `record-deploy.sh`: `helm upgrade`, then the `dashboard-api` rollout, whose `migrations`
   initContainer runs Alembic and reconcile (3b), then `butlers-up`, `butlers-control` and
   readiness. Its exit status is kept, not acted on yet.
3. `scripts/k8s/public-mcp-owner-step.sh dev <tag>`, whatever step 2 returned. A release can fail
   after reconcile has run (a later rollout or the readiness wait), so the step must still purge
   a narrowing. It picks the running `dashboard-api` pod whose image tag is `<tag>` (and fails if
   none is), checks the pod's command listing for `owner-sql`, captures the whole file from that
   pod, and only then pipes it to the superuser `psql` in the primary. Nothing is written to disk.
   When the migrations did not reconcile this manifest, the file's guard refuses and changes
   nothing (D3 step 1).
4. After step 3 exits 0, the public chart `deploy/helm/butlers-public-mcp` at the same tag (3d),
   so the public pod ships the manifest just applied.

The target exits with the first non-zero status of these steps. The owner step runs on every dev
deploy; with an unchanged manifest and a healthy slot it changes nothing (D3 "Rerun"). Read its
outcome in the deploy output:

- **Success.** The step exits 0, the public chart follows, and the checks below pass.
- **`public-mcp owner step FAILED`.** The deploy exits non-zero. Nothing rolls back: the Butlers
  release, its deployments row and the publication as reconcile left it stand, and the public
  chart is not upgraded. What the server does depends on the run (D3 "Failure"):
  - after a manifest change, or when the run had already deleted the applied record (a rebuild),
    it refuses with `projection_apply_pending` (section 4) and the warning flag drops;
  - with an unchanged manifest and no rebuild, it keeps serving the applied projection, and the
    marker is the only report.

  Fix the cause, then rerun only the step with
  `scripts/k8s/public-mcp-owner-step.sh dev <deployed tag>`, or deploy again. A guard refusal
  after failed migrations is fixed by fixing the release.
- **A notice and exit 0.** The deployed image predates the command (a rollback to an older tag),
  or the off switch `PUBLIC_MCP_OWNER_STEP_dev` is `off` (section 7); nothing ran.

After a slot invalidation (`projection_stale` with `wal_status = 'lost'`), run the same script
at once rather than wait for the next deploy.

**Elsewhere it is manual** (prod is deferred, OD18). Run from the deployed image or a checkout at
the deployed revision. Capture the file first, so a failed generator runs nothing, and write
nothing into the checkout:

```bash
primary="$(kubectl -n postgres-<env> get pod \
  -l cnpg.io/cluster=postgres-<env>,cnpg.io/instanceRole=primary -o name)"
sql="$(butlers public-mcp owner-sql)" && printf '%s\n' "$sql" | \
  kubectl -n postgres-<env> exec -i "$primary" -c postgres -- \
  psql -X -U postgres -v ON_ERROR_STOP=1 -d butlers -f -
```

Verify, as superuser:

```sql
-- in butlers
SELECT subname, subenabled, subowner::regrole, subslotname FROM pg_subscription;
  -- butlers_public_sub | t | mcp_subscription_owner | butlers_public_sub
SELECT slot_name, plugin, active, wal_status FROM pg_replication_slots WHERE slot_type = 'logical';
  -- butlers_public_sub | pgoutput | t | reserved or extended, never lost;
  -- no pg_*_sync_* slot once the copy is done
-- in butlers_public (\connect butlers_public)
SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;
  -- only state r once the initial copy is complete
TABLE mcp_read.manifest_applied;
  -- the applied digest equals the digest in butlers mcp_surface.manifest_state, copy complete
```

A rerun with an unchanged manifest and a healthy slot changes nothing (D3 "Rerun"). If a run stops
midway after a manifest change or after deleting the applied record, the record is gone or old,
so the server refuses with `projection_apply_pending`; with an unchanged manifest and no rebuild
the record still matches and the server keeps serving. Either way, fix the cause and run the step
again (on dev, the script above).

### 3d. Install the public chart

**Lands with `bu-y66o00.6`** (task 2.4). The chart creates namespace `butlers-public-<env>` and an
ExternalSecret for `MCP_PUBLIC_RO_DB_PASSWORD`, and mounts the hop-bearer digest, the verifier
keyring and the break-glass `disabled_domains` ConfigMap (D8). On dev the chart is installed by
the `deploy-%` recipe after a successful owner step, at the same tag (3c step 4; RFC 0039 D3
"The public chart"), so a manifest change needs no separate public deploy. [Unknown] The rest of
the install path (values file, image build) is not specified yet; `bu-y66o00.6` adds it here and
in `deploy/helm/butlers-public-mcp/README.md`.

Verify: run the live NetworkPolicy check (D8 item 6), then the task 2.5 smoke test from a test pod
in `mcp`: `describe` answers, and every other tool either serves or refuses with one of the codes
in [section 4](#4-fail-closed-states).

### 3e. Alerts and monitors

**Lands with `bu-y66o00.7`** (task 3.2). Butlers ships the alert rules and the two flag recording
rules `butlers_public_mcp:critical_ok` and `butlers_public_mcp:warning_ok` (D16). [Unknown] Which
chart renders them is not decided. Homelab adds one Uptime Kuma `json-query` monitor per flag to
`ansible/roles/proxmox/files/uptimekuma-monitors.toml` (owner act 7; the critical one at
`interval = 60`). Test-fire: force each flag to 0 with a synthetic condition and watch the monitor
go DOWN and the Telegram notification arrive.

Then the gateway acts 4-6 and client consent (act 8). On dev the first connection exposes live
data, which the owner accepted (OD12).

## 4. Fail-closed states

What a refusal means on a fresh or changing environment, and which step fixes it. The codes and
their precedence are RFC 0039 D17; the mapping to setup steps is [Inferred] from its failure
table.

| Refusal | Likely missing or broken step | Fix |
|---|---|---|
| `projection_apply_pending` | The owner step has not run since the first sync or the last manifest change, stopped midway, or the initial copy is not complete | Run 3c; wait for every relation to reach state `r` |
| `projection_version_mismatch` | Deploy skew: the public pod ships a different manifest from the replicated digest | On dev, the last deploy's owner step failed, so its public chart step did not run: fix 3c and deploy again. Elsewhere, finish the `butlers` rollout (3b) or deploy the public chart at the same revision |
| `projection_stale` | Replication older than 15 minutes, or `replication_as_of()` NULL (no subscription, or it never confirmed a position) | Check the slot; if `wal_status = 'lost'`, rerun 3c, which rebuilds the slot and subscription |
| `unavailable` | No connection to `butlers_public`: `postgres-<env>` down, the connection limit reached, a `pg_hba` refusal (for example, a pod on a second node), or the password Secret missing | Section 1 checks; the ExternalSecret status in `butlers-public-<env>` |
| `domain_disabled` | A kill switch is off | Intended, or see section 6 |
| `blocked` | The canary latch holds | Section 6, leak runbook |
| JSON-RPC `-32001` | Bearer or assertion refused | Gateway acts 4-6; bearer digest and keyring in 3d |

## 5. Upkeep cadence

**Lands with `bu-y66o00.4` and `bu-y66o00.7`.** The owner step must run (RFC 0039 D3 "Cadence"):

- once at the first sync;
- once after each manifest change that 3b has applied;
- once after each slot invalidation.

**On dev this needs no owner act** (OD20). A manifest change reaches dev only through a deploy,
and every `make deploy-dev` runs the step after its migrations, whatever the release returned,
and then the public chart at the same tag (3c), so the first sync and every manifest change are
covered. Only a slot invalidation between deploys calls for a manual run of
`scripts/k8s/public-mcp-owner-step.sh dev <deployed tag>`. **Elsewhere** it stays the one
recurring owner act.

The server announces a due run: the `butlers_public_mcp_owner_step_pending` gauge is 1 with
`change="narrowing"` or `change="widening"`, the warning flag drops, and a narrowing left pending
for 24 hours turns the critical flag (D16). On dev the pending state normally lasts only between
a deploy's reconcile and its owner step, so a warning that outlives the deploy, or the 24-hour
critical, means the automatic step is failing: read the deploy's `public-mcp owner step FAILED`
output (a guard refusal means the migrations did not reconcile) and rerun the script after the
fix. A pending state with no `FAILED` marker in the last deploy means the off switch is `off` or
no dev deploy has run since the change. A slot invalidation shows as `projection_stale` with
the slot and lag alerts instead.

The `init-db.sql` rerun needs no cadence on dev, because every `make deploy-dev` repeats it (3a).
Rerun section 1 after a CNPG upgrade (the `local` map and the socket path are CNPG internals) and
after adding a node (the pod range) [Inferred].

## 6. Incidents

**Lands with `bu-y66o00.7`** (task 7.2), which fills this section: flipping each kill-switch
layer, rotating the bearer and keyring, draining or flushing the inbox, and the canary latch. The
switches and the leak runbook are RFC 0039 D16; the leak runbook ends with the owner step (3c),
which purges the narrowed rows. On dev the deploy that ships the narrowed manifest runs it, even
when that deploy's release fails after its migrations; if the deploy prints `public-mcp owner
step FAILED`, the purge did not happen, so fix the cause and rerun
`scripts/k8s/public-mcp-owner-step.sh dev <deployed tag>` before re-enabling.

## 7. Teardown and rebuild

**Lands with `bu-y66o00.7`** (task 8.1). On dev, first merge a change that sets
`PUBLIC_MCP_OWNER_STEP_dev := off` in `deploy/helm/butlers/makefile` (RFC 0039 D3 "Off switch").
Without it, the next `make deploy-dev` recreates the slot and subscription, re-copies the
published data into `butlers_public` and reinstalls the public chart. Reconcile still recreates
the publication, which is inert without a slot or subscription. Rollback order: disable `all`,
scale the public Deployment to 0, uninstall the public chart, then as superuser drop the
subscription (which also drops its slot and table-sync slots) and the publication, and confirm no
`butlers_public_sub` or `pg_<oid>_sync_%` slot remains. Then run one `make deploy-dev` and
confirm it recreates no slot, subscription or public chart. Removing `butlers_public`, the roles
and the `pg_hba` lines reverses owner act 2 in homelab.

Rebuild by setting the off switch back to `on` and running sections 1, 3a, 3b and 3c again; the end state is the applied digest equal to
the manifest digest and both flags at 1.

**Restore drill.** CNPG backups include `butlers_public`, but nothing restores from it. A restored
instance drops `butlers_public` or keeps its subscription disabled, so a restored copy never
replicates from a live primary (D3 "Shared fate and capacity";
[Backup and Restore](backup-restore.md)).

## 8. Custody

Credential names, holders and mounts are the RFC 0039 D9 custody table; record names here, never
values. The publisher has no credential at all (OD14). `kubectl exec`, attach and debug on the
instance pods, pod creation, Secret read and `Cluster` writes in `postgres-<env>`, and node access
are already superuser-equivalent (D9, residual risk), so they stay with the owner.

On dev the owner step's superuser SQL is emitted by the deployed `dashboard-api` pod (3c), so it
trusts that image and pod, which run privileged; setting `dashboardApi.privileged=false` would not
remove that trust. The tag check and the captured output narrow it to the deployed tag's pod and
a complete file (RFC 0039 D9, residual risk).

## Related

- [RFC 0039](../../about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md): D3 placement and
  the owner step, D8 network, D9 roles and custody, D16 alerting, D17 failure modes, Rollout.
- [Deployment topology](../../about/lay-and-land/deployment.md): where each piece runs.
- [Kubernetes Deployment](kubernetes-deployment.md) and
  [Reviewed bootstrap](../data_and_storage/reviewed-bootstrap.md): the `butlers` release and
  `init-db.sql`.
- OpenSpec change `openspec/changes/add-public-mcp-read-surface/` (`tasks.md` is the work map).
