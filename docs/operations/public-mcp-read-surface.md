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

## Status: live on dev behind the gateway

As of 2026-11-22 the whole surface runs on dev:

- **Projection.** Every `make deploy-dev` reconciles the publication and runs the owner step
  (3b, 3c).
- **Public server.** The public chart (3d) runs in `butlers-public-dev` with the verifier keyring
  installed (owner act 4) and the site audience set. Clients reach it only through the gateway
  endpoint (owner acts 5, 6 and 8). The chart is upgraded by hand: `PUBLIC_MCP_CHART_dev` is still
  `off` in `deploy/helm/butlers/makefile`, so `make deploy-dev` does not upgrade it (3c step 4;
  switching it on is `bu-y66o00.67`).
- **Clients.** claude.ai is connected; its first signed call answered `describe` on 2026-11-22.
  ChatGPT is next (task 3.1).
- **Alerts.** The `PrometheusRule` and `PodMonitor` ship with the chart, both flags read 1, and
  the two Uptime Kuma monitors poll them (3e).
- **Open.** The ChatGPT connection, the dev rollback rehearsal that section 7 scripts (task 8.1),
  the widening and narrowing rehearsal (task 8.2), the security review (task 6.1) and the metric
  sources 3e lists as gaps.

The rest of this section says what each piece does.

The source provides the canonical full-v1
`src/butlers/knowledge_read/projection.toml`, its closed immutable model, catalog validation,
and `butlers public-mcp reconcile --database butlers`. On dev the `migrations` initContainer
runs reconcile after Alembic (`publicMcp.reconcile`, true only in `values.dev.yaml`), and every
`make deploy-dev` then runs the owner step (3c). Nothing runs it at daemon startup or in Compose,
and the chart default leaves reconcile off elsewhere.

With all five CNPG-managed roles and `butlers_public` already present, the privileged
`init-db.sql` bootstrap supplies `mcp_surface.controls` and `manifest_state`, owned by the
configured migration principal, and its primary database CREATE privilege. Absent those exact
prerequisites, projection additions are skipped. Existing foreign owners, unsupported shapes and
unrecognized ACLs refuse; runtime/PUBLIC metadata grants are revoked on rerun, with Relationship
retaining USAGE and SELECT on controls only. The bootstrap preserves externally managed identities and refuses a publisher credential,
membership or incompatible peer/HBA configuration. It sets the public reader timeout to
two seconds and synchronizes publisher CONNECT, schema USAGE and table SELECT to the
actual publication, revoking its other schema/table/column/sequence grants. Only the named
publisher SELECT/USAGE metadata grants are recognized on rerun; other foreign ACLs refuse.

Run reconcile explicitly as that ordinary migration principal **after all source chains**
(core, domain, memory) have completed. It validates every source before publication DDL, takes a
transaction-scoped advisory lock with a five-second lock timeout, and limits each statement to
60 seconds. It converges only `butlers_public_v1` and `mcp_surface.manifest_state` in one
transaction. Readback checks action flags, exact table membership, explicit finite `prattrs`
column lists and closed row filters; all-table and schema wildcards refuse. A matching digest
alone never qualifies as a no-op. Failure rolls back publication and digest together. No slot,
subscription, subscriber connection or grant/owner step is performed by reconcile.

The manifest is the single exposure decision: 47 entries declare 472 columns, including
controls/state. Registry validation cannot add a predicate; future columns stay private until
an explicit edit. Finance/Health JSONB arrives through the existing credential-filtered scalar
materializations, never as JSONB columns. Graph keys derive from the same fact predicate lists.
The packaged resource is read without a mutable external manifest override.

The connection-free renderer `butlers.migrations.public_mcp_owner` supplies the subscriber
database. `butlers public-mcp owner-sql` prints its file, and on dev
`scripts/k8s/public-mcp-owner-step.sh`, run by `make deploy-dev`, pipes that file to the CNPG
primary as the superuser (3c). The same recipe then upgrades the public chart at the same tag
when `PUBLIC_MCP_CHART_dev` is `on`, so the shipped, replicated and applied digests converge in
one deploy (RFC 0039 D3 "The public chart"); while that switch is `off` the chart is upgraded by
hand (3d step 5).

The renderer validates the reconciled digest and actual publication, primary types/keys,
credentialless publisher, finite WAL cap and closed existing subscriber catalog before
mutation. Applied JSON is decoded with the same closed version-1 semantics as the pure
transition comparator. Rebuild and narrowing are separate decisions. Rebuilds capture
validated identities and delete the applied row atomically before disabling, disassociating
and dropping the subscription. Cleanup targets only the exact main slot and sync slots whose
subscription OIDs were captured from the validated catalog. Unrelated slots and backends
remain untouched. The final applied write is last; enabled subscription and all expected
relation states `r` independently determine initial-copy completeness. An unchanged healthy
rerun preserves the applied row and view/subscription OIDs.

An owner-only `mcp_local.rebuild_state` journal retains database/subscription identity, an
attempt UUID, target digest, at most 16 validated canonical declarations and at most 16
captured subscription OIDs. Its closed columns, constraints, ownership and ACLs are checked;
malformed, foreign or conflicting live identities refuse. Capture and applied-row deletion
share one transaction, so interruption cannot lose the exact old cleanup identities. Resume
retains them even if the target changes. Final applied write and journal removal share one
transaction; a journal is never evidence of completion and has no reader/importer grant.

Subscriber-local `mcp_read` views use a fixed `(source_id text, fields jsonb)` output, plus the
declared search columns of an indexed search source (below). Fields are explicitly declared,
never whole-row discovery. Numeric values are represented
as exact decimal text, nulls remain null, composite source IDs use JSON arrays, and graph
source prefixes map to manifest domains without exposing `publish_key`. Entity deletion,
unidentified and placeholder flags are excluded from every read view. Finance transactions
hide soft-deleted rows. These local representations do not alter publication columns or the
canonical digest. Added base inputs precede view replacement; replaced definitions stop
referencing removed/generated/type-changing inputs before those inputs are dropped. Existing
view OIDs remain stable; only a source removed from the manifest loses its view. Metadata is
a view and freshness is a timestamp-only function; exactly two pinned-search-path definers
exist, with no monitoring role.

The owner step also installs `pg_trgm` in a projection-only `mcp_ext` schema, as the superuser:
the schema, the extension and every member must be superuser-owned, so the projection owner
cannot plant or grant a function there (pg_trgm is a trusted extension it could otherwise
create). `mcp_public_ro` may execute only `mcp_ext.similarity(text, text)` (the `entity_lookup`
and `search` trigram match) and `mcp_ext.similarity_op(text, text)` (the `%` operator, whose
recheck the `search` trigram index scan runs as the reader). The step refuses any other state
before creating anything.

Besides `pg_trgm` (and `plpgsql` in `pg_catalog`), the only extension the step admits is
`pg_stat_statements` in `public` (bu-y66o00.60). CNPG creates it, when missing, in every
database, `butlers_public` included, whenever the cluster sets a `pg_stat_statements.*`
parameter. The step accepts it only if the
extension and each of its members belong to a superuser, no member is SECURITY DEFINER, and no
member is granted to a role outside PUBLIC, its owner and the projection roles. It then
revokes every member from everyone but its owner, as the superuser, and refuses if any grant
remains. Any other function, or any relation in `public` other than a table, index or
sequence, is still refused.

Search indexes (bu-y66o00.53). No index can match an expression over a view's `fields` jsonb,
so a search source too costly to scan declares search columns in
`butlers.knowledge_read.search_index`. Today only `chronicler.episodes_public` does:
`search_title` (`left(COALESCE(title, kind), 200)`) and `search_vector` (the English
`tsvector` of title, kind and category, joined with `||`, because `concat_ws` is not
immutable; it equals the `concat_ws` vector). Its `mcp_read` view appends both columns after
`fields`, and the owner step, as the superuser after `mcp_ext` is verified, creates a GIN index
on each expression over the subscriber table: `chronicler.episodes_public_mcp_search_title`
(`mcp_ext.gin_trgm_ops`) and `chronicler.episodes_public_mcp_search_vector`. They are expression
indexes, so the replicated columns do not change. PostgreSQL makes the table owner,
`mcp_projection_owner`, own them. Each is `CREATE INDEX IF NOT EXISTS` under a stable name; a
same-named index whose definition differs from a twin built on a temporary copy is dropped and
rebuilt, and a lost one is recreated on the next run. When title, kind or category leaves the
manifest, the view keeps both columns as typed NULLs (`CREATE OR REPLACE VIEW` cannot drop a
column, and the step never drops a view the server reads), the step drops both indexes, and the
reader ranks the episodes on `fields` again; widening back restores both. The step's catalog guard admits exactly
these names, on their declared table, as plain single-key GIN indexes; any other non-primary
index on a subscriber table still refuses the step before mutation. `search` reads the
episodes through those columns with a necessary-condition prefilter (vector match, or `%` at a
threshold it pins to 0.3), so the planner answers it from a BitmapOr of both indexes while
ranking and the exact match condition stay as before. Dropping `pg_trgm` now needs `CASCADE`,
which takes the trigram index with it; the next owner step rebuilds both.

The pure readers in `butlers.knowledge_read` serve the 19 tools of the pinned v1 catalogue
(`describe` lists them). Each takes an asyncpg connection to `butlers_public`, a host-built
`ReadContext` (caller `sub`, the request's `replication_as_of()`, the union of kill-switch layers,
the process cursor key) and the raw arguments, and returns JSON-ready data or raises `ReadError`
with a D17 code. They read only `mcp_read` views, write nothing (`propose_fact` aside, section
"Proposal intake") and keep no state. Connections, authentication, budgets, canaries, DLP and
audit belong to the host, `butlers.capability_host`; the transport is `butlers.mcp_public`.

**Known gap.** The deployed API and migrations share one database login, and the API does not
`SET ROLE`, so SQL ACLs cannot limit the API to controls-only writes while that identity also
owns the manifest state (section 6, "Privilege").

Steps tagged **Now** are runnable today. Never improvise a substitute for a step (for example, a
hand-written subscription): the design depends on exact step order and on the guard checks inside
the generated file.

**Environments.**

