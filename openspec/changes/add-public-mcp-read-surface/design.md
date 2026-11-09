## Context

`proposal.md` gives the problem, the scope and the non-goals. The design
contract is RFC 0039, and this file does not repeat it. It maps RFC decisions to
capabilities, and records the few spec-level choices that the RFC left open or
that this change had to settle. Baseline: `origin/main` at `9f723b9468`.

Terms used throughout:

- **Primary**: the live `butlers` database on CNPG `postgres-<env>`.
- **Projection**: the `butlers_public` database, a second database on the same
  cluster (OD10), fed only by logical replication from the primary.
- **Public server**: the `butlers-public-mcp` Deployment.
- **Assertion**: the Worker-signed per-call Ed25519 token (RFC 0039 D7).
- **Domain**: a kill-switch unit, one of `knowledge`, `relationship`, `time`,
  `travel`, `finance`, `health` and `proposals`.

## Goals / Non-Goals

**Goal.** The owner's ChatGPT and claude.ai clients read Butlers context over
MCP, deterministically, with no Butlers model spend. They can also propose
Relationship triples, which take effect only after the owner approves each one.

**Design boundary.** No LLM is on the request path. The public server reaches
nothing but DNS and the PostgreSQL port of the instance that holds the
projection, where `pg_hba` admits it to `butlers_public` alone. The only write
is an inbox row. Non-goals are
listed in `proposal.md` § Out of Scope.

## Decisions

The decisions are RFC 0039's. This table says which capability spec makes each
one observable.

