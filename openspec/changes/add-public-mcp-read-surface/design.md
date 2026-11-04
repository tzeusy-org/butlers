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
| D2 | One publication; replica-identity filters, so a restriction arrives as a DELETE; no JSONB published; source-owned materializations (Chronicler, entity flags, finance overlays, health values) | `public-mcp-projection` REQ-001, -002, -005, -006, -007, -013 |
| D3 | Projection placement (OD10): a second database on `postgres-<env>`, isolated by ordered `pg_hba` reject lines and grants (roles, not the shared `10.42.0.0/24` range, separate the pods); same-instance subscription with a pre-created slot, created by the owner step (OD14); shared fate with the primary, including the shared, 90 % full disk | `public-mcp-projection` REQ-003, -008, -011, -015; `public-mcp-authorization` REQ-008 |
| D4 | One manifest; generated DDL; reconcile (publication and replicated digest) split from the owner step (slot, subscription, purge, applied digest); five drift checks; narrowing purges; v1 exposure defaults with domains per table | `public-mcp-projection` REQ-001 to -004, -012, -015 |
| D5 | One server for every domain; domains attach to data; static `tools/list`; no tiers or unlock | `public-mcp-authorization` REQ-009 |
| D6 | Hop bearer, digest-only, constant-time | `public-mcp-authorization` REQ-001 |
| D7 | Assertion format, verification order, keyring, replay set, single replica | `public-mcp-authorization` REQ-002 to -007 |
| D8 | Namespace `butlers-public-<env>`, default-deny; one allow to the `postgres-<env>` instance pods, widening nothing else; live policy check; the precondition in `butlers-<env>` | `public-mcp-authorization` REQ-008; `public-mcp-projection` REQ-014; `deployment-hardening` REQ-013 |
| D9 | Roles on both databases; one Butlers-managed REPLICATION role, primary-read-all and with no credential (local peer login only, OD14); the subscription owner, kept as created (OD15); `mcp_surface` schema; slot safety | `public-mcp-projection` REQ-009, -010, -011; `database-security` REQ-016 |
| D10 | The 17-tool contract and the ChatGPT compatibility shapes | `public-mcp-surface` REQ-002 to -010, -012 |
| D11 | Lexical search in v1 | `public-mcp-surface` REQ-003, -006 |
| D12 | Reads never write | `public-mcp-surface` REQ-011 |
| D13 | `propose_fact` pipeline and states | `public-mcp-proposals` (all); `module-approvals` REQ-010; `dashboard-approvals` REQ-904; `relationship-facts` REQ-011 |
| D14 | Structural no-LLM guarantees | `public-mcp-surface` REQ-001 |
| D15 | DLP, canaries, audit, budgets | `public-mcp-authorization` REQ-010 to -013 |
| D16 | Kill-switch layers, leak runbook, and alerting as Prometheus rules collapsed into two flags that Uptime Kuma polls (OD11), including the owner-step pending warning and the 24-hour narrowing critical | `public-mcp-authorization` REQ-009, -014; `public-mcp-proposals` REQ-007; `dashboard-settings-console` REQ-001 and MODIFIED Settings Console Page (REQ-002) |
| D17 | Freshness thresholds, the fail-closed applied-digest check, error codes (including `projection_apply_pending`) and precedence, failure table | `public-mcp-surface` REQ-005, -013; `public-mcp-projection` REQ-010, -015 |

The RFC 0038 amendment (D5, D6, D7 and D13, amended in place) is not specified
here. It goes into `generalist-lead-focused-delegation`. See `proposal.md` §
Coordinated outside this change.

## Spec-level decisions

These choices were needed to write observable requirements. RFC 0039 does not
make them, or leaves them to "the spec". Each is conservative and reversible,
and the conformance review checks them against OD1 to OD15.

### S1. Entity flags become published columns on `public.entities`

[Observed] Today three entity flags are JSONB keys in `public.entities.metadata`:
the merge pointer `merged_into`, the soft-delete stamp `deleted_at`
(`alembic/versions/core/core_002_identity.py:124-127`) and the
unidentified-sender marker `unidentified` (`src/butlers/identity.py:459`). An
unidentified sender's entity is named `Unknown (<channel> <value>)`, which
embeds the raw email address or phone number (`src/butlers/identity.py:1067-1071`).
`metadata` is never published (D4), and PG17 does not publish generated columns
(D2 rule 6). A core migration therefore adds three plain columns,
`merged_into UUID NULL`, `deleted BOOLEAN NOT NULL` and
`unidentified BOOLEAN NOT NULL`, and a `BEFORE INSERT OR UPDATE` trigger keeps
them equal to the JSONB keys. A contract test proves parity. Existing readers
keep reading the JSONB keys; moving them is out of scope. The manifest
publishes `merged_into` and filters entities on `deleted = false AND
unidentified = false`, with both flags in the replica identity, so deleting an
entity or creating an unknown sender never publishes it. `entity_lookup`
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
JSONB key, and `metadata` is denylisted (D4 check 3).

