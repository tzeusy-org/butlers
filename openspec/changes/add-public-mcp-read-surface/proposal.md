## Why

The owner's own AI clients (ChatGPT, including its Dots agent, and claude.ai)
cannot see what Butlers knows: who someone is, recent trips, time use, spend,
health series. Today the only route is pasting context by hand. Butlers already
holds that context in structured, provenance-tagged form, and the owner's
Cloudflare MCP gateway (`mcp.tze.how`) already provides OAuth 2.1 with PKCE and
a GitHub-id allowlist. This change specifies a deterministic, read-only MCP
server that serves that context to those clients, with defense in depth past
Cloudflare, and one owner-gated write: `propose_fact`.

The design contract is RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`,
D1 to D17). The owner decisions it rests on (OD1 to OD15, recorded 2026-11-04)
are authoritative; the doctrine exception, the owner's answers to the four
items the conformance review returned (OD10 to OD13), and OD14 and OD15,
recorded after homelab PR Tzeusy/homelab#40 applied the CNPG owner acts to
`postgres-dev`, are in
`about/legends-and-lore/decisions/2026-11-04-doctrine-public-mcp-read-surface.md`,
written in this change.

## What Changes

- **New standalone server.** `butlers-public-mcp` runs three packages:
  `butlers.mcp_public` (thin FastMCP stateless-HTTP entrypoint), over
  `butlers.capability_host` (authorization, allowlist, budgets, kill switches,
  DLP, audit), over `butlers.knowledge_read` (side-effect-free reads). It is not
  a butler or a module, and it never starts an LLM session (RFC 0039 D1, D14).
- **Projection database.** The server reads only `butlers_public`, a second
  database on the existing cluster `postgres-<env>` (OD10), fed one way by
  PostgreSQL 17 logical replication from one checked-in allowlist manifest.
  Ordered `pg_hba` reject lines and grants keep its roles out of every other
  database. The migration Job owns the publication; the owner runs the slot
  and subscription work as one generated, idempotent owner step
  (`butlers public-mcp owner-sql`, OD14), and the server refuses with
  `projection_apply_pending` until that step has applied the manifest it
  ships with. The publisher has no credential: it logs in only by local peer
  authentication inside the instance pods (OD14).
  Credentials, `butler_secrets`, `entity_info` and connector tokens are never
  published, no JSONB column is ever published, and a contract test asserts
  that the publication equals the manifest. Fields held only in JSONB (entity
  flags, finance overlays, health values) are published through
  trigger-maintained plain columns or tables (D2, D3, D4, D9).
- **Authorization past Cloudflare.** A filter-proxy hop bearer, plus a
  Worker-signed Ed25519 assertion on every `tools/call`, bound to user, client,
  method, tool, query string, body digest and a single-use `jti`, verified at
  home with a 30-second lifetime (D6, D7).
- **Network placement.** A dedicated default-deny namespace. Ingress comes only
  from the `butlers` filter-proxy release; egress goes only to the PostgreSQL
  port of `postgres-<env>` and to DNS. That instance also serves the primary,
  so `pg_hba` and grants, not the network, separate the two (D3, D8).
- **17 tools on one server**, finance and health included, each in a
  kill-switch domain: `search` and `fetch` in the exact ChatGPT contract,
  `search_filtered`, `entity_lookup`, `entity_profile`, `graph_neighbors`,
  `graph_path`, `timeline`, `time_use`, `trips`, `describe`, `finance_summary`,
  `finance_ledger`, `finance_obligations`, `health_series`, `health_profile` and
  `propose_fact` (D5, D10, D11).
- **Response safety.** A DLP scan, canaries, per-tool budgets, a structured audit
  line per call, fixed error codes and refusal when the projection is more than
  15 minutes stale (D15, D16, D17). Alerts are Prometheus rules collapsed into
  two flag series that the owner's Uptime Kuma polls (OD11, D16).
- **`propose_fact`.** The proposal goes into an inbox. A deterministic importer
  in the Relationship daemon parks it as `relationship_assert_fact` on the
  approvals page, and the owner approves or rejects it. Applying it is
  deterministic and keeps `content_authority = 'third_party'` and
  `src = 'external_mcp:<proposal_id>'` (D13).
- **Kill switches.** One per domain plus `all`. The owner flips them from a new
  Settings panel, a CLI command or a break-glass ConfigMap (D16).
- **Precondition.** Default-deny ingress in `butlers-<env>`, so no gateway allow
  rule can ever reach the unauthenticated butler daemons (D8 precondition).

## Capabilities

### New Capabilities

- `public-mcp-surface`: the tool contract, including the ChatGPT `search`/`fetch`
  shapes, source envelopes and freshness, pure reads, bounds and cursors, the
  error shape, and the no-LLM guarantee.
- `public-mcp-authorization`: hop bearer, request limits, Worker assertion
  verification, keyring rotation, namespace placement, kill switches, DLP,
  canaries, budgets, audit and alerting.
- `public-mcp-projection`: the allowlist manifest, generated publication and
  subscriber DDL, drift checks, credential absence, restriction-as-delete,
  Chronicler corrected visibility, the entity merge pointer, isolation from the
  primary, projection roles, freshness source, slot safety, the owner step and
  the fail-closed applied digest.
- `public-mcp-proposals`: `propose_fact` receipt, idempotency, the inbox cap,
  deterministic import and validation, proposal states, visibility and
  retention, the proposals kill switch, and the v1 rule that only the owner's
  per-event approval applies a proposal.

### Modified Capabilities

- `module-approvals`: ADDED REQ-module-approvals-010. External proposals park
  through a deterministic path that sends no push, matches no standing rule
  and never feeds standing-rule promotion.
- `dashboard-approvals`: ADDED REQ-dashboard-approvals-904, a typed review card
  for external fact proposals.
- `relationship-facts`: ADDED REQ-relationship-facts-011. An approved external
  proposal writes a verified fact that keeps third-party authority and external
  provenance.
- `deployment-hardening`: ADDED REQ-deployment-hardening-013, default-deny
  ingress for the Butlers namespace.
- `database-security`: ADDED REQ-database-security-016, covering the single
  Butlers-managed REPLICATION exception, which has no credential and logs in
  only by local peer authentication (OD14), the subscription owner's kept
  credential, which no Butlers workload uses (OD15), and the `mcp_surface`
  control schema on the primary.
- `dashboard-settings-console`: MODIFIED Settings Console Page
  (REQ-dashboard-settings-console-002), adding a Public MCP panel to the grid;
  ADDED REQ-dashboard-settings-console-001, the `/settings/public-mcp` page
  with per-domain kill switches.

### Coordinated outside this change

- **RFC 0038 amendment.** RFC 0039 amends D5, D6, D7 and D13. Its spec text goes
  in place into `generalist-lead-focused-delegation/specs/capability-authorization/spec.md`
  as REQ-capability-authorization-022 and -023, and it must land before
  `bu-2ckast` archives that change. It is deliberately not a second delta here:
  a second active writer of `capability-authorization` would conflict.

## Impact

- **Boundary classification.** This change adds external ingress, a new security
  boundary, new database roles, a REPLICATION role, a new shared-schema column on
  `public.entities` and a new approval producer. `design.md` is required.
- **Code.** New packages `src/butlers/knowledge_read/`, `src/butlers/capability_host/`
  and `src/butlers/mcp_public/`; a `butlers public-mcp` CLI group (`reconcile`,
  `owner-sql`, `disable`, `enable`); publisher grants and the `mcp_public_ro`
  `statement_timeout` in `scripts/init-db.sql`; a Relationship job `public_mcp_proposal_import`; a
  dashboard-api owner route and Settings panel for kill switches; a typed
  approvals review for `relationship_assert_fact` proposals of external origin.
- **Schema.** Replica-identity indexes on row-filtered source tables (through
  `butlers.migrations.projection.ensure_replica_identity`), Chronicler
  `episodes_public` and `rollups_public`, the `public.entities` flag columns
  (`merged_into`, `deleted`, `unidentified`), the finance `display_merchant`
  and `display_category` columns, the health `*_public` tables,
  `public.entity_graph_withheld_counts`, each trigger-maintained with a parity
  test, the `mcp_surface` schema, `relationship.external_fact_proposals`, and
  projection-side `mcp_read` views, `mcp_inbox.proposals` and the
  subscriber-local `mcp_local.manifest_applied`.
- **Deploy.** A new chart `deploy/helm/butlers-public-mcp`, which creates
  namespace `butlers-public-<env>` and an ExternalSecret reading
  `MCP_PUBLIC_RO_DB_PASSWORD`; NetworkPolicies in `butlers-public-<env>` and
  `butlers-<env>`; a reconcile step in the migration Job for the publication
  and the replicated digest only; the owner step, which pre-creates the
  replication slot and creates the subscription with `create_slot = false`
  (same-instance replication, D3); the alert rules and the two flag recording
  rules.
- **Dependencies.** None new: `cryptography`, `rfc8785` and `fastmcp` are
  already present. The assertion verifier reuses the
  `butlers.core.runtime_probe_control` codec.
- **Owner acts (homelab, OD5).** Done on dev by homelab PR
  Tzeusy/homelab#40: on `postgres-<env>`, the `butlers_public` database and
  the managed roles (`mcp_public_ro` with connection limit 10, the publisher
  with no password, `mcp_subscription_owner`), the ordered `pg_hba` lines on
  `10.42.0.0/24` with three reject lines, the `local` `pg_ident` line for the
  publisher, `max_slot_wal_keep_size = 10GB`, and the password Secrets; no
  `postgres-<env>` NetworkPolicy, because none selects the instance pods.
  The namespace is now the chart's. Still open: rerunning `init-db.sql` with
  the publisher grants and `statement_timeout`; the owner step at first sync
  and then once per manifest change and per slot invalidation (the one recurring
  owner act); the Worker signing key and its deploy; the `mcp` filter-proxy
  release with the dev ExternalSecret; the `MCP_SERVERS` entry; two Uptime
  Kuma `json-query` monitors on the flags (a precondition of
  `public-mcp-authorization` REQ-014); client connection and consent. They do
  not block Butlers-side work.
- **Owner answers (OD10 to OD15).** The four items the conformance review
  returned are decided: the projection is a second database on the existing
  cluster, alerts reach the owner through Uptime Kuma, live data in `dev` from
  the first connection is accepted, and the upkeep of the trigger-maintained
  materializations (`design.md` S1 and S7) is accepted. OD14 adopts the
  no-credential publisher and the single owner step, with the server failing
  closed while it is pending; OD15 keeps `mcp_subscription_owner` as created.
- **No effect on the live system's availability.** No Butlers daemon depends on
  the public server, the projection or the gateway. The projection shares the
  primary's instance, volume, WAL and backups, so it goes down with the primary,
  and the restore drill must tolerate or drop it (RFC 0039 D3). The disk under
  dev and prod is shared and 90 % full [Observed]; the projection adds its
  tables and up to 10GB of slot WAL to that shared risk.

## Out of Scope

- **Excel** and any client other than ChatGPT (including Dots) and claude.ai (OD7).
- **Any write other than `propose_fact`.** That means no capture, note,
  memory-fact, entity-creation, domain-mutation, send or spend tool, and no
  write to Obsidian (the vault already has its own server).
- **Enabling automatic (LLM) approval of proposals.** Any later automatic
  approval must first have its own recorded owner decision and must run as a
  bounded offline batch, never on the MCP request path. It is blocked by
  `bu-u5euwb.3` and `bu-u5s4w8`. This change specifies only that nothing but
  the owner's per-event approval applies a proposal.
- **Interactions and `relationship`-schema memory facts** in the projection.
  They hold contact details and email-derived summaries; v1 does not publish
  them, so `entity_profile` has no interaction section.
- **Separate tier servers** (`butlers-finance`, `butlers-health`), per-client
  tiers, and any manual, time-boxed or passkey unlock or elevation step (OD3).
- **An LLM, sampling, elicitation or a butler fronting any request** (OD8), and
  generic `query`, `sql` or `invoke` tools.
- **Semantic or embedding search**, and any model in the public pod (D11 deferred
  alternative).
- **Views on the primary database**, any login from the public pod to the
  primary database, and any route from it to butler daemons, dashboard-api or
  the internet.
- **A separate CNPG cluster for the projection** (rejected by the owner in
  favour of lower upkeep, OD10) and **a new Alertmanager receiver** (OD11).
- **More than one replica.** The replay set is in memory, so v1 runs
  `replicas: 1`.
- **Prod rollout.** v1 targets `dev`, which is the live system. Prod waits for a
  separate owner act.
- **Successor work, filed blocked:** taint and S1 `external` principal labels on
  applied facts (`bu-pkuny2`, `bu-dhloi1.6`); a proposals card that conforms to
  the D19 dossier (`bu-vvsfi8`).
- **Widening `pending_actions.origin`** beyond `NULL | 'prepared'`.

## Owner Approval

The owner recorded decisions OD1 to OD9 on 2026-11-04 and accepted the doctrine
change (OD2), then answered the four items the conformance review returned
(OD10 to OD13) the same day, and recorded OD14 and OD15 after the homelab
apply. One item beyond OD15 (a `pg_hba` reject line for
`mcp_subscription_owner`, RFC 0039 open question 5) returns to the owner. Under the owner's standing rule, text that
conforms to those decisions is adopted once the PR's independent conformance
review passes; text that goes beyond them returns to the owner. The spec-level choices in
`design.md` § Spec-level decisions are listed for that review. Implementation is
a downstream handoff, not part of this specification PR.