| Environment | Butlers namespace | CNPG cluster | Public namespace | State |
|---|---|---|---|---|
| dev (the live system) | `butlers-dev` | `postgres-dev` | `butlers-public-dev` (created by the public chart) | Live behind the gateway (see Status); homelab acts 2 and 3 applied (Tzeusy/homelab#40) |
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
| 4 | Publisher grants and `statement_timeout` in `init-db.sql` (owner act 9) | Butlers, run as superuser | `scripts/init-db.sql` | Now |
| 5 | Publication `butlers_public_v1` and `mcp_surface.manifest_state` (reconcile) | Butlers, migrations | `projection.toml` | Now on dev |
| 6 | Slot, subscription, subscriber DDL, applied digest (owner act 10, the owner step) | superuser: `make deploy-dev` on dev (OD20), the owner elsewhere | generated by `owner-sql` | Now |
| 7 | Public chart: namespace, ExternalSecret, bearer digest, keyring | Butlers | `deploy/helm/butlers-public-mcp` | Chart shipped dark; deployed at go-live |
| 8 | Alert rules and the two Uptime Kuma monitors (owner act 7) | Butlers rules, homelab monitors | rules: `deploy/helm/butlers-public-mcp` (`PrometheusRule`, `PodMonitor`); monitors: homelab | Rules shipped with the chart (dark until go-live); monitors and the fire drill (owner act 7) done on dev 2026-11-21 |
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
| `cluster.postgresql.pgHba` | the ordered allow and reject lines of D3 item 1 | the pod range (`10.42.0.0/16` on dev, as applied; the node's `podCIDR` is `10.42.0.0/24`) |
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

**Now**, including its projection grants: the publisher's `CONNECT` on `butlers` and its `USAGE` and `SELECT` sync, and
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

**Now on dev.** The migrations initContainer of `dashboard-api` (what RFC 0039 calls the
migration Job) runs Alembic, then `butlers public-mcp reconcile` as the migration user. An image
without that command (a rollback to a tag before it) skips it with a notice instead of failing the
rollout; the owner step's guard then refuses, which is fail-closed. The skip needs a command listing
that succeeds and lacks the command: a listing that crashes fails the rollout. The chart
value `publicMcp.reconcile` turns this on; it is true only in `values.dev.yaml`, because reconcile
refuses where `init-db.sql` has not created `mcp_surface` (no CNPG projection roles or
`butlers_public`). Reconcile makes publication `butlers_public_v1` equal the manifest and writes the
digest into `mcp_surface.manifest_state` in one transaction. It touches no slot, subscription or
`butlers_public` object (D4, OD14). Deploy as usual
([Kubernetes Deployment](kubernetes-deployment.md#deploy)).

Verify, in `butlers`:

```sql
SELECT schemaname, tablename FROM pg_publication_tables
 WHERE pubname = 'butlers_public_v1' ORDER BY 1, 2;  -- the manifest's tables
TABLE mcp_surface.manifest_state;                    -- one digest row
```

Compose and ordinary migration fixtures do not reconcile; run it explicitly there.

### 3c. Owner step (owner act 10)

**Now.** `butlers public-mcp owner-sql` prints the file. It is computed from the manifest alone, holds no
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
   a narrowing. It requires a running `dashboard-api` pod whose image tag is exactly `<tag>` (and
   fails if none is), then renders the file from the git tree of `<tag>` in the deployer's
   checkout (`git archive` into a temporary directory, `uv run --project <tree> butlers public-mcp
   owner-sql`), never from the pod, which is privileged and writable. It checks the tree's command
   listing for `owner-sql`, captures the whole file, refuses it if a psql meta-command outside the
   allowlist, a backtick or an unquoted `:name` variable appears
   (`scripts/k8s/public_mcp_sql_guard.py`), and only then pipes it
   to the superuser `psql` in the primary. Nothing is written to the checkout. The tag must be a
   commit in the checkout, so fetch before deploying a tag built elsewhere.
   When the migrations did not reconcile this manifest, the file's guard refuses and changes
   nothing (D3 step 1).
4. After step 3 exits 0 (and with both `PUBLIC_MCP_OWNER_STEP_dev` and `PUBLIC_MCP_CHART_dev` on),
   the public chart `deploy/helm/butlers-public-mcp` at the same tag (3d), so the public pod
   ships the manifest just applied. `PUBLIC_MCP_CHART_dev` is still `off` on `main`, so today the
   recipe ends at step 3 and the public chart is upgraded by hand (3d step 5). Until that switch
   is on, a deploy that changes the manifest leaves the public pod on its old image, which refuses
   with `projection_version_mismatch` until the hand upgrade.

The target exits with the first non-zero status of these steps. The owner step runs on every dev
deploy; with an unchanged manifest and a healthy slot it changes nothing (D3 "Rerun"). Read its
outcome in the deploy output:

- **Success.** The step exits 0 and the checks below pass (with `PUBLIC_MCP_CHART_dev` on, the public
  chart follows).
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

The chart `deploy/helm/butlers-public-mcp` (task 2.4, `bu-y66o00.6`) creates namespace
`butlers-public-<env>`, three ExternalSecrets (the `mcp_public_ro` password, mounted as a file, from Bitwarden item
`MCP_PUBLIC_RO_DB_PASSWORD`; the hop-bearer digest from `MCP_PUBLIC_HOP_BEARER_DIGEST`; the canaries from `MCP_PUBLIC_CANARIES`), the
verifier keyring and break-glass switch ConfigMaps, one Deployment (one replica) with its
ClusterIP Service, and default-deny NetworkPolicies with the four allows (D8). Its README has the
runtime contract and values.

**Install path.**

1. *Image.* The same `butlers-app` image and TAG as the `butlers` chart (`make ship-dev` builds it);
   the command is `butlers-public-mcp`. The TAG must contain that entrypoint.
2. *Bitwarden (dev store).* Item `MCP_PUBLIC_RO_DB_PASSWORD` already holds the CNPG password
   (owner act 2). Create `MCP_PUBLIC_HOP_BEARER_DIGEST` = lowercase hex SHA-256 of the
   filter-proxy `UPSTREAM_BEARER`, no prefix (owner act 5), never the bearer itself; and
   `MCP_PUBLIC_CANARIES` = the JSON list of canary strings (each at least 8 characters, D15), which
   the server requires. The list must be non-empty (at least one string of 8 or more characters):
   a missing, invalid or empty file makes every tool refuse, and the chart cannot read the item, so
   the live checks verify it. To overlap a bearer rotation set
   `externalSecrets.bearerDigestNextKey` to a second item holding the next digest; the server
   accepts both while the overlap lasts.
3. *Site values.* The server requires `MCP_PUBLIC_AUDIENCE` (https URL of the public endpoint) and
   `MCP_PUBLIC_ALLOWED_SUBS` (comma-separated owner subjects). Neither is committed: export
   `BUTLERS_PUBLIC_MCP_AUDIENCE` and `BUTLERS_PUBLIC_MCP_ALLOWED_SUBS` (optionally
   `BUTLERS_PUBLIC_MCP_CLIENT_DOMAINS`, `BUTLERS_PUBLIC_MCP_REGISTERED_CLIENTS`) for the deploy,
   or keep them in the site env file (`/secrets/.dev.env`), which `scripts/site-env.sh` exports;
   the chart refuses to render without the first two.
4. *Keyring.* After owner act 4, write the verifier keyring document (version, current,
   retiring; public keys only) to a file and name it in `BUTLERS_PUBLIC_MCP_KEYRING_FILE` (env or
   site env file); the deploy script renders it with `--set-file keyringJson=`. Without it every
   upgrade resets the keyring to empty, and the server verifies nothing and refuses every call.
5. *Deploy.* On dev, `make deploy-dev` upgrades the chart after a successful owner step, at the
   same tag, through `scripts/k8s/public-mcp-chart-deploy.sh`. **The hook is guarded** by
   `PUBLIC_MCP_CHART_dev` in `deploy/helm/butlers/makefile`, which is still `off`: every public
   chart revision on dev so far was a hand upgrade. Switching it on is a reviewed change
   (`bu-y66o00.67`). It never runs when the owner step was switched off or failed, and its status
   joins the recipe's (first non-zero wins). Prod stays off (OD18). By hand, from a checkout whose
   site env carries the values of step 3 and the keyring of step 4:
   `scripts/k8s/public-mcp-chart-deploy.sh dev <tag> on`. It uses the chart files of that
   checkout and the image at `<tag>`. The release record lives in
   `butlers-dev` (helm cannot store a release in a namespace the chart creates); the namespace
   carries `helm.sh/resource-policy: keep`.
6. *Filter-proxy.* Owner act 5 points `UPSTREAM_URL` at
   `http://butlers-public-mcp.butlers-public-<env>.svc.cluster.local:8080/mcp`.

*Break glass.* ConfigMap `butlers-public-mcp-switches`, key `disabled_domains`: one domain or `all`
per line, `#` comments. An empty or comment-only file disables nothing. A missing file, an unknown
entry, invalid UTF-8 or an unreadable file disables everything (the chart always mounts it). The server re-reads it every 10 s.

The process the chart runs (`butlers.mcp_public`, task 2.3) is the console script
`butlers-public-mcp`, installed with the `butlers` package. The image puts no venv on PATH, so the
chart runs the script's target, `python -m butlers.mcp_public.server`, through the image's sealed
`scripts/runtime_clis/python_entrypoint.py` (as every other role does):

| Contract | Value |
| --- | --- |
| MCP | `POST :8080/mcp`, stateless JSON responses; `GET`/`DELETE` are 405 after the bearer, other paths 404 |
| `server/discover` | -32601 until homelab cross-checks the gateway's catalogue probe |
| Metrics and probes | `GET :9090/metrics` (Prometheus text), `GET :9090/healthz` (no database) |
| Required env | `MCP_PUBLIC_AUDIENCE` (https URL), `MCP_PUBLIC_ALLOWED_SUBS`, `MCP_PUBLIC_DB_HOST` |
| Optional env | `MCP_PUBLIC_DB_PORT` (5432), `MCP_PUBLIC_DB_NAME` (`butlers_public`), `MCP_PUBLIC_DB_USER` (`mcp_public_ro`), `MCP_PUBLIC_CLIENT_DOMAINS` (`claude.ai,chatgpt.com`), `MCP_PUBLIC_REGISTERED_CLIENTS`, `MCP_PUBLIC_DASHBOARD_BASE` |
| Files under `/etc/butlers-public-mcp/` | `db/password` (read once at start), `bearer/digest` and optional `bearer/digest_next`, `keyring/keyring.json`, `switches/disabled_domains` (must exist; missing disables every domain), `canaries/canaries.json` (non-empty; re-read every 10 s) |

A missing or malformed required setting exits 2 before any port opens. The reader pool pins
`search_path` to `pg_catalog, pg_temp`. Access logs are off; the audit line on stdout is the request log.

Verify: `scripts/k8s/public-mcp-live-checks.sh dev` runs the live NetworkPolicy check (D8 item 6),
the `postgres-<env>` policy check (REQ-public-mcp-projection-014), the reachability checks
(labelled and unlabelled `mcp` pods, the pod's 5432 path, no internet) and the public half of the
task 1.8 isolation test (`mcp_public_ro` reaches `butlers_public` only). Then the task 2.5 smoke
from a test pod in `mcp`: `describe` answers, and every other tool either serves or refuses with
one of the codes in [section 4](#4-fail-closed-states).

*Dark smoke (before owner act 4).* `bws run -- scripts/k8s/public-mcp-smoke.sh dev`, with the hop
bearer in `MCP_PUBLIC_HOP_BEARER`. It refuses unless the keyring is empty, installs a throwaway
test key as the keyring (RFC 0039 P2 "with a test keyring"), restarts the server, runs the live
checks, then from a labelled probe pod in `mcp`: the bearer and assertion refusals, `initialize`
and `tools/list`, every tool but `propose_fact` (it writes an inbox row), the first call's
canary-probe time and the `entity_lookup`/`search` p95. The no-reasoning witness lists the rows
created in `butlers` during the window (sessions, route inbox, schedules, routing log, goals,
message inbox, ingestion events) by source and fails on any mcp/public source. The empty keyring
is restored on exit. Past act 4 the smoke goes through the gateway Worker (`bu-y66o00.7`).

*Probe pods and namespace `mcp`.* Homelab makes `mcp` default-deny in both directions
(`helm/traefik_mcp_local`, DNS only), so a probe pod there cannot leave the namespace on its own.
The live checks and the smoke apply `scripts/k8s/public-mcp-probe-egress.yaml` for their run and
delete it on exit. It allows egress only to the public pod's 8080, and only for pods with the
marker label `butlers.tze.how/public-mcp-probe=true`, which no gateway pod carries. Both REACH
probes carry the marker, so the unlabelled probe's refusal comes from this chart's ingress
policy. The labelled probe also carries `app.kubernetes.io/instance=mcp-butlers`, the selector
label homelab `helm/mcp_server` gives release `butlers`; the chart's ingress allow admits exactly
that label. On kube-router a policy rejection reads as "connection refused", the same as a closed
port, so a refusal alone never says which side denied the connection.

### 3e. Alerts and monitors

**Rules shipped** (task 3.2, `bu-y66o00.7`). The public chart renders a `PrometheusRule` with every
alert condition and the two flag recording rules `butlers_public_mcp:critical_ok` and
`butlers_public_mcp:warning_ok` (D16), and a `PodMonitor` for the metrics port 9090. Both are
values (`monitoring.podMonitor`, `monitoring.rules`), on in `values.dev.yaml` with the label
`release: lgtm` the cluster Prometheus selects, and ship dark with the chart. Nothing is added to
ingress. The alert table, the thresholds (all values) and the sources are in the
[chart README](../../deploy/helm/butlers-public-mcp/README.md#alerts-and-flags-rfc-0039-d16-task-32).

The flags are 1 while no alert of their severity fires and 0 while any does; each is one series
that is never empty while Prometheus evaluates, and only this surface's alerts (label
`surface="butlers-public-mcp"`) move them. Narrowing left pending 24 hours turns the critical
flag; widening pending, any age, only the warning flag (`butlers_public_mcp_owner_step_pending`).

*Sources outside the chart.* Slot alerts read CNPG's `cnpg_pg_replication_slots_*` series from
the `postgres-<env>` exporter. Importer alerts read the OTel series
`butlers_public_mcp_proposal_import_inbox_depth` and
`butlers_public_mcp_proposal_import_outcomes_total{state="apply_failed"}`.

*Slot series on dev.* [Observed: dev, 2026-11-21] The homelab PodMonitor `lgtm/lgtm-cnpg` already
scrapes `postgres-dev`. The gap was database access, not the scrape: `core_001` revokes `CONNECT`
on `butlers` from `PUBLIC`, so the exporter role `cnpg_metrics_exporter` was denied every query
there, the slot query included. `scripts/init-db.sql` now grants that role `CONNECT` (only) when it
exists (bu-y66o00.63), so every `bootstrap-dev` rerun keeps it; the homelab's hand grant of
2026-11-21 is no longer the only copy. Verified with
`cnpg_pg_replication_slots_active{namespace="postgres-dev"}`, which returned `butlers_public_sub`
at 1.

*Importer series.* `butlers_public_mcp_proposal_import_outcomes_total` has no series until the
first import outcome (an empty inbox records none). That does not move a flag: the apply-failed
rule only fires on a transition, and `PublicMcpImporterMetricsMissing` reads
`butlers_public_mcp_proposal_import_switch_enabled`. The importer's OTLP path
(`OTEL_EXPORTER_OTLP_ENDPOINT`) must reach the `lgtm` Prometheus under the names above; while it
does not, `PublicMcpImporterMetricsMissing` keeps the warning flag at 0 by design (as
`PublicMcpSlotMetricsMissing` does for a lost slot scrape), and `PublicMcpServerMetricsMissing`
(critical) covers a stopped scrape of the server itself.

*Not covered, no metric source today* (see the README for why): replication lag over 10 minutes,
a logical slot on an allowed name with a plugin other than `pgoutput`, and one `(sub, cid)` reading
over 25 % of a domain's rows in a day. Each needs a source first (a server gauge from a background
poll, a CNPG custom query adding `plugin` or subscription lag, a projection row-count gauge), so
REQ-public-mcp-authorization-014 and task 3.2 stay open on them.

*Tests.* `tests/scripts/test_public_mcp_alert_rules.py` runs `promtool test rules` over the
rendered rules, one case per covered condition, the healthy case, and the 24-hour narrowing and
48-hour widening scenarios. promtool is pinned with its SHA-256 in `scripts/lint-tool-versions.json`
and installed by `make install-lint-tools`; the test skips without it locally, and CI's guards job
sets `BUTLERS_REQUIRE_PROMTOOL=1` so it cannot skip there.

*Owner act 7 (done on dev, 2026-11-21).* Homelab declares one Uptime Kuma `json-query` monitor per
flag in `ansible/roles/proxmox_sentinel/files/uptimekuma-monitors.toml` (query
`<flag> == bool 1`, `jsonPath = "data.result[0].value[1]"`, `expectedValue = "1"`): critical
(Kuma id 43, `interval = 60`, 1 retry) and warning (id 44, `interval = 300`, 2 retries). Fire
drill, about 17:29-17:50Z: both monitors went DOWN and recovered. Telegram delivery awaits the
owner's confirmation.

Then the gateway acts 4-6 and client consent (act 8). On dev the first connection exposes live
data, which the owner accepted (OD12).

## 4. Fail-closed states

What a refusal means on a fresh or changing environment, and which step fixes it. The codes and
their precedence are RFC 0039 D17; the mapping to setup steps is [Inferred] from its failure
table.

| Refusal | Likely missing or broken step | Fix |
|---|---|---|
| `projection_apply_pending` | The owner step has not run since the first sync or the last manifest change, stopped midway, or the initial copy is not complete | Run 3c; wait for every relation to reach state `r` |
| `projection_version_mismatch` | Deploy skew: the public pod ships a different manifest from the replicated digest | On dev, the public chart is not at the deployed tag: while `PUBLIC_MCP_CHART_dev` is `off`, run `scripts/k8s/public-mcp-chart-deploy.sh dev <deployed tag> on` (3d step 5); with it on, the last deploy's owner step failed, so its chart step did not run: fix 3c and deploy again. Elsewhere, finish the `butlers` rollout (3b) or deploy the public chart at the same revision |
| `projection_stale` | Replication older than 15 minutes, or `replication_as_of()` NULL (no subscription, or it never confirmed a position) | Check the slot; if `wal_status = 'lost'`, rerun 3c, which rebuilds the slot and subscription |
| `unavailable` | The canaries file is missing, invalid or an empty list (ExternalSecret `butlers-public-mcp-canaries`, Bitwarden `MCP_PUBLIC_CANARIES`; `describe` still answers), or no connection to `butlers_public`: `postgres-<env>` down, the connection limit reached, a `pg_hba` refusal (for example, a pod on a second node), or the password Secret missing | Section 1 checks; the ExternalSecret status in `butlers-public-<env>` (`butlers-public-mcp-db`, mounted at `/etc/butlers-public-mcp/db/password`) |
| `domain_disabled` | A kill switch is off | Intended, or see section 6 |
| `blocked` | The canary latch holds | Section 6, leak runbook |
| JSON-RPC `-32001` | Bearer or assertion refused | Gateway acts 4-6; bearer digest (ExternalSecret `butlers-public-mcp-bearer`, Bitwarden `MCP_PUBLIC_HOP_BEARER_DIGEST`) and keyring (ConfigMap `butlers-public-mcp-keyring`) in 3d |
| Pod not Ready, or the `kubectl` rollout never finishes | The TAG has no `butlers-public-mcp` entrypoint (chart switched on before the server PRs merged), or the bearer-digest Secret is missing, so the server fails closed at startup | Check `PUBLIC_MCP_CHART_dev` and the TAG; `kubectl -n butlers-public-<env> get externalsecret` |
| Every request refused after a hop-bearer change | The digest Secret lags the Bitwarden item (refresh interval 1h) | Force an ExternalSecret refresh; the server accepts the current and next digest during a rotation (D6) |

## 5. Upkeep cadence

**The step is now; the alert rules ship with the public chart (3e).** The owner step must run (RFC 0039 D3 "Cadence"):

- once at the first sync;
- once after each manifest change that 3b has applied;
- once after each slot invalidation.

**On dev this needs no owner act** (OD20). A manifest change reaches dev only through a deploy,
and every `make deploy-dev` runs the step after its migrations, whatever the release returned,
and, once `PUBLIC_MCP_CHART_dev` is `on`, the public chart at the same tag (3c), so the first sync
and every manifest change are covered. While that switch is `off`, a manifest change also needs
the hand chart upgrade (3d step 5). Only a slot invalidation between deploys calls for a manual run of
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

Task 7.2. Each procedure gives its trigger, the steps, how to verify them and who runs them. The
switches and the leak runbook are RFC 0039 D16; the failure codes are D17 (section 4).

**Who.** The owner runs every step that touches Bitwarden, the homelab repository, the site env
file, the cluster with `kubectl`, or the superuser path (`kubectl exec` into the `postgres-dev`
primary, the owner step). Agents prepare the reviewed changes (manifest, values, makefile), read
the results the owner pastes back, and may call `describe` through a connected client. Agents
never run a superuser step (RFC 0039 D9).

**Handles used below** (dev; replace `dev` for another environment):

```bash
# The CNPG primary, selected by label (section 1). Superuser psql: one statement per -c.
primary="$(kubectl -n postgres-dev get pod \
  -l cnpg.io/cluster=postgres-dev,cnpg.io/instanceRole=primary -o name)"
kubectl -n postgres-dev exec -i "$primary" -c postgres -- psql -X -d butlers -c "<query>"
# The deployed tag: the part after the last ':' of the dashboard-api image.
kubectl -n butlers-dev get deploy dashboard-api \
  -o jsonpath='{.spec.template.spec.containers[?(@.name=="dashboard-api")].image}'
```

`psql -c` with several statements runs them in one transaction, which `DROP SUBSCRIPTION` refuses,
so every superuser statement below is its own `-c`.

**Reading `describe`.** It answers whenever the projection is reachable, even while the latch or
a switch stops every other tool. `domains` maps each of the seven data domains to `disabled` (any
switch layer), `stale` (replication older than the D17 cutoff) or `enabled`; `all` shows as every
domain `disabled`. `manifest_digest` is the replicated digest of `mcp_surface.manifest_state`.
Call it from a connected client.

### Kill-switch layers (task 5.6)

The owner flips the normal switch from the dashboard or the CLI; the rest are break glass. Nothing
here is instant except scaling to zero and the gateway: **never promise immediate effect from the
first two rows.**

| Layer | Flip | Reaches | Latency | Who |
| --- | --- | --- | --- | --- |
| `mcp_surface.controls` row per domain, plus `all` (primary) | Dashboard `/settings/public-mcp` (owner passkey session), or `butlers public-mcp disable <domain\|all>` | The public server by replication; the Relationship proposal importer reads the primary | Public server: within 15 minutes (the D17 stale cutoff). Importer: its next pass | owner |
| `disabled_domains` key of ConfigMap `butlers-public-mcp-switches` in `butlers-public-<env>` | `kubectl patch` | The public server only | About a minute: the kubelet's volume refresh, then the 10 s re-read | owner |
| Canary latch | The detector | Every tool except `describe` | Until the pod restarts | automatic |
| Scale the public Deployment to 0 | `kubectl scale` | The whole surface | Immediate | owner |
| Gateway: Worker maintenance, or the server removed from `MCP_SERVERS` | Homelab repository | Every client | Immediate | owner (homelab) |

Domains are `all`, `knowledge`, `relationship`, `time`, `travel`, `finance`, `health` and
`proposals`; `all` turns off every data tool and leaves `describe`. Disabling and enabling are one
step each, with no unlock.

**Layer 1, the controls row (normal path).**

- *Flip.* The dashboard Settings page, or the CLI as the migration principal. On dev the
  `dashboard-api` container carries that login, so:

  ```bash
  kubectl -n butlers-dev exec deploy/dashboard-api -c dashboard-api -- \
    /usr/local/bin/python -I /app/scripts/runtime_clis/python_entrypoint.py \
    butlers public-mcp disable all        # or: enable <domain|all>, disable <domain>
  ```

  [Observed: `disable all`, `enable all` and `reconcile --database butlers` ran this way in the
  2026-11-22 dev rollback rehearsal.]
- *Verify on the primary* (superuser, `-d butlers`):
  `SELECT domain, state, changed_at, changed_by FROM mcp_surface.controls ORDER BY domain;`.
  Then `describe` shows the domain `disabled`, within 15 minutes. For `proposals` or `all`, the
  importer's gauge `butlers_public_mcp_proposal_import_switch_enabled` reads 0 after its next pass.
- *Undo.* `enable` the same row. Enabling `all` does not enable a domain that has its own
  `disabled` row.

**Layer 2, break glass.**

- *Flip.* One domain or `all` per line:

  ```bash
  kubectl -n butlers-public-dev patch configmap butlers-public-mcp-switches --type merge \
    -p '{"data":{"disabled_domains":"all\n"}}'          # or "finance\nhealth\n"
  ```

  An unknown name disables every domain (section 3d, "Break glass").
- *Verify.* `kubectl -n butlers-public-dev get configmap butlers-public-mcp-switches -o
  jsonpath='{.data.disabled_domains}'` prints the lines; `describe` shows the domains `disabled`
  within about a minute.
- *Undo.* Patch the key back to a comment, `"# none\n"`.
- *Caveat.* Every public chart upgrade rewrites this ConfigMap from the chart's `disabledDomains`
  value, which disables nothing, so a break-glass entry does not survive an upgrade. In an
  incident set layer 1 as well, and read `describe` again after each chart upgrade. Layer 2 does
  not stop the importer.

**Layer 3, the canary latch.** Not flipped by hand; see "Canary latch" below.

**Layer 4, scale to zero.**

- *Flip.* `kubectl -n butlers-public-dev scale deployment/butlers-public-mcp --replicas=0`.
- *Verify.* `kubectl -n butlers-public-dev get deploy,pods` shows `0/0` and no pod. Clients fail
  at the gateway. After 5 minutes `PublicMcpServerMetricsMissing` drops the critical flag, so the
  critical Kuma monitor goes DOWN and the owner's Telegram gets a message: expected.
- *Undo.* `--replicas=1`; any chart upgrade also restores one replica. The new process starts with
  the latch clear, an empty replay set and the owner-step gauge unset until the first call.

**Layer 5, the gateway.** Homelab acts, outside this repository (RFC 0039 D16).

**Dashboard.** `GET /api/settings/public-mcp` lists the eight rows with state, change time and
actor; `GET /api/settings/public-mcp/summary` feeds the Settings console panel (how many of the
seven domains are off, and whether `all` is); `PUT /api/settings/public-mcp/{domain}` with
`{"state": "enabled"|"disabled"}` writes one row. The write needs the owner's verified dashboard
session and answers 401 without one, before any database work; an unknown domain is 404. The actor
recorded is `owner:dashboard` and cannot be supplied by the caller.

**CLI.** `butlers public-mcp disable <domain|all>` and `enable <domain|all>` (optional
`--database`, default `butlers`) write the same row with actor `owner:cli`. They connect as the
migration principal from the `POSTGRES_*` / `DATABASE_URL` environment, the same login as
`public-mcp reconcile` and as the dashboard API. A repeated write changes nothing and keeps the
original time and actor. Output restates the replication delay and the break-glass path.

**Privilege.** `mcp_surface.controls` is owned by the migration principal, which is the dashboard
and CLI login, so the write needs no grant and no definer function. No butler runtime role can
write it, and only `butler_relationship_rw` can read it (importer). `init-db.sql` adds the private
`changed_by` column (`NOT NULL DEFAULT 'system'`) to an existing table and rechecks the shape;
`changed_by` is not in the projection manifest and never replicates. The write is an upsert, so
a deleted seed row can still be switched off during an incident. `init-db.sql` (3a) must run before
the new API image or CLI is used, because the `changed_by` column comes from it (every
`make deploy-dev` does this first). The dashboard login being the migration principal is a
deployment assumption, not something the API checks; a different login gets a closed 503. The
console summary reports `missing_rows` so an unseeded table never reads as all-enabled.

### Rotate the hop bearer (D6)

*Trigger.* A scheduled rotation, or a suspected exposure of the filter-proxy `UPSTREAM_BEARER`.
A digest that does not match refuses every request with HTTP 401 before the body is read, and
`PublicMcpBearerRefusals` lowers the warning flag.

The server accepts two digests, `bearer/digest` and the optional `bearer/digest_next`, re-read
every 10 seconds. The chart fills them from the Bitwarden dev items named by
`externalSecrets.bearerDigestKey` (`MCP_PUBLIC_HOP_BEARER_DIGEST`) and
`externalSecrets.bearerDigestNextKey` (empty by default), through ExternalSecret
`butlers-public-mcp-bearer` (refresh interval 1h). `scripts/k8s/public-mcp-chart-deploy.sh`
passes only `values.yaml` and `values.dev.yaml`, so the next-digest item name goes into
`deploy/helm/butlers-public-mcp/values.dev.yaml` in a reviewed change. An item name is not a
secret.

1. *Owner, homelab custody.* Generate the new bearer. Compute its digest, lowercase hex SHA-256 of
   the bearer with no trailing newline and no prefix (`printf '%s' "<bearer>" | sha256sum`), and
   store it in a new Bitwarden dev item, for example `MCP_PUBLIC_HOP_BEARER_DIGEST_NEXT`. Never put
   the bearer itself in a Butlers item.
2. *Agent or owner, reviewed PR.* Set `externalSecrets.bearerDigestNextKey` to that item name in
   `values.dev.yaml`.
3. *Owner.* From a checkout of `main` with that change:
   `scripts/k8s/public-mcp-chart-deploy.sh dev <deployed tag> on`. The bearer volume gains the
   `digest_next` item, so the pod is recreated.
4. *Verify.* `kubectl -n butlers-public-dev get externalsecret butlers-public-mcp-bearer` is
   `Ready`; key names only:
   `kubectl -n butlers-public-dev get secret butlers-public-mcp-bearer -o json | jq '.data | keys'`
   lists `digest` and `digest_next`. A client call still succeeds on the old bearer.
5. *Owner, homelab.* Switch the filter-proxy `UPSTREAM_BEARER` to the new bearer. Verify that a
   client call succeeds and `butlers_public_mcp_bearer_refusals_total` does not rise.
6. *Owner, promote.* Write the new digest into `MCP_PUBLIC_HOP_BEARER_DIGEST` and refresh at once
   instead of waiting up to an hour:

   ```bash
   kubectl -n butlers-public-dev annotate externalsecret butlers-public-mcp-bearer \
     force-sync="$(date +%s)" --overwrite
   ```

   Then a reviewed PR sets `bearerDigestNextKey` back to `""`, the owner runs the chart deploy of
   step 3 again and deletes the next-digest item. Verify: the key list is `digest` only and client
   calls succeed.

### Rotate the verifier keyring (D7)

*Trigger.* A scheduled rotation of the Worker's Ed25519 key, or its exposure. A keyring that does
not match refuses every call (JSON-RPC `-32001`); the audit line's `cause` reads `keyring`, `kid`
or `signature`, and `PublicMcpAssertionFailures` lowers the warning flag.

The keyring is a public-key document in ConfigMap `butlers-public-mcp-keyring`, rendered from the
file that `BUTLERS_PUBLIC_MCP_KEYRING_FILE` names in the site env file (3d step 4). Its shape,
checked by `butlers.core.runtime_probe_control.keys`:

```json
{"version": 1,
 "current":  {"alg": "EdDSA", "kid": "<new kid>", "public_key_b64u": "<new>", "sign_from": "<T>"},
 "retiring": [{"alg": "EdDSA", "kid": "<old kid>", "public_key_b64u": "<old>",
               "sign_from": "<old sign_from>", "sign_until": "<T>", "accept_until": "<T + 70 s to 5 min>"}]}
```

Exactly one `current` and at most one `retiring` entry; distinct kids and keys; the current
`sign_from` equals the retiring `sign_until` (the cutover instant T, a UTC second); `accept_until`
is 70 seconds to 5 minutes after it. An assertion verifies only under the key whose window holds
its `iat`: the old key up to T inclusive, the new key from T.

1. *Owner, homelab.* Generate the new key pair; the private key stays a Worker secret.
2. *Owner.* Pick T after steps 3 and 4 can finish. Write the document above over the keyring file
   (public keys only).
3. *Owner.* `scripts/k8s/public-mcp-chart-deploy.sh dev <deployed tag> on`. Only the ConfigMap
   changes, so the pod keeps running and re-reads it within about a minute. Verify the kids (the
   document is public):
   `kubectl -n butlers-public-dev get configmap butlers-public-mcp-keyring -o jsonpath='{.data.keyring\.json}' | jq '{current: .current.kid, retiring: [.retiring[].kid]}'`.
   Calls still succeed on the old key.
4. *Owner, homelab.* Redeploy the Worker to sign with the new key from T. [Unknown] whether the
   Worker can hold both keys and switch at T by itself; if it switches when deployed, calls signed
   between that moment and T (new key too early) or after T (old key too late) are refused, so
   deploy it as close to T as possible. Clients retry.
5. *Verify after T.* Client calls succeed and `butlers_public_mcp_auth_failures_total` stays flat.
6. *Owner, after `accept_until`.* Set `"retiring": []`, run the chart deploy of step 3 again, and
   verify the kid list shows no retiring entry. The file stays the record of the current key.

### Rebuild after slot invalidation

*Trigger.* Every data tool answers `projection_stale`, `describe` shows each domain `stale`, and
the slot is lost. `PublicMcpSlotRetainedWal` (critical) usually fires first, and
`PublicMcpSlotInactive` (warning) once the subscription stops.

1. *Confirm* (superuser, `-d butlers`):
   `SELECT slot_name, active, wal_status FROM pg_replication_slots WHERE slot_name = 'butlers_public_sub';`.
   Only `wal_status = 'lost'` needs a rebuild; `unreserved` can still recover once the subscriber
   catches up. Look for the cause too, in `-d butlers_public`:
   `SELECT subname, pid, last_msg_receipt_time FROM pg_stat_subscription;`.
2. *Owner.* Fix the cause (a disabled or failing subscription, disk pressure), then, from a
   checkout that has the deployed tag (`git fetch` first):
   `scripts/k8s/public-mcp-owner-step.sh dev <deployed tag>`. It deletes the applied record (the
   server answers `projection_apply_pending` from then on), drops the subscription and its slots,
   truncates the mirrored tables, recreates the slot and subscription and re-copies (RFC 0039 D3
   step 4).
3. *Verify.* The 3c queries: the slot `reserved` or `extended`, the subscription enabled, every
   relation in state `r`, the applied digest equal to the manifest digest. `describe` no longer
   shows `stale`, and both flags return to 1.

### Drain or flush the proposal inbox (section 9)

*Trigger.* `PublicMcpInboxUndrained` (depth over 500), or `propose_fact` answering `unavailable`
with `retryable: true` (the cap of 1,000 rows).

- *Diagnose (read-only; agent or owner).* The importer runs every 5 minutes in `butlers-up`. A pass
  that logs `no mcp_proposals_reader password file is mounted; intake skipped` means Secret
  `postgres-mcp-proposals-reader-user` is missing in `butlers-dev`; errors count in
  `butlers_public_mcp_proposal_import_errors_total{step}`. Depth: the gauge
  `butlers_public_mcp_proposal_import_inbox_depth`, or as superuser in `-d butlers_public`
  `SELECT count(*) FROM mcp_inbox.proposals;`.
- *Drain (owner fixes the cause).* Each pass takes up to 100 rows, so a full inbox empties in about
  ten passes. Intake runs while `proposals` is off, so disabling it does not stop the drain; it
  only stops parking. To stop new proposals while the inbox drains, disable `proposals` (layer 1,
  and layer 2 for immediate effect). Verify: the depth falls, and in `-d butlers`
  `SELECT state, count(*) FROM relationship.external_fact_proposals GROUP BY 1;` grows by the
  copied rows.
- *Flush (owner, superuser).* To discard rows never imported, for example a flood from one
  client: disable `proposals` (layers 1 and 2), then in `-d butlers_public`
  `DELETE FROM mcp_inbox.proposals;`. [Inferred: no script does this; a deleted row never reaches
  the primary.] Rows already imported stay `imported`, and park when `proposals` is enabled again
  (the rate limits still apply); reject unwanted ones in the approvals queue.

### Canary latch

*Trips when* the projection probe, which runs on the first data call after each new applied
manifest or canary set, finds a canary string in any `mcp_read` view, or a canary string appears
in a response about to be released (D15). Each hit counts `butlers_public_mcp_canary_hits_total`
and sets `butlers_public_mcp_canary_latched` to 1: `PublicMcpCanaryHit` and
`PublicMcpCanaryLatched` (both critical) drop the critical flag, and the owner's Telegram gets a
message.

*Blocks* every tool except `describe`, for every client, with `blocked`, until the process
restarts. `blocked` while the latch gauge is 0 is a DLP drop (`PublicMcpDlpDrop`), not the latch.
The audit lines (`kubectl -n butlers-public-dev logs deploy/butlers-public-mcp`) carry the tool and
outcome of each call, never content.

*Clear (owner).* Run the leak runbook below first. A restart forgets the probe result, so with the
canary still in the projection the next data call latches again. Then:
`kubectl -n butlers-public-dev rollout restart deployment/butlers-public-mcp` (a chart upgrade that
changes the pod also restarts it). Verify: a data call answers normally or `domain_disabled`, not
`blocked`; the latch gauge is not 1; the critical flag returns to 1.

### Leak runbook (D16)

*Trigger.* A canary hit (the latch), or a column, table or predicate published that must not be.
The surface refuses from the moment the publication narrows until the owner step completes (D17).

1. *Stop serving (owner, at once).* Disable the domain, or `all`, in layer 2 for immediate effect
   and in layer 1 so it survives chart upgrades; or scale to zero (layer 4). A tripped latch
   already blocks.
2. *Narrow the manifest (agent or owner, reviewed PR).* Remove the column, table or predicate from
   `src/butlers/knowledge_read/projection.toml`, with the tests and docs it touches, and merge.
3. *Deploy (owner).* `scripts/k8s/deploy-dev.sh` from the updated `main` (it builds and ships).
   The migrations' reconcile narrows the publication (3b); the owner step in the same deploy sees
   a changed entry and rebuilds: it deletes the applied record, drops the subscription, truncates
   every mirrored table and re-copies under the narrowed publication. That is the purge. It runs
   even when the release fails after its migrations. If the deploy prints `public-mcp owner step
   FAILED`, the purge did not happen: fix the cause and rerun
   `scripts/k8s/public-mcp-owner-step.sh dev <deployed tag>` before going on.
4. *Ship the public chart at the same tag (owner).* While `PUBLIC_MCP_CHART_dev` is `off`:
   `scripts/k8s/public-mcp-chart-deploy.sh dev <deployed tag> on`. The new pod ships the narrowed
   manifest and starts with the latch clear. The upgrade resets layer 2; layer 1 keeps the domain
   off, which `describe` confirms.
5. *Confirm the purge (read-only).* The 3c queries: the applied digest equals the manifest
   digest and every relation is in state `r`. The removed column is gone from its subscriber
   table, as superuser in `-d butlers_public`:
   `SELECT column_name FROM information_schema.columns WHERE table_schema = '<schema>' AND table_name = '<table>';`,
   and from `describe`'s `columns`.
6. *Confirm the canary probe is clean.* Make one data call (any tool but `describe`). The probe
   runs before the domain check, so with the domain still off a clean projection answers
   `domain_disabled` and a dirty one `blocked`, latching again [Inferred:
   `butlers.capability_host.host`, `_run`]. The latch gauge stays 0 and the critical flag at 1.
7. *Re-enable (owner).* `enable` the layer 1 row and clear any layer 2 entry. Verify: `describe`
   shows the domain `enabled`.

## 7. Teardown and rebuild

Task 8.1, on dev only (OD18). **Not yet rehearsed:** the first rehearsal follows this section
verbatim and fixes every gap it finds here. Every step is the owner's (`kubectl` on the cluster;
step 4 is the superuser path); an agent prepares the two makefile PRs and reads the results.

**What to expect while it runs.**

- *Clients.* claude.ai and ChatGPT lose the server from step 2 until the rebuild's public chart is
  up. [Unknown] whether a client needs reconnecting afterwards.
- *Alerts.* After step 2, `PublicMcpServerMetricsMissing` fires within 5 minutes: the critical
  flag drops to 0, the critical Kuma monitor goes DOWN and the owner's Telegram gets a message.
  Step 3 removes the chart's rules, so both flag series disappear; Kuma counts an empty result as
  DOWN, so the warning monitor follows after its retries, with its own message. Both stay DOWN
  until the rebuild's chart is installed and both flags read 1.
- *Primary.* Untouched. No butler, dashboard or applied fact changes. The proposal importer keeps
  running in `butlers-up`; with `all` disabled it parks nothing.

**Off switch, answered from the makefile.** `PUBLIC_MCP_OWNER_STEP_dev := off` alone also stops
the public chart step. The `deploy-%` recipe runs `public-mcp-chart-deploy.sh` only when the owner
step exited 0 **and** `PUBLIC_MCP_OWNER_STEP_$*` is `on`:

```make
if [ $$os -eq 0 ] && [ "$(PUBLIC_MCP_OWNER_STEP_$*)" = on ]; then \
	../../../scripts/k8s/public-mcp-chart-deploy.sh "$*" "$(TAG)" "$(PUBLIC_MCP_CHART_$*)"; pc=$$?; \
fi; \
```

With the switch `off` the owner step script prints a notice and exits 0 before doing anything, and
the chart step is skipped without a notice. `PUBLIC_MCP_CHART_dev` need not change; it is `off` on
`main` today anyway. `make` reads both switches from the checkout it runs in, not from the
deployed tag.

**Before step 1**, record the baseline the rehearsal must leave unchanged, as superuser in
`-d butlers`: `SELECT state, count(*) FROM relationship.external_fact_proposals GROUP BY 1;`, and
note the deployed tag (handles above) as `tag`.

**Teardown.**

0. *Off switch.* Merge a reviewed PR that sets `PUBLIC_MCP_OWNER_STEP_dev := off` in
   `deploy/helm/butlers/makefile`, then `git pull` the checkout you deploy from. Verify:
   `grep -n '^PUBLIC_MCP_' deploy/helm/butlers/makefile` shows `PUBLIC_MCP_OWNER_STEP_dev := off`.
1. *Disable `all`* (layer 1):

   ```bash
   kubectl -n butlers-dev exec deploy/dashboard-api -c dashboard-api -- \
     /usr/local/bin/python -I /app/scripts/runtime_clis/python_entrypoint.py \
     butlers public-mcp disable all
   ```

   Verify (superuser, `-d butlers`): `SELECT state FROM mcp_surface.controls WHERE domain = 'all';`
   is `disabled`. Replication does not matter here: the server goes away next. The row stays
   disabled through the rebuild, which therefore starts closed.
2. *Scale to zero:*
   `kubectl -n butlers-public-dev scale deployment/butlers-public-mcp --replicas=0`. Verify:
   `kubectl -n butlers-public-dev get deploy,pods` shows `0/0` and no pod.
3. *Uninstall the public chart* (the release record lives in `butlers-dev`):
   `helm -n butlers-dev uninstall butlers-public-mcp`. What it leaves: the namespace
   `butlers-public-dev`, kept by `helm.sh/resource-policy: keep`, holding nothing of the chart's.
   Helm deletes the Deployment, the Service, the five NetworkPolicies, both ConfigMaps (keyring,
   switches), the three ExternalSecrets, the `PrometheusRule` and the `PodMonitor`; the Secrets the
   ExternalSecrets created go with them, because `creationPolicy: Owner` makes each ExternalSecret
   their owner [Inferred: External Secrets Operator garbage collection]. Bitwarden items, the site
   env file, the keyring file and everything in `postgres-dev` are untouched. Verify:
   `helm -n butlers-dev status butlers-public-mcp` reports the release not found, and
   `kubectl -n butlers-public-dev get deploy,svc,configmap,secret,externalsecret,networkpolicy,prometheusrule,podmonitor`
   lists only Kubernetes' own `kube-root-ca.crt` ConfigMap [Observed: 2026-11-22 rehearsal, which
   also confirms the Secrets went with their ExternalSecrets]. The rebuild's install
   reuses the namespace.
4. *Drop the subscription, then the publication* (superuser). First confirm the initial copy is
   complete, in `-d butlers_public`:

   ```sql
   SELECT s.oid, s.subenabled,
          (SELECT count(*) FROM pg_subscription_rel r
            WHERE r.srsubid = s.oid AND r.srsubstate <> 'r') AS not_ready
     FROM pg_subscription s WHERE s.subname = 'butlers_public_sub';
   -- one row, not_ready 0; note the oid for the fallback
   ```

   Then, each statement on its own:

   ```bash
   kubectl -n postgres-dev exec -i "$primary" -c postgres -- \
     psql -X -v ON_ERROR_STOP=1 -d butlers_public -c "DROP SUBSCRIPTION butlers_public_sub;"
   kubectl -n postgres-dev exec -i "$primary" -c postgres -- \
     psql -X -v ON_ERROR_STOP=1 -d butlers -c "DROP PUBLICATION butlers_public_v1;"
   ```

   Only a superuser can drop a `password_required = false` subscription. A plain `DROP
   SUBSCRIPTION` also drops the slot `butlers_public_sub` and any table-sync slots on the primary,
   through the subscription's own connection. *Fallback*, if the drop fails (for example
   `not_ready` was above 0, where a table-sync worker can deadlock a drop on this same-instance
   topology, RFC 0039 D3 step 4): run the owner step's own order, one `-c` each. In
   `-d butlers_public`: `ALTER SUBSCRIPTION butlers_public_sub DISABLE;`, then repeat
   `SELECT count(*) FROM pg_stat_subscription WHERE subname = 'butlers_public_sub' AND pid IS NOT NULL;`
   until it is 0, then `ALTER SUBSCRIPTION butlers_public_sub SET (slot_name = NONE);` and
   `DROP SUBSCRIPTION butlers_public_sub;`. Those leave the slots behind, so in `-d butlers`, with
   `<oid>` from the check above:
   `SELECT pg_terminate_backend(active_pid) FROM pg_replication_slots WHERE (slot_name = 'butlers_public_sub' OR slot_name LIKE 'pg\_<oid>\_sync\_%') AND active_pid IS NOT NULL;`,
   then
   `SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE (slot_name = 'butlers_public_sub' OR slot_name LIKE 'pg\_<oid>\_sync\_%') AND NOT active;`,
   then the `DROP PUBLICATION` above.
5. *Verify* (superuser, `-d butlers`):

   ```sql
   SELECT slot_name, slot_type, active FROM pg_replication_slots
    WHERE slot_name = 'butlers_public_sub' OR slot_name LIKE 'pg\_%\_sync\_%';  -- 0 rows
   SELECT count(*) FROM pg_replication_slots WHERE slot_type = 'logical';      -- 0
   SELECT count(*) FROM pg_subscription;            -- 0 (a catalog shared by every database)
   SELECT count(*) FROM pg_publication WHERE pubname = 'butlers_public_v1';    -- 0
   SELECT state, count(*) FROM relationship.external_fact_proposals GROUP BY 1;
     -- the baseline, apart from rows a pass imported meanwhile
   ```

   `make status-dev` (from `deploy/helm/butlers`) shows the `butlers-dev` pods running. Left behind
   by design, for the rebuild to reuse: `butlers_public` with its roles, the subscriber tables
   holding their last copied rows, the `mcp_read` views, `mcp_local.manifest_applied` with the old
   digest and the inbox; on the primary, `mcp_surface.controls` and `mcp_surface.manifest_state`.
   Removing `butlers_public`, the roles and the `pg_hba` lines reverses owner act 2 in homelab and
   is not part of the rehearsal.
6. *One deploy recreates nothing.* From the same checkout, redeploy the pushed tag without a
   build: `scripts/k8s/deploy-dev.sh "$tag"`. Check:
   - the output carries `public-mcp owner step: PUBLIC_MCP_OWNER_STEP_dev is off; skipped`, no
     `public-mcp chart` line (the recipe does not call it), and the deploy exits 0;
   - the step 5 queries still find no slot and no subscription. `butlers_public_v1` is still
     absent after a same-tag deploy: reconcile runs only in the `dashboard-api` migrations
     initContainer, and a deploy that leaves the `dashboard-api` pod spec unchanged does not
     restart it (observed in the 2026-11-22 rehearsal). A deploy of a new tag restarts the pod and
     its reconcile recreates the publication, which is inert without a slot or subscription and
     retains no WAL. Either outcome passes;
   - `helm -n butlers-dev status butlers-public-mcp` still reports not found, and
     `kubectl -n butlers-public-dev get pods` lists nothing.

**Rebuild.** It ends with the applied digest equal to the manifest digest, both flags at 1, and
`describe` answering.

1. *Switch back on.* Merge a reviewed PR that sets `PUBLIC_MCP_OWNER_STEP_dev := on` (it may also
   switch `PUBLIC_MCP_CHART_dev` on, `bu-y66o00.67`), and `git pull` the deploy checkout.
2. *Preflight.* Section 1, both tables (read-only). "Logical slots before first sync" holds again:
   no logical slot.
3. *Reconcile, then deploy.* If `SELECT count(*) FROM pg_publication WHERE pubname =
   'butlers_public_v1';` in `-d butlers` returns 0 (teardown step 6 deployed the same tag, so
   reconcile never reran), recreate it first, as the migration principal, with the teardown
   step 1 command and `reconcile --database butlers` in place of `disable all`. It prints
   `Primary projection reconciled`; the query then returns 1. Without the publication the owner
   step cannot build the subscription. Then deploy: `scripts/k8s/deploy-dev.sh "$tag"`. It reruns
   `init-db.sql` (3a) and the owner step (3c), which finds no slot, rebuilds and starts the
   initial copy. The output ends the step with `public-mcp owner step: applied`.
4. *Public chart.* If `PUBLIC_MCP_CHART_dev` is still `off`:
   `scripts/k8s/public-mcp-chart-deploy.sh dev "$tag" on`, from a checkout whose site env carries
   `BUTLERS_PUBLIC_MCP_AUDIENCE`, `BUTLERS_PUBLIC_MCP_ALLOWED_SUBS` and
   `BUTLERS_PUBLIC_MCP_KEYRING_FILE` (3d steps 3 and 4; without the keyring every call is refused).
   The ExternalSecrets sync again from the unchanged Bitwarden items.
5. *Copy complete.* In `-d butlers_public`,
   `SELECT srsubstate, count(*) FROM pg_subscription_rel GROUP BY 1;` shows only `r`; then the rest
   of the 3c verify queries.
6. *Live checks:* `scripts/k8s/public-mcp-live-checks.sh dev` exits 0.
7. *Re-enable:* the step 1 command with `enable all`. `describe` shows every domain `enabled`
   within 15 minutes.
8. *End state.*
   - `TABLE mcp_surface.manifest_state;` in `-d butlers` and `TABLE mcp_read.manifest_applied;` in
     `-d butlers_public` show the same digest.
   - In the cluster Prometheus, `butlers_public_mcp:critical_ok` and
     `butlers_public_mcp:warning_ok` both read 1, and both Kuma monitors are UP again.
   - `describe` from a connected client: `manifest_digest` is that digest, every domain is
     `enabled`, and `tools` lists 19 names.

**Widening and narrowing rehearsal (task 8.2).** A reviewed PR edits
`src/butlers/knowledge_read/projection.toml`: a narrowing removes a column (or a table), a
widening adds one; reverting the PR rehearses the other direction. Deploy it with
`scripts/k8s/deploy-dev.sh`, then the public chart by hand while `PUBLIC_MCP_CHART_dev` is `off`.

- *The window* runs from the migrations' reconcile until the owner step records the new digest.
  Data calls in it answer `projection_apply_pending`, and any call, `describe` included, sets
  `butlers_public_mcp_owner_step_pending{change="narrowing"}` or `{change="widening"}` to 1, so
  `PublicMcpOwnerStepPending` lowers the warning flag (section 5). The gauge is refreshed only by
  requests: call `describe` during the window to see it, and once after the step to return it to
  0. A narrowing left pending 24 hours would also turn the critical flag. Until the hand chart
  upgrade, the old pod answers `projection_version_mismatch` instead (section 4).
- *Narrowing* rebuilds the subscription and re-copies (D3 step 4). Verify as in leak runbook step
  5: the column is gone from the subscriber table and from `describe`'s `columns`; every answer in
  the window was a refusal, so none carried it.
- *Widening* that only adds a table needs no rebuild; a changed existing entry rebuilds. Verify the
  new column in the subscriber table and in `describe`'s `columns`.

**Rolling back below the search indexes (bu-y66o00.53).** An owner step from any tag before
#5867 cannot run over this one's projection:

- its prior-catalog guard refuses the two GIN indexes ("subscriber unowned/undeclared relation
  policy refused");
- its `_TRGM_SAFE` refuses the `similarity_op` grant;
- its `CREATE OR REPLACE VIEW` cannot drop the episodes view's two search columns.

On dev, `make deploy-dev` runs the owner step for the tag it deploys, so redeploying an older tag
fails at step 3 until the projection is reverted first. As the superuser in `butlers_public` (the
same `kubectl exec` path as section 1, with `-d butlers_public`), run, in one transaction:

```sql
BEGIN;
DROP INDEX IF EXISTS chronicler.episodes_public_mcp_search_title,
    chronicler.episodes_public_mcp_search_vector;
DROP VIEW IF EXISTS mcp_read.chronicler__episodes_public;
REVOKE EXECUTE ON FUNCTION mcp_ext.similarity_op(text, text) FROM mcp_public_ro;
COMMIT;
```

Then deploy the older tag at once. Its owner step recreates the view (absent views are created)
and grants `SELECT` on it. Until it does, a running new public server answers `search`,
`search_filtered`, `timeline` and `fetch` of an episode with `unavailable` or an error, so
disable the `time` domain first if the server is serving. Rolling forward again needs nothing:
the new step appends the columns and recreates the indexes and the grant.

**Restore drill.** CNPG backups include `butlers_public`, but nothing restores from it. A restored
instance drops `butlers_public` or keeps its subscription disabled, so a restored copy never
replicates from a live primary (D3 "Shared fate and capacity";
[Backup and Restore](backup-restore.md)).

## 8. Custody

Credential names, holders and mounts are the RFC 0039 D9 custody table; record names here, never
values. The publisher has no credential at all (OD14). `kubectl exec`, attach and debug on the
instance pods, pod creation, Secret read and `Cluster` writes in `postgres-<env>`, and node access
are already superuser-equivalent (D9, residual risk), so they stay with the owner.

On dev the owner step's superuser SQL is rendered from the deployed tag's git tree in the
deployer's checkout (3c), not taken from the privileged `dashboard-api` pod, so code in that pod
cannot reach the superuser path. The SQL trusts the tag's git objects and the dependencies `uv`
installs for them; the meta-command allowlist keeps `\!` and backticks off it (RFC 0039 D9,
residual risk).

## 9. Proposal importer

The Relationship job `public_mcp_proposal_import` (RFC 0039 D13 steps 2, 6 and 7; task 5.2) runs
inside `butlers-up` every 5 minutes (`roster/relationship/butler.toml`, `dispatch_mode = "job"`, no
model, no session). Code: `roster/relationship/tools/public_mcp_proposal_import.py`; table:
`relationship.external_fact_proposals` (payload and bookkeeping columns since `rel_043`, all
private: the publication still carries only `proposal_id`, `state`, `decided_at`).

**Credential.** It logs in to `butlers_public` as `mcp_proposals_reader` with the password from a
file, never an env var or `butler_secrets`:

| Setting | Default |
| --- | --- |
| `BUTLERS_MCP_PROPOSALS_READER_PASSWORD_FILE` | `/etc/butlers/mcp-proposals-reader/password` |
| `BUTLERS_MCP_PROPOSALS_DB_HOST` | the primary's host (`DATABASE_URL` or `POSTGRES_HOST`) |
| `BUTLERS_MCP_PROPOSALS_DB_PORT` | the primary's port |
| `BUTLERS_MCP_PROPOSALS_DB_NAME` | `butlers_public` |

The `butlers` chart mounts Secret `postgres-mcp-proposals-reader-user` (key `password`, in
`butlers-<env>`; Bitwarden `MCP_PROPOSALS_READER_DB_PASSWORD`) read-only at that path on
`butlers-up` as an **optional** volume. Without the Secret the pod still starts; each pass logs
`no mcp_proposals_reader password file is mounted; intake skipped`, counts
`butlers.public_mcp.proposal_import.intake_skipped`, and still reconciles and clears. The password is
re-read every pass, so a rotated Secret takes effect without a restart. Egress is the existing
`butlers-up` database site on 5432 (`scripts/egress-profile-coverage.json`).

**One pass, in order.**

1. *Switch:* the `proposals` and `all` rows of `mcp_surface.controls` on the primary. A missing
   row or a failed read counts as disabled. The replicated copy, the break-glass ConfigMap and the
   canary latch do not affect the importer.
2. *Intake:* up to 100 inbox rows by `received_at`, then `proposal_id`; one primary transaction
   inserts them as `imported` with `ON CONFLICT DO NOTHING`; after it commits, the importer deletes
   exactly those ids from the inbox. A crash between the two leaves duplicates in both places that
   the next pass absorbs. Intake runs while the switch is off: rows are kept, unparked.
3. *Validate and park* (switch on only): each `imported` row received at least 30 seconds before the
   pass, oldest first, one transaction each under an advisory lock. First failing check wins:
   `invalid` (incomplete payload, predicate outside `proposable.toml`, unrepresentable client label
   or non-`http(s)` evidence URL, a missing, merged or deleted entity, or an owner subject);
   `invalid` (the response DLP's counted rules match the note or URL); `duplicate` (identical
   active fact); `conflict` (an earlier proposal used the same `(sub, cid, idempotency_key)`);
   `rate_limited`. Otherwise `park_external_fact_proposal` parks one action and the row becomes
   `parked` with its `pending_action_id`, in the same transaction.
4. *Expire and reconcile:* the importer expires its own parked actions past `expires_at` (nothing
   else sweeps ordinary approvals), then maps each action: `executed` to `applied`, `rejected` to
   `owner_rejected`, `expired` or `cancelled` to `expired`, `abandoned` or an `approved` action with
   an `action_execution_failed` event to `apply_failed`. A failed execution is still retryable in
   approvals; if the retry executes, `apply_failed` moves on to `applied`. An approval that
   arrives after the subject or object was merged, deleted or reaped by the enrichment job
   fails its replay closed (`relationship_assert_fact` re-checks both ids for an `external_mcp:`
   action), so it is reported `apply_failed` and no fact is written on a tombstone.
5. *Retention:* 30 days after `decided_at`, a terminal row's `note` and `evidence_url` become
   NULL; ids, state and timestamps stay.

**Rate limits.** Per `(sub, cid)`. *Today* is the UTC calendar day of the proposal's own
`received_at`: the 21st proposal received that day (any state, ordered by `received_at` then
`proposal_id`) is `rate_limited`, whenever the pass runs. *Unresolved* is `state = 'parked'`: with
50 already parked and undecided, the next is `rate_limited`. Both are terminal; the client proposes
again later.

**Metrics** (OTel meter `butlers`; ids and states only, never notes, URLs or client values):
`butlers.public_mcp.proposal_import.outcomes{state}`, `.copied`, `.errors{step}`,
`.intake_skipped{reason}`, `.inbox_depth` (gauge) and `.switch_enabled` (gauge). The D16 rules
"inbox undrained above 500" and "any `apply_failed`" read the inbox-depth gauge and the
`state="apply_failed"` outcome (in Prometheus: `butlers_public_mcp_proposal_import_inbox_depth` and
`butlers_public_mcp_proposal_import_outcomes_total{state="apply_failed"}`); the chart's alert rules
`PublicMcpInboxUndrained` and `PublicMcpProposalApplyFailed` read them (3e).

**Checks.** Inbox depth from the projection owner connection:
`SELECT count(*) FROM mcp_inbox.proposals`; states on the primary:
`SELECT state, count(*) FROM relationship.external_fact_proposals GROUP BY 1`. A row stuck in
`imported` while the switch is on means its park failed: the pass log names the proposal id and the
error class only.

### End-to-end proposal run (task 5.7)

Hermetic: `tests/integration/test_public_mcp_proposal_e2e.py` drives `propose_fact` through the
real host, one importer pass, an owner approval plus the shared executor, an owner rejection and an
expiry, then reads `applied`, `owner_rejected` and `expired` through `fetch("proposal:<id>")`.

Live dev run, **live leg tracked in `bu-y66o00.48`**, blocked on the `bu-y66o00.6` deploy and on a dashboard deploy that carries the `bu-y66o00.9` typed review (not run yet): `scripts/k8s/public_mcp_proposal_e2e.py`
with three synthetic entity pairs, tokens read from files:

```bash
public_mcp_proposal_e2e.py propose --mcp-url URL --token-file F --state S \
    --applied A:B --rejected C:D --expired E:F
# wait for an importer pass (every 5 minutes)
public_mcp_proposal_e2e.py decide --dashboard-url URL --token-file F --state S
public_mcp_proposal_e2e.py verify --mcp-url URL --token-file F --state S
```

`decide` echoes the action's `external_proposal_review.displayed_call` into the approve body, as
the dashboard does (the generic `call_preview` of an external proposal is always null; see
[Approvals module § External MCP fact proposals](../modules/approvals.md#external-mcp-fact-proposals)).
A review without a displayed call, for example after an entity was merged or deleted, makes `decide`
report `BLOCKED` with the review's stated reason for the applied path; the reject path still runs. All URLs must be https. `expired` needs the 14-day window to pass, or the owner setting that one synthetic action's
`expires_at` into the past; `verify` reports it `PENDING`, never as a pass, until then.

## Related

- [RFC 0039](../../about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md): D3 placement and
  the owner step, D8 network, D9 roles and custody, D16 alerting, D17 failure modes, Rollout.
- [Deployment topology](../../about/lay-and-land/deployment.md): where each piece runs.
- [Kubernetes Deployment](kubernetes-deployment.md) and
  [Reviewed bootstrap](../data_and_storage/reviewed-bootstrap.md): the `butlers` release and
  `init-db.sql`.
- OpenSpec change `openspec/changes/add-public-mcp-read-surface/` (`tasks.md` is the work map).

## Proposal intake: `propose_fact`

The public server's one write (RFC 0039 D13 step 1, built by `bu-y66o00.8`). It validates a closed
argument set, then inserts one row into `mcp_inbox.proposals` on `butlers_public`. Nothing reads the
row until the importer (section 9) pulls it, and nothing is applied until the owner approves it.

- **Arguments:** `subject_entity_id`, `predicate`, `object_entity_id`, optional `evidence_url`
  (absolute `http` or `https` URL with a host, no credentials or whitespace, at most 2048
  characters), optional `note` (at most 500) and `idempotency_key` (1 to 64). Anything else is
  `invalid_argument`. `predicate` must be in `src/butlers/knowledge_read/proposable.toml`:
  `knows`, `colleague-of`, `friend-of`, `co-attended`, `visited`, `works-at`, `member-of`. Widening
  that file is a reviewed change; a family-gated, contact or override predicate, or a literal
  object, is refused and writes nothing. The tool schema's `predicate` enum is generated from it.
- **Identity:** `proposal_id = uuid5(NS, name)`, where `name` is the RFC 8785 (JCS) text of the JSON
  array `[sub, cid, idempotency_key, sha256_hex(JCS(payload))]` and `payload` is the object
  `{subject_entity_id, predicate, object_entity_id, evidence_url, note}` (absent optionals are
  `null`). `sub` and `cid` come from the verified assertion, never from arguments, so one caller's
  key cannot collide with another's. `NS` is `91aad1a5-9534-5720-865f-e229e55a3b92`, fixed forever.
- **Write:** `INSERT ... ON CONFLICT DO NOTHING` with no read: the role has column-limited INSERT and
  no SELECT, so an identical retry is absorbed by the primary key and returns the same id. A changed
  payload under a reused key gets a new id; the importer marks it `conflict`.
- **Full inbox:** at 1,000 undrained rows the cap trigger refuses the insert and the call returns
  `unavailable` with `retryable: true` and no row. Only that exact trigger error is mapped; any
  other database error also surfaces as `unavailable` through the host's fail-closed path. A retry
  of a proposal already in the inbox is still a no-op. Draining is the importer's delete.
- **Kill switch:** `proposals` (or `all`), replicated or in the break-glass file, returns
  `domain_disabled` before any write. The check runs inside the write's own transaction.
- **Transaction:** every other tool runs in a read-only repeatable-read snapshot. `propose_fact`
  alone is flagged `writes` and runs in an ordinary read-write transaction opened by the same host
  path, after the same canary probe, budget (20 per `(sub, cid)` per day) and projection checks.
  The host refuses to start if any other tool is flagged. The database role, not this flag, is the
  authority bound.

The primary proposal-status prerequisite is Relationship revision `rel_041`. It
stores caller-assigned proposal IDs, the D13 primary state and decision timestamp,
and private retention-capable note/evidence fields. The manifest exposes only
`proposal_id`, `state` and `decided_at` in the `proposals` domain. `rel_043` adds the
importer's payload and bookkeeping columns without changing that column list; the importer,
approval reconciliation and 30-day text clearing are described in section 9.

## Adversarial case coverage (task 6.1)

Each adversarial case from `tasks.md` 6.1 maps to an executing test. "PG17" tests need Docker and
run in the CI integration lane (`BUTLERS_REQUIRE_DOCKER=1` there turns a skip into a failure); the
rest are unit tests. The alert-rule cases need the pinned `promtool` (`make install-lint-tools`).
Paths are under `tests/` unless they start with `roster/`.

| Case | Executing tests | Kind |
| --- | --- | --- |
| Replayed assertion | `capability_host/test_assertion.py::test_replayed_assertion_is_refused`, `::test_assertion_from_before_a_restart_is_refused` | unit |
| Expired or future assertion | `capability_host/test_assertion.py::test_expired_assertion_is_refused`, `::test_clock_bounds` | unit |
| Body swap (`bsha`) | `capability_host/test_assertion.py::test_body_swap_is_refused`, `::test_method_and_tool_are_bound`, `::test_query_string_is_bound_and_path_is_not` | unit |
| Forged identity headers | `capability_host/test_transport.py::test_forged_identity_headers_without_an_assertion_are_refused`, `::test_forged_identity_headers_never_override_the_assertion`, `::test_a_bad_assertion_on_an_exempt_method_is_refused`; `capability_host/test_write_path.py::test_the_write_sees_sub_and_cid_from_the_assertion_only` | unit |
| Bearer-only call | `capability_host/test_transport.py::test_bearer_alone_cannot_call_a_tool`, `::test_tool_call_without_assertion_is_refused_with_no_query` | unit |
| Oversized body | `capability_host/test_transport.py::test_oversized_body_is_refused`, `::test_a_non_ascii_content_length_is_a_size_refusal`; `mcp_public/test_app.py::test_an_oversized_streamed_body_stops_being_read_past_the_limit` | unit |
| JSON-RPC batch body | `capability_host/test_transport.py::test_batch_request_is_refused`, `::test_duplicate_keys_are_refused` | unit |
| Denylisted manifest edit | `knowledge_read/test_projection_manifest.py::test_closed_manifest_refuses_positioned_policy_mutations`, `::test_credential_guard_has_a_type_consistent_control`, `::test_marks_and_entity_flags_cannot_expand_privacy`; `knowledge_read/test_projection_drift_contract.py::test_check_1_and_3_bad_manifest_edit_goes_red` | unit |
| Credential-named JSON paths (snake, camel, kebab) | Finance: `migrations/test_public_projection_source.py::test_finance_health_json_paths_credentials_and_lifecycle`. Health: `::test_health_metadata_credential_paths_never_materialize`, `::test_health_typed_shapes_and_qualification`. Marks: `knowledge_read/test_projection_manifest.py::test_a_credential_json_path_cannot_be_marked_in_any_key_style` | PG17, unit |
| Pre-signed and userinfo URLs in marked columns | `capability_host/test_dlp.py::test_signed_and_userinfo_urls_are_redacted_on_marked_columns`, `::test_signed_and_userinfo_urls_are_redacted_on_marked_columns_and_paths` | unit |
| Canary leak | `capability_host/test_containment.py::test_a_canary_in_a_response_latches_the_surface_off`, `::test_canaries_are_found_through_normalization_and_in_keys`, `::test_the_canary_check_runs_before_dlp_could_hide_it`; `integration/test_capability_host_projection.py::test_a_canary_in_the_projection_latches_on_the_probe`; `integration/test_public_mcp_owner_lifecycle.py::test_canaries_in_excluded_sources_are_absent_after_a_full_sync` | unit, PG17 |
| DLP evasion (full-width digits, zero-width characters, separators) | `capability_host/test_dlp.py::test_separators_and_invisible_characters_do_not_defeat_the_scan`, `::test_zero_width_characters_inside_a_card_number_are_removed_before_the_scan`, `::test_space_and_dash_separators_do_not_defeat_the_luhn_rule` | unit |
| `test_decoding` slot created as the publisher | `integration/test_public_mcp_owner_lifecycle.py::test_missing_slot_rebuilds_stale_data_and_invalid_slot_refuses[test_decoding]` (owner step refuses); `scripts/test_public_mcp_alert_rules.py::test_condition_drives_its_flag[foreign logical slot]` (critical alert); `integration/test_public_mcp_owner_lifecycle.py::test_actual_tcp_principals_and_no_credential_guards` (no role can become the publisher) | PG17, promtool |
| Temporary-table shadowing as `mcp_public_ro` | `integration/test_public_mcp_owner_lifecycle.py::test_real_privileges_inbox_cap_and_definer_search_path` (`CREATE TEMPORARY TABLE` refused; definer functions pin `search_path`), `::test_actual_full_copy_catalog_views_freshness_and_noop` (no `TEMPORARY` privilege); `integration/test_public_mcp_server.py::test_the_reader_pool_pins_search_path` | PG17 |
| Evidence URL carrying instructions | `mcp_public/test_proposals.py::test_an_evidence_url_carrying_prose_instructions_is_refused`, `::test_an_encoded_instruction_url_is_stored_only_as_a_bound_value`; `roster/relationship/tests/test_public_mcp_proposal_import_unit.py::test_an_instruction_bearing_evidence_url_reaches_the_owner_as_scheme_and_host_only`; `integration/test_public_mcp_proposal_import.py::test_an_instruction_carrying_proposal_parks_as_inert_data` | unit, PG17 |
| Proposal targeting the owner or a family predicate | `mcp_public/test_proposals.py::test_only_the_allowlist_is_proposable`; `knowledge_read/test_proposable.py::test_no_proposable_predicate_is_family_gated_contact_or_override`; `integration/test_public_mcp_proposal_import.py::test_each_validation_outcome` (owner subject and family predicate are `invalid`) | unit, PG17 |

Known gaps, filed rather than fixed here because each changes a stated rule: `bu-y66o00.70`
(token-only userinfo, `secret`/`apikey` query names and non-Cf invisible fillers in response DLP)
and `bu-y66o00.71` (acronym, concatenated and digit-suffixed credential key names, which the
`json_fields_public` trigger and the manifest guard both tokenize the same narrow way).