Two designs were weighed:

- *(a) Trigger-synced plain columns on the source table*, as S1 does for
  entities. Chosen for finance: a `BEFORE INSERT OR UPDATE` trigger keeps
  `display_merchant` and `display_category` equal to the overlay expressions.
  `finance.accounts` is published as `id, institution, type` only, and the
  `mcp_read` view derives `account_label` from them.
- *(b) Source-owned public tables*, as Chronicler does. Chosen for health,
  because the source is the shared memory `facts` table, where adding
  domain-specific columns would diverge from the memory module's schema. An
  `AFTER INSERT OR UPDATE OR DELETE` trigger on `health.facts` maintains
  `health.measurements_public`, `health.medications_public` and
  `health.conditions_public`, copying only allowlisted keys and never `notes`.

Both use database triggers rather than writer code, so every write path is
covered without a sweep. The graph withheld-count table
(`public.entity_graph_withheld_counts`) follows pattern (b) for the same
reason: non-normal live edges carry their payload, so they cannot be
published even as stubs. **Upkeep**: one trigger function per source (four in
total with S1), each with a parity test that fails in the PR of any migration
that renames a key it reads. This is upkeep beyond the single manifest (OD1).
The owner accepted it (OD13), with the parity tests and the CI drift checks as
its mitigation.

The same analysis put two more sources out of v1 rather than adding
materializations: interactions, which now live as `relationship` memory facts
(the `relationship.interactions` table was dropped, `roster/relationship/migrations/010`),
and memory facts of the `relationship` schema in general, which hold contact
details and email-derived summaries. `entity_profile` therefore offers no
interaction section in v1.

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
  widening left pending costs only availability and stays a warning.

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

## Risks / Trade-offs

- **Labels written by LLMs.** Sensitivity labels come from LLM writers
  ([Observed] `store_fact` defaults to `normal`). So the manifest exposes memory
  facts only through literal per-schema predicate allowlists and literal column
  lists, drops the `relationship` memory schema, and the DLP scan is
  load-bearing, not cosmetic. Canaries detect a manifest mistake in CI and in
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
  needs a manual superuser step. Until it runs after a manifest change the
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
   display columns and the health public tables with their triggers,
   `public.entity_graph_withheld_counts`, the `mcp_surface` schema and
   `relationship.external_fact_proposals`. Then the init-db REPLICATION
   exception and publisher grants, the reconcile step in the migration Job,
   and `butlers public-mcp owner-sql`. The CNPG owner acts (the
   `butlers_public` database, the managed roles, the ordered `pg_hba` lines
   and the publisher's peer mapping) are done on dev by homelab PR
   Tzeusy/homelab#40. The owner reruns `init-db.sql`, then runs the owner
   step, which pre-creates the slot and creates the subscription. Gate: the
   initial sync matches the manifest and the canaries are absent.
3. **P2.** The dark server and chart, smoke-tested from a test pod in `mcp` with a
   test keyring.
4. **P3.** Owner acts on the gateway, then the first real calls, which are
   audited.
5. **P4 and P5.** Finance and health enabled on deploy (OD3); then the inbox,
   importer, typed card and apply.

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
  Primary data is unaffected.
- **Schema:** the additive columns and tables can stay, inert. Reverting
  replica identity to the primary key is a plain `ALTER TABLE`.
- **Facts already applied** from proposals stay. They are owner-approved,
  `third_party`, and retractable through the normal Relationship retraction
  path. Rollback replays or deletes nothing.

## Open Questions

These are carried from RFC 0039. None of them blocks a v1-mandatory requirement
as written.

1. Finance and health breadth: REQ-012 fixes the conservative v1 default.
   Widening it is a manifest edit and a spec amendment.
2. Prod rollout. v1 targets dev only; prod waits for a separate owner act.
3. The knowledge predicate allowlists are written from the predicate registry
   at implementation time (task 1.4); the owner may widen them.

Closed by the owner on 2026-11-04: projection placement (OD10, a second
database; `public-mcp-projection` REQ-008 states the `pg_hba` isolation
unconditionally), the alert path (OD11, Uptime Kuma polling two Prometheus
flags; `public-mcp-authorization` REQ-014, proven by forcing each flag to 0 in
task 3.2), and the publisher's credential (OD14: there is none;
`database-security` REQ-016). Closed by observation [homelab report,
2026-11-04]: prod's PostgreSQL version (PG 17.8, `wal_level=logical`, one
instance, no standbys) and whether a NetworkPolicy selects the `postgres-dev`
pods (none does; `public-mcp-projection` REQ-014). The Chronicler materialization shape (tables, not a column) is
decided by RFC 0039 D2 and stated in REQ-006.