| RFC 0039 | What it decides | Specified in |
|---|---|---|
| D1 | Standalone server; package layering and import direction | `public-mcp-surface` REQ-001 |
| D2 | One publication; replica-identity filters, so a restriction arrives as a DELETE; no JSONB published; source-owned materializations (Chronicler, with `sensitive` episodes published and overrides that can only narrow unless the owner submits them; entity flags; finance overlays; health values; the finance and health JSON fields of OD17, accepted by OD19; and the graph `publish_key` and per-source withheld counts, so edges carry the facts' own predicate allowlists) | `public-mcp-projection` REQ-001, -002, -005, -006, -007, -013 |
| D3 | Projection placement (OD10): a second database on `postgres-<env>`, isolated by ordered `pg_hba` reject lines and grants (roles, not the shared `10.42.0.0/24` range, separate the pods); same-instance subscription with a pre-created slot, created by the owner step (OD14), which every dev deploy runs after its migrations (OD20); shared fate with the primary, including the shared, 90 % full disk | `public-mcp-projection` REQ-003, -008, -011, -015; `public-mcp-authorization` REQ-008 |
| D4 | One manifest; generated DDL; reconcile (publication and replicated digest) split from the owner step (slot, subscription, purge, applied digest); five drift checks, with a two-tier column denylist (credential tier everywhere, breadth tier outside finance and health, OD17); narrowing purges; v1 exposure defaults with domains per table, finance and health at full breadth (OD17), facts about other people published up to the contact, identity and credential line (OD21) | `public-mcp-projection` REQ-001 to -004, -012, -015 |
| D5 | One server for every domain; domains attach to data; static `tools/list`; no tiers or unlock | `public-mcp-authorization` REQ-009 |
| D6 | Hop bearer, digest-only, constant-time | `public-mcp-authorization` REQ-001 |
| D7 | Assertion format, verification order, keyring, replay set, single replica | `public-mcp-authorization` REQ-002 to -007 |
| D8 | Namespace `butlers-public-<env>`, default-deny; one allow to the `postgres-<env>` instance pods, widening nothing else; live policy check; the precondition in `butlers-<env>` | `public-mcp-authorization` REQ-008; `public-mcp-projection` REQ-014; `deployment-hardening` REQ-013 |
| D9 | Roles on both databases; one Butlers-managed REPLICATION role, primary-read-all and with no credential (local peer login only, OD14); the subscription owner, kept as created (OD15); `mcp_surface` schema; slot safety | `public-mcp-projection` REQ-009, -010, -011; `database-security` REQ-016 |
| D10 | The 19-tool contract, including the finance and health records tools (OD17, accepted by OD19), and the ChatGPT compatibility shapes | `public-mcp-surface` REQ-002 to -010, -012, -014 |
| D11 | Lexical search in v1 | `public-mcp-surface` REQ-003, -006 |
| D12 | Reads never write | `public-mcp-surface` REQ-011 |
| D13 | `propose_fact` pipeline and states | `public-mcp-proposals` (all); `module-approvals` REQ-010; `dashboard-approvals` REQ-904; `relationship-facts` REQ-011 |
| D14 | Structural no-LLM guarantees | `public-mcp-surface` REQ-001 |
| D15 | DLP (credential rules everywhere; identifier and personal-contact rules outside finance and health, OD17), canaries, audit, budgets; the OD17 threat-model assumption | `public-mcp-authorization` REQ-010 to -013 |
| D16 | Kill-switch layers, leak runbook, and alerting as Prometheus rules collapsed into two flags that Uptime Kuma polls (OD11), including the owner-step pending warning and the 24-hour narrowing critical, which on dev signals that the automatic owner step is failing (OD20) | `public-mcp-authorization` REQ-009, -014; `public-mcp-proposals` REQ-007; `dashboard-settings-console` REQ-001 and MODIFIED Settings Console Page (REQ-002) |
| D17 | Freshness thresholds, the fail-closed applied-digest check, error codes (including `projection_apply_pending`) and precedence, failure table | `public-mcp-surface` REQ-005, -013; `public-mcp-projection` REQ-010, -015 |

The RFC 0038 amendment (D5, D6, D7 and D13, amended in place) is not specified
here. It goes into `generalist-lead-focused-delegation`. See `proposal.md` §
Coordinated outside this change.

## Spec-level decisions

These choices were needed to write observable requirements. RFC 0039 does not
make them, or leaves them to "the spec". Each is conservative and reversible,
and the conformance review checks them against OD1 to OD21.

### S1. Entity flags become published columns on `public.entities`

[Observed] Today three entity flags are JSONB keys in `public.entities.metadata`:
the merge pointer `merged_into`, the soft-delete stamp `deleted_at`
(`alembic/versions/core/core_002_identity.py:124-127`) and the
unidentified-sender marker `unidentified` (`src/butlers/identity.py:459`). An
unidentified sender's entity is named `Unknown (<channel> <value>)`, which
embeds the raw email address or phone number (`src/butlers/identity.py:1067-1071`).
`metadata` is never published (D4), and PG17 does not publish generated columns
(D2 rule 6). [Observed] The dashboard promote route clears `unidentified`
without renaming the entity (`src/butlers/api/routers/memory.py:2866-2872`),
so `unidentified` alone does not keep the placeholder name out; the sender's
channel and value stay in `metadata` as `source_channel` and `source_value`
(`src/butlers/identity.py:1208-1210`). A core migration therefore adds four
plain columns, `merged_into UUID NULL`, `deleted BOOLEAN NOT NULL`,
`unidentified BOOLEAN NOT NULL` and `placeholder_name BOOLEAN NOT NULL`, and a
`BEFORE INSERT OR UPDATE` trigger keeps them equal to the JSONB keys, with
`placeholder_name` true while `canonical_name` still equals the
`Unknown (<source_channel> <source_value>)` form. [Observed] Other routes hide an entity
from active surfaces through `tombstone`, `archived`, `archived_at` and `listed`
(`roster/relationship/api/router.py:1161-1174,5755`, `roster/relationship/tools/contacts.py:336`),
and readers treat any present `merged_into` as a tombstone, so `deleted` is also true for any
of those and for a `merged_into` that is not a UUID, whose column stays NULL.
A contract test proves parity. Existing readers keep reading the JSONB keys; moving them is out of
scope. The manifest publishes `merged_into` and filters entities on `deleted
= false AND unidentified = false AND placeholder_name = false`, with the three
flags in the replica identity and in the column list (D2 rule 2; no view
returns them), so deleting an entity, creating an unknown sender or promoting
one without renaming it never publishes it. A Telegram chat id in such a name
matches no DLP rule, so the flag, not response-time DLP, is the control. `entity_lookup`
resolves a merged entity to its survivor, and the importer treats a merged or
deleted subject or object as `invalid`.

*Rejected:* publishing `metadata`, which carries unreviewed keys; an exporter
table for entities, which adds a second pipe for three columns.

### S2. External apply is an ADDED Relationship requirement, not a MODIFIED REQ-relationship-facts-005

An earlier draft of RFC 0039's traceability table named "MODIFIED
`relationship-facts` REQ-005"; the table now reads "ADDED REQ-011".
REQ-005's replay rules do not change. They apply unchanged to an
importer-parked action, because the importer writes `fact_approval_context`
like any other park. What is new is the authority outcome: an owner approval of
a third-party frozen report produces a verified fact that keeps
`third_party` authority. That outcome is stated once, as
REQ-relationship-facts-011. This avoids restating the full REQ-005 text and
risking drift.

[Inferred] The private admitted-decision hook (`record_admitted_approval`)
currently refuses a fresh approval that has no owner-class admitted authority.
REQ-relationship-facts-011 makes the required outcome observable. The task list
proves that the hook passes, or amends it test-first, without converting the
authority to owner.

### S3. A full inbox refuses with `unavailable`, retryable

D13 caps undrained inbox rows at 1,000 but names no client-visible error.
`public-mcp-proposals` REQ-003 uses the existing D17 code `unavailable` with
`retryable: true`. It does not use `budget_exhausted`, because the cap is
global, not a per-user budget, and it must not disclose other users' volume.

### S4. The `proposals` kill switch also pauses importer parking

D16 defines switches as tool-level. For incident response, a disabled
`proposals` domain (or `all`) also stops the importer from parking new
proposals. Rows stay `received` or `imported` until the switch is re-enabled.
Actions already parked stay on the approvals page for the owner to decide.
Without this, a flood that was already accepted would still reach the owner's
queue during an incident. The importer reads the switch at its source: the
Relationship runtime role gets `SELECT` on `mcp_surface.controls` on the
primary and nothing else in that schema (`database-security` REQ-016). The
break-glass ConfigMap and the canary latch live with the public pod and affect
only the public server.

### S5. The Settings panel is a dashboard surface and needs its own delta

D16 puts the routine switch on the dashboard Settings page.
`review-and-documentation.md` § Spec discipline requires a dashboard capability
for a new panel, so `dashboard-settings-console` gains REQ-001. The panel writes
`mcp_surface.controls` on the primary through an owner route. It cannot see the
public pod, so it reports "takes effect within 15 minutes" and names the
break-glass path rather than claiming immediate effect.

### S6. "No session or goal is created" is the observable no-LLM test

The import ban, network policy and capability declaration are structural (D14).
The observable witness is behavioral. Over a run of every tool, including
`propose_fact` and the importer, the counts of rows in `sessions`,
`route_inbox`, `goal_inbox`, schedules and `ingestion_events` stay unchanged,
and no model-provider request is attempted (`public-mcp-surface` REQ-001).

### S7. Finance and health values are published from trigger-maintained materializations

[Observed] The finance tools show an overlay, not the raw columns: the
merchant is `COALESCE(metadata->>'normalized_merchant', merchant)` and the
category is `COALESCE(metadata->>'inferred_category', category)`
(`roster/finance/tools/spending.py:181,197`). A `normalized_merchant` column
exists on `finance.transactions` (finance migration 006), but the overlay the
tools read is the JSONB key. `finance.transactions` has no account label, only
`account_id`, and `finance.accounts` holds `name` and `last_four` next to
`institution` and `type`. Health measurements, medications and conditions are
memory facts in the `health` schema whose values are `metadata` keys, next to
free-text `notes` (`roster/health/tools/measurements.py:133-135`,
`medications.py:96-108`, `conditions.py:88-92`). Column lists cannot select a
JSONB key, and a JSONB column is refused by type in every domain (D4 check 3).

Two designs were weighed:

- *(a) Trigger-synced plain columns on the source table*, as S1 does for
  entities. Chosen for finance: a `BEFORE INSERT OR UPDATE` trigger keeps
  `display_merchant` and `display_category` equal to the overlay expressions.
  Since OD17, `finance.accounts` is published in full (`name` and
  `last_four` included), and the `mcp_read` view still derives
  `account_label` from institution and type.
- *(b) Source-owned public tables*, as Chronicler does. Chosen for health,
  because the source is the shared memory `facts` table, where adding
  domain-specific columns would diverge from the memory module's schema. An
  `AFTER INSERT OR UPDATE OR DELETE` trigger on `health.facts` maintains
  `health.measurements_public`, `health.medications_public` and
  `health.conditions_public`, which since OD17 carry `notes` and cover active
  facts of every sensitivity.

OD17 shares everything finance and health hold, and much of it lives in
JSONB: the finance fact layer keeps amounts, merchants, account names and
`last_four` in `metadata` (`roster/finance/tools/facts.py:19-20,256-266,533-535`),
and health facts keep their values there. A JSONB column still cannot be
published (column lists cannot narrow it, and condition 1 needs every
published path checked for credentials), so pattern (b) is generalized: one
generic trigger function, attached to every published finance and health
table with a JSONB column, keeps `finance.json_fields_public` and
`health.json_fields_public` equal to every scalar leaf, one row per dotted
path, and skips any path with a segment on the credential tier of the
denylist, after splitting each key on separators and camelCase boundaries.
`row_id` is the text of the source table's declared single-column key. `raw_data` on `finance.transactions` has no writer today
[Observed: no reference outside `roster/finance/migrations/006`], so it adds
nothing yet.

All of these use database triggers rather than writer code, so every write
path is covered without a sweep. The graph follows patterns (a) and (b) for
the same reason (RFC 0039 D2). [Observed] `public.entity_graph_edges.predicate`
is nullable because withheld stubs carry none
(`alembic/versions/core/core_215_entity_graph_edges.py:146,173-181`), so it
cannot sit in a replica-identity index, and a `sensitivity = 'normal'`
filter alone publishes edges of facts the fact filters exclude. Pattern (a)
adds `publish_key` (`<source_schema>.<source_table>:<predicate>` on a live
edge, `''` on a stub), which the generator filters with the union of the
edge-producing fact entries' predicate allowlists; pattern (b) keeps
`public.entity_graph_withheld_counts` per source, published only for those
same sources, so finance and health stubs are never counted as withheld
while `health_records` and `finance_records` serve the facts. **Upkeep**: one
trigger function per source (five trigger sources in total with S1, the
graph and the JSON-field function), each with a parity test that fails in the PR of any
migration that renames a key it reads. This is upkeep beyond the single
manifest (OD1). The owner accepted it for the first four (OD13) and for the
JSON-field function (OD19), with the parity tests and the CI drift checks as
its mitigation; the graph `publish_key` extends the graph source's existing
trigger pair and parity test.

Outside finance and health, the first draft also put interactions and every
`relationship`-schema memory fact out of v1, because they hold contact
details and email-derived summaries. OD21 lifts that: those facts are about
people other than the owner, and the line is now predicate kind and
credentials (RFC 0039 D4). The `relationship` memory schema publishes under a
literal predicate allowlist with no contact- or identity-kind predicate, so
interactions reach `entity_profile` as facts, and the personal-contact DLP
rules redact email addresses and phone numbers in their text. They match no
street address, so an address written into such text is published; the
predicate allowlist is the only control there (RFC 0039 D15 residual risk).
In finance and health the OD21 line is not applied, because OD17 shares
everything those domains hold; the owner chose that carve-out (OD22).

### S8. The owner step's shape and the fail-closed bound

OD14 fixes that the slot and subscription work is one owner step and that the
server fails closed while it is pending. Four details were needed to make that
observable, each conservative and reversible:

- **Rebuild, not per-table resync.** [Inferred] PostgreSQL has no command
  that re-copies one table of a live subscription, so any changed or removed
  entry of an already-applied table makes the file drop the subscription,
  truncate every mirrored table and re-copy. A table that is only added is
  picked up by `REFRESH PUBLICATION` without a rebuild.
- **The applied record carries the manifest, not only its digest.** The
  server needs the direction of a pending change (narrowing or widening) to
  pick the alert severity, and a digest alone cannot tell it.
  `mcp_local.manifest_applied` therefore stores the canonical manifest text
  next to the digest, readable only through `mcp_read.manifest_applied`.
- **New code, not a reuse.** `projection_apply_pending` is a separate code
  from `projection_version_mismatch`, so the owner can tell "deploy the
  migration Job" from "run the owner step". It sits right after it in the
  precedence, because the owner step refuses to run before reconcile.
- **24 hours to critical, for narrowing only.** The surface already refuses,
  so the remaining exposure is excluded rows still in `butlers_public`. A
  widening left pending costs only availability and stays a warning. On dev
  every deploy runs the step, whatever the release returned (OD20, S9), so a
  pending state that outlives the deploy means the automatic step failed and
  printed its marker, and the critical is its backstop.

The publisher's schema `USAGE` and table `SELECT` sync lives in
`scripts/init-db.sql` (RFC 0039 D9) and is repeated by the owner step, so
that a manifest change does not also need an `init-db.sql` rerun. The initial
copy needs both, and [Observed: `postgres-dev`, 2026-11-04] the publisher
holds `USAGE` on no Butlers schema today.

The owner-step guard checks the publication itself against rows embedded in
the file, not only the digest in `mcp_surface.manifest_state`, and only the
migration user can write that digest, in the same transaction as the
publication change. A forged or early digest therefore cannot let the step
record a manifest the publication does not yet match.

### S9. The dev deploy's owner step

OD20 fixes that `make deploy-dev` runs the owner step after the migrations
and that no manual step remains on dev. Five details make that observable,
each conservative and reversible:

- **A separate script after the release, whatever it returned.** [Observed]
  `deploy-%` depends on `bootstrap-%`, which runs `init-db.sql` before
  `helm upgrade`, and `record-deploy.sh` returns after the `dashboard-api`
  rollout, whose `migrations` initContainer runs reconcile, and after the
  `butlers-up` and `butlers-control` rollouts and the readiness wait, any of
  which can fail after reconcile has run (`deploy/helm/butlers/makefile:33-40`,
  `scripts/k8s/record-deploy.sh:47-74`). The step is therefore a second
  recipe command, `scripts/k8s/public-mcp-owner-step.sh <env> <tag>`, that
  runs whatever `record-deploy.sh` returned; the recipe exits with the first
  non-zero status. The file's guard refuses when the migrations did not
  reconcile this manifest, which is fail-closed. Gating the step on the
  release's exit would leave a narrowed publication unpurged after a
  readiness failure, with no marker. Putting it in `bootstrap-dev` would run
  it before reconcile, where its guard stops.
- **Rendered from the tag's git tree, captured and allowlisted first.** The
  script requires a running `dashboard-api` pod whose image tag equals
  `<tag>` (and fails if none does), then renders the file from that tag's
  commit in the deployer's checkout (`git archive`, `uv run --project
  <tree> butlers public-mcp owner-sql`), as `bootstrap-db.sh` reads
  `init-db.sql` with `git show <tag>:`. It never takes the file from a pod:
  `dashboard-api` is privileged and writable (amended in review,
  2026-11-09). A rollback tag or a dirty working tree can never produce a
  file for a different manifest, a generator that dies midway feeds psql
  nothing, and a psql meta-command allowlist keeps `\!` and backticks off
  the superuser path (RFC 0039 D3, D9).
- **Every stop is an error.** The file never uses `\q`; each guard stop
  raises an error, so psql exits non-zero.
- **Fail loud, never roll back.** A failure exits non-zero with a fixed
  marker; the release, its deployments row and the publication stand. The
  server is fail-closed (`projection_apply_pending`) when a manifest change
  or a rebuild is pending; with an unchanged manifest and no rebuild the
  applied projection keeps serving and only the marker reports the failure.
  An image without the command, and any environment other than `dev`, print
  a notice and exit 0.
- **The public chart follows, and an off switch holds a teardown.** After a
  successful step the recipe upgrades `deploy/helm/butlers-public-mcp` at the
  same tag, so the public pod's shipped manifest converges in the same
  deploy. A committed `PUBLIC_MCP_OWNER_STEP_dev := off` in the makefile makes
  the script exit 0 at once and the recipe skip the public chart, so a dev
  teardown is not recreated by the next deploy.

## Risks / Trade-offs

- **Labels written by LLMs.** Sensitivity labels come from LLM writers
  ([Observed] `store_fact` defaults to `normal`). So the manifest exposes memory
  facts only through literal per-schema predicate allowlists with no contact-
  or identity-kind predicate (OD21) and literal column lists, graph edges take
  the same allowlists (RFC 0039 D2), and the DLP scan is load-bearing, not
  cosmetic. A label can only narrow, never widen: a `pii` memory fact or edge
  is withheld, and a Chronicler override from the LLM-callable correction tool
  can only raise an episode's privacy; only an override the owner submits on
  the dashboard can lower it. Canaries detect a manifest mistake in CI and in
  production.
- **Steered clients.** A client model can be steered by content it reads
  (prompt injection) into reading any enabled domain under the owner's valid
  session and leaking it through its own tools. Per-client budgets, volume
  alerts, audit and kill switches bound and detect this; nothing on the
  Butlers side can prevent it (RFC 0039 D15).
- **Publisher access.** The `REPLICATION` role can decode every change on
  the primary regardless of grants. It has no credential (OD14): it logs in
  only by local peer authentication inside the instance pods, every TCP login
  as it is rejected, no `replication` entry admits it, no role is a member of
  it, and any unexpected slot raises a critical alert (RFC 0039 D9). The
  residual risk is the set of permissions that are already
  superuser-equivalent: exec, attach and debug on the instance pods, pod
  creation, Secret read (the CNPG `streaming_replica` certificate) and
  `Cluster` writes in `postgres-<env>`, and node access. The publisher adds
  none of them.
- **Subscription-owner credential.** `mcp_subscription_owner` keeps `LOGIN`
  and three password copies (OD15). No Butlers workload mounts them, but the
  password reaches `butlers_public` and `postgres` over TCP ([Observed] not
  `butlers`), where it can rewrite the projection and forge the applied
  digest, so its custody is recorded. The owner declined a `pg_hba` reject
  line and accepted the risk (OD16).
- **Owner step pending.** Each manifest change and each slot invalidation
  needs a superuser step. On dev every deploy runs it after the migrations
  (OD20); elsewhere it is manual. Until it runs after a manifest change the
  server refuses with `projection_apply_pending`, the warning flag is down,
  and a narrowing left pending for 24 hours turns critical; after an
  invalidation it refuses with `projection_stale`. A server shipping the
  narrowed manifest therefore never serves a publisher-side narrowing before
  the purge. A server still shipping the old manifest serves old rows only
  until the new digest replicates, which reconcile writes in the same
  transaction as the publication change, so that window is the replication
  lag, capped at 15 minutes (RFC 0039 D17).
- **Shared instance.** The projection lives on the primary's instance (OD10),
  so the network cannot separate them. Ordered `pg_hba` reject lines and
  grants are the boundary, proven by a contract test over `butlers`,
  `postgres` and `template1`. The projection shares the primary's volume, WAL,
  backups and restore drill, goes down with it, and adds public read load to
  it, bounded by a connection limit of 10 and the 2-second timeout (RFC 0039
  D3). The single-node pod range `10.42.0.0/24` covers both the public pod and
  the `butlers-<env>` pods, so roles, not addresses, separate them.
- **Shared disk.** [Observed] Dev and prod share one 1.4T disk, 90 % full with
  144G free, and the PVC sizes are not enforced. A full disk stops both
  clusters; the projection adds its tables and up to 10GB of retained slot
  WAL (about 7 % of the free space). The cap bounds retained WAL only:
  logical-decoding spill files and the WAL that the same-instance apply and
  initial copy write are outside it [Inferred]. The retained-WAL alert is the
  Butlers-side bound; host disk headroom stays the owner's (RFC 0039 D3).
- **Narrowing leaves copies.** Removing data from the manifest does not delete
  what already replicated, so the owner step truncates and re-copies
  (`public-mcp-projection` REQ-003).
- **Retention.** Terminal proposals drop their note and evidence URL after 30
  days (`public-mcp-proposals` REQ-006); audit lines carry no content.
- **Stale kill switch.** Switches replicate, so the effect lags. The 15-minute
  stale cutoff bounds it. The ConfigMap and scale-to-zero switches do not depend
  on replication.
- **WAL retention.** A stalled slot retains WAL on the primary. The slot cap
  (`max_slot_wal_keep_size = 10GB`, set on dev) and alerts at 50 % bound it.
  An invalidated slot costs a projection rebuild by the owner step, never the
  primary.
- **Alert latency.** Uptime Kuma's poll interval bounds detection latency on
  top of each rule's window; the critical monitor polls every 60 seconds. An
  unreachable Prometheus or an empty flag turns the monitor DOWN, so the alert
  path checks itself (RFC 0039 D16).
- **Sparse graph.** [Observed `bu-h40h2b.7`] There are about 7 edges over 626
  entities, so `graph_*` is correct but nearly empty until that work lands.
- **Coarse client id.** A claude.ai connector also appears in Claude Code
  sessions on the same account (D7 known limits). Audit records `cid` as a label
  only.
- **Merge churn with `bu-cadcr1`.** It is avoided by not widening
  `pending_actions.origin`. External origin lives only in fact provenance.

## Migration Plan

1. **P0.** Default-deny ingress in `butlers-<env>` lands first, with its rendered-chart
   contract test.
2. **P1.** Source migrations: replica-identity indexes, Chronicler `*_public`
   tables, the `public.entities` flag columns with their trigger, the finance
   display columns, the health public tables and the finance and health JSON
   fields with their triggers,
   `public.entity_graph_withheld_counts`, the `mcp_surface` schema and
   `relationship.external_fact_proposals`. Then the init-db REPLICATION
   exception and publisher grants, the reconcile step in the migration Job,
   and `butlers public-mcp owner-sql`. The CNPG owner acts (the
   `butlers_public` database, the managed roles, the ordered `pg_hba` lines
   and the publisher's peer mapping) are done on dev by homelab PR
   Tzeusy/homelab#40. On dev, `make deploy-dev` reruns `init-db.sql` before
   the release and runs the owner step after its migrations (OD20); the step
   pre-creates the slot and creates the subscription. Gate: the initial sync
   matches the manifest and the canaries are absent.
3. **P2.** The dark server and chart, smoke-tested from a test pod in `mcp` with a
   test keyring.
4. **P3.** Owner acts on the gateway, then the first real calls, which are
   audited.
5. **P4 and P5.** Finance and health enabled on deploy (OD3) at full breadth
   (OD17); then the inbox, importer, typed card and apply.

Every phase targets `dev` only. Prod rollout is deferred for the foreseeable
future (OD18); the plan stays written for `<env>`, so it would apply to prod
unchanged if a new owner decision lifts the deferral.

Every source migration is additive. The replica-identity change swaps the
identity index and does not touch row data.

## Rollback

- **Fastest:** disable the `all` domain (Settings or CLI), or apply the
  break-glass ConfigMap, or scale the Deployment to 0.
- **Remove the surface:** drop the `butlers` entry from `MCP_SERVERS` (owner),
  uninstall the chart, then, as the superuser through `kubectl exec` (only a
  superuser can drop a `password_required = false` subscription), run a
  plain `DROP SUBSCRIPTION`, which also drops its slot and any table-sync
  slots on the primary, then drop the publication, and confirm that no
  `butlers_public_sub` or `pg_<oid>_sync_%` slot remains. The rest is a
  homelab owner act that reverses owner act 2: remove the `butlers_public`
  database, the managed roles and the `pg_hba` lines from the CNPG values,
  since a `psql` drop of CNPG-managed objects could be reconciled back.
  Primary data is unaffected. On dev, first set `PUBLIC_MCP_OWNER_STEP_dev :=
  off` in `deploy/helm/butlers/makefile` (S9), or the next `make deploy-dev`
  recreates the slot, the subscription and the public chart; reconcile still
  recreates the publication, which is inert without a slot or subscription.
- **Schema:** the additive columns and tables can stay, inert. Reverting
  replica identity to the primary key is a plain `ALTER TABLE`.
- **Facts already applied** from proposals stay. They are owner-approved,
  `third_party`, and retractable through the normal Relationship retraction
  path. Rollback replays or deletes nothing.

## Open Questions

These are carried from RFC 0039. None of them blocks a v1-mandatory requirement
as written.

1. The knowledge predicate allowlists are written from the predicate registry
   at implementation time (task 1.4); the owner may widen them.
Closed by the owner on 2026-11-05: OD21's contact and identity line does not
apply in finance and health (OD22, RFC 0039 open question 6), so clinicians'
phone numbers and Health calendar attendee and organizer emails are published;
`public-mcp-projection` REQ-012 states the carve-out.

Closed by the owner on 2026-11-04: finance and health breadth (OD17,
everything except credentials; `public-mcp-projection` REQ-012 and -013,
`public-mcp-surface` REQ-009, -010 and -014, `public-mcp-authorization`
REQ-010), the OD17 records tools and JSON-field materialization (OD19,
accepted), the dev owner step (OD20, automatic in `make deploy-dev`;
`public-mcp-projection` REQ-003), facts about other people (OD21, published
up to the contact, identity and credential line; `public-mcp-projection`
REQ-012), prod rollout (OD18, deferred for the foreseeable future; dev is the
only target), projection placement (OD10, a second
database; `public-mcp-projection` REQ-008 states the `pg_hba` isolation
unconditionally), the alert path (OD11, Uptime Kuma polling two Prometheus
flags; `public-mcp-authorization` REQ-014, proven by forcing each flag to 0 in
task 3.2), and the publisher's credential (OD14: there is none;
`database-security` REQ-016). Closed by observation [homelab report,
2026-11-04]: prod's PostgreSQL version (PG 17.8, `wal_level=logical`, one
instance, no standbys) and whether a NetworkPolicy selects the `postgres-dev`
pods (none does; `public-mcp-projection` REQ-014). The Chronicler materialization shape (tables, not a column) is
decided by RFC 0039 D2 and stated in REQ-006.
