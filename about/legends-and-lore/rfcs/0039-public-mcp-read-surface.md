# RFC 0039: Public MCP Read Surface

**Status:** Accepted (2026-11-04) on the owner decisions recorded in
[Owner decisions](#owner-decisions) (OD1 to OD15). Under the owner's standing
rule, text that conforms to those decisions is adopted once the PR's independent
conformance review passes; the owner does not sign off exact wording. Text that
goes beyond OD1 to OD15 returns to the owner. The conformance review returned
four items beyond OD1 to OD9, and the owner answered all four on 2026-11-04:
the projection is a second database on the existing cluster (OD10, D3), alerts
reach the owner through the existing Uptime Kuma (OD11, D16), live data in
`dev` from the first connection is accepted (OD12, owner act 8), and the upkeep
of the source-owned materializations in D2 is accepted (OD13). After homelab
PR Tzeusy/homelab#40 applied the CNPG owner acts to `postgres-dev`, the owner
also adopted a publisher with no credential, whose slot and subscription work
is one owner step (OD14, D3, D9), and kept `mcp_subscription_owner` as created
(OD15, D9). The
[decision record](../decisions/2026-11-04-doctrine-public-mcp-read-surface.md)
records them. Implementation has not started.
**Author:** Claude (th-projects project-feature-request run), for the owner (tze)
**Date:** 2026-11-04
**Number:** provisional; `bu-bda935` (post-switch corpus rewrite) may renumber it.
**Doctrine exception:** a third named exception to vision.md Rule 3 and to the
architecture.md exception mechanism. It covers one deterministic, internet-reachable,
read-only MCP server for the owner's own AI clients. The server reads only a
one-way logical-replication projection database. No credential is reachable,
no LLM runs on the request path, writes are proposals the owner gates, and the
owner holds kill switches. The exception does not generalize. See
[Doctrine relationship](#doctrine-relationship).
**Decision record:** [`decisions/2026-11-04-doctrine-public-mcp-read-surface.md`](../decisions/2026-11-04-doctrine-public-mcp-read-surface.md)
**OpenSpec change:** `add-public-mcp-read-surface` (capabilities
`public-mcp-surface`, `public-mcp-authorization`, `public-mcp-projection`,
`public-mcp-proposals`)
**Amends:** RFC 0038 D5, D6, D7 and D13, in place in `generalist-lead-focused-delegation`
(see [Relation to other RFCs](#relation-to-other-rfcs)).

## Summary

The owner's own AI clients (ChatGPT, including its Dots agent, and claude.ai)
get read access to Butlers data over MCP, through the owner's existing
Cloudflare gateway at `mcp.tze.how`. The rules of the surface:

- **A separate deterministic server.** `butlers.mcp_public` (thin FastMCP
  stateless-HTTP entrypoint) over `butlers.capability_host` (auth,
  allowlist, budgets, DLP, audit) over `butlers.knowledge_read`
  (side-effect-free reads). Not a butler, not a module; it never starts an
  LLM session.
- **Reads only a projection database**, fed one way by PostgreSQL 17 logical
  replication from one checked-in allowlist manifest. Credentials,
  `butler_secrets`, secured `entity_info` rows and connector tokens are never
  published; a contract test asserts publication equals manifest.
- **Defense in depth past Cloudflare.** Every `tools/call` carries a per-call
  Ed25519 assertion signed by the Worker and verified at home, plus the
  filter-proxy hop bearer. The pod runs in a default-deny namespace with
  ingress only from that filter-proxy release and egress only to the
  PostgreSQL port of the cluster that holds the projection, where `pg_hba`
  admits its role to the projection database alone; that role is read-only
  except INSERT on a proposals inbox.
- **One server for all domains**, finance and health included (OD3), with
  per-domain kill switches for incidents and no unlock step.
- **The only write is `propose_fact`.** A triple lands in an inbox; a
  deterministic importer parks it on the dashboard approvals page; only the
  owner's approval applies it, through deterministic code, with
  `content_authority=third_party` and origin `external_mcp` in the fact's
  provenance.

Nothing on this surface spends Butlers model budget: the clients' own
subscriptions do the reasoning, and no request reaches a Butlers model.

## Motivation

### What each client gains

| Client | Main use | Tools it leans on |
|---|---|---|
| ChatGPT, including Dots (OD6: Pro and higher tiers can use custom MCP) | Deep-research and agent tasks with the owner's context: who someone is, recent trips and taste, what happened last week | `search` and `fetch` in the exact ChatGPT contract, `trips`, `timeline` |
| claude.ai (and Claude Code sessions that inherit the connector) | Cross-domain synthesis: sleep against spend against who the owner saw, meeting and relationship prep, retrospectives from Chronicler aggregates | typed tools: `entity_profile`, `graph_*`, `time_use`, `finance_*`, `health_series` |
| Excel | Deferred (OD7) | — |

[Inferred] Today the only way to get this context into those clients is to
paste it in. Butlers already holds it in structured, provenance-tagged form,
ingested continuously by about a dozen connectors. Both clients do the
reasoning on the owner's subscription, so Butlers spends nothing on models and
adds no session load.

### What Obsidian already covers

[Observed: homelab gateway config] The owner's Obsidian vault is already served
through the same gateway, with read, write and delete tools. Long-form notes,
shared drafts and anything a ChatGPT agent wants to write as prose belong
there. This surface does not duplicate vault writing. It adds what the vault
cannot provide: entity resolution, the relationship graph, the money ledger,
health series, time use and travel, each with provenance and a freshness
stamp. The only write path, `propose_fact`, exists because a structured fact
written as Obsidian prose would never reach the registry.

**Why now.** [Observed] The gateway already has OAuth 2.1 with PKCE, a
GitHub-id allowlist, per-server audiences and Worker-set identity headers,
and dev Postgres (CNPG `postgres-dev`, PG 17.8) already runs with
`wal_level=logical`. Homelab PR Tzeusy/homelab#40 has applied the CNPG side of
the owner acts to `postgres-dev` [Observed: homelab report, 2026-11-04]. What
remains is Butlers-side work plus a short list of owner acts
([Rollout](#rollout-and-owner-acts)).

## Vocabulary

| Term | Meaning |
|---|---|
| Public server | The `butlers-public-mcp` Deployment that runs `butlers.mcp_public`. |
| Gateway | The owner's Cloudflare Worker (OAuth, allowlist, assertion signing), then `cloudflared`, `traefik-mcp`, then the `butlers` filter-proxy release in namespace `mcp`. |
| Primary | The live Butlers database (`butlers` on CNPG `postgres-<env>`). |
| Projection | The `butlers_public` database, fed only by logical replication from the primary. It also holds the proposals inbox. It is a second database on the primary's CNPG cluster `postgres-<env>` (D3, OD10). |
| Manifest | `src/butlers/knowledge_read/projection.toml`: the one checked-in list of published tables, columns, row filters, domains and tool sources. |
| Assertion | The Worker's Ed25519 signature over one forwarded MCP request (D7). It is unrelated to the RFC 0038 broker assertion and never accepted in its place. |
| Hop bearer | `UPSTREAM_BEARER`, which filter-proxy injects into every forwarded request (D6). |
| Domain | A kill-switch unit: `knowledge`, `relationship`, `time`, `travel`, `finance`, `health`, `proposals`. |
| Proposal | One `propose_fact` call. It is stored in the inbox and has no effect until the owner approves it. |
| Owner step | The one idempotent SQL file that `butlers public-mcp owner-sql` emits for a manifest, which the owner runs as the CNPG superuser through `kubectl exec`. It creates the slot and the subscription, converges the subscriber side and records the applied digest (D3, OD14). |
| Applied digest | The manifest digest the last complete owner step recorded in `butlers_public`. The public server serves only while it equals the digest the server ships with (D17). |
| Publisher | `butlers_projection_publisher`, the `REPLICATION` role the subscription connects as. It has no password or certificate and logs in only by local peer authentication inside the instance pods (D9, OD14). |
| Migration Job | The step that applies Butlers migrations as the migration user, today the `dashboard-api` `migrations` initContainer that runs `db migrate` (`deploy/helm/butlers/templates/dashboard-api.yaml`). `butlers public-mcp reconcile` runs in it after Alembic (D4). OD14 calls it the migration Job; it is not a separate Kubernetes Job. |

## Topology

```mermaid
flowchart LR
    C["ChatGPT / Dots, claude.ai"] --> W["Cloudflare Worker (owner edge):<br/>OAuth 2.1+PKCE, GitHub-id allowlist,<br/>strips x-mcp-*, signs X-MCP-Assertion"]
    subgraph Gw["k3s: mcp-gateway / mcp"]
        CFD["cloudflared-mcp"] --> TR["traefik-mcp<br/>(watches ns mcp only)"]
        TR --> FP["filter-proxy release 'butlers'<br/>injects hop bearer; FIXES off"]
    end
    MCP["ns butlers-public-&lt;env&gt; (default-deny):<br/>butlers-public-mcp"]
    subgraph PG["ns postgres-&lt;env&gt;: CNPG cluster postgres-&lt;env&gt;"]
        PRI[("database 'butlers' (primary)<br/>publication butlers_public_v1")]
        PROJ[("database butlers_public (projection)<br/>mirrored tables, read views, inbox")]
    end
    subgraph Main["ns butlers-&lt;env&gt;"]
        REL["butlers-up pod (Relationship)<br/>job: public_mcp_proposal_import"]
        APR["dashboard approvals<br/>(passkey owner session)"]
    end
    W -->|"VPC binding"| CFD
    FP -->|"only allowed ingress"| MCP
    MCP -->|"only allowed egress: 5432;<br/>pg_hba admits butlers_public only"| PROJ
    PRI -->|"logical replication, one way,<br/>same instance, publisher by local peer auth"| PROJ
    OWN["owner step (OD14): owner-sql file,<br/>kubectl exec psql -U postgres"] -->|"slot, publisher grants"| PRI
    OWN -->|"subscription, purge,<br/>applied digest"| PROJ
    REL -->|"pull inbox (SELECT, DELETE)"| PROJ
    REL -->|"park relationship_assert_fact"| APR
```

The public server has no login to the primary database and no route to butler
daemons, dashboard-api, the Switchboard, any model provider or the internet.
Its one network path is the PostgreSQL port of `postgres-<env>`, the instance
that also serves the primary, so the boundary between projection and primary
is `pg_hba` and grants, not the network (D3). The only paths from the outside
into Butlers state are the replicated read data and the inbox, which the
importer pulls on Butlers' own schedule.

## Doctrine relationship

### Lines this RFC amends

The doctrine sweep edits these lines in place and keeps rule numbers stable.
The conditions under the table are part of the exception. If any condition
stops holding, the exception lapses.

| File and line | Current text (abridged) | Amendment |
|---|---|---|
| `vision.md:76-84` Rule 3 | "Cross-domain capability access is MCP-only through the control plane. Switchboard remains the broker…" | Remove the sentence "These two named carve-outs do not create a general class of on-demand exceptions" from the RFC 0030 paragraph. Add a third named exception paragraph after the identity-projection paragraph. It names this surface, points to RFC 0039 and the decision record, restates the four conditions and the lapse clause, and ends "These three named carve-outs do not create a general class of on-demand exceptions." |
| `vision.md:65-68` Rule 1 | "There is no shared infrastructure." | Clarify that owner-operated edge infrastructure in the auth path (the owner's Cloudflare account and the GitHub identity used as an allowlist key) is not tenancy or shared infrastructure. Nothing in it is multi-user. |
| `vision.md:52-54` | "not a platform for third-party developers" | Unchanged. The RFC states that the surface is not a developer API: one owner, two named client families, no public registration. |
| `architecture.md:39-40` | "Client-to-butler: … through their MCP endpoints." | Note that the public server is a client of the projection database, not of butler endpoints. |
| `architecture.md:58-67` exception mechanism | "…read-only at the database level, batch-oriented… Write operations and interactive queries must always go through the Switchboard." | Add the RFC 0039 departure. Reads are interactive, but they hit a replicated copy, not the live store. The single write is an inbox INSERT that only the owner can turn into an effect. Same shape as RFC 0030's "Departure from RFC 0010's Batch criterion". |
| `architecture.md:419-420` anti-pattern | "Running multiple PostgreSQL instances for isolation that schema separation already provides" | Add the boundary: schema separation does not isolate against a compromise of an internet-facing process; a separate database behind `pg_hba` does (D3). |
| `security.md:30-46` partially trusted | Covers LLM API providers and sessions only. | Add "Owner-operated edge and the owner's external AI clients". Cloudflare terminates TLS and sees response payloads. ChatGPT and claude.ai are models: they get only published projection data, and anything they send back is third-party content. |
| `security.md:47-54` untrusted senders | "…never becomes steering memory… requires owner authority… or explicit owner endorsement." | Unchanged. An applied proposal is owner-endorsed but keeps `content_authority=third_party`, and it never becomes a rule or an owner-profile fact (D13). |
| `security.md:162` | "Cross-butler data access goes through MCP and the Switchboard, not direct SQL." | Name the replication role and the projection as the one guarded exception (D2, D9). State that a `REPLICATION` role can decode every change in the database regardless of grants, so the publisher is primary-read-all. It has no credential (OD14): it logs in only by local peer authentication inside the instance pods, and no role is a member of it. The permissions that reach primary-read-all (exec, attach and debug on the instance pods, pod creation, Secret read and `Cluster` writes in `postgres-<env>`, node access) are superuser-equivalent already, and the publisher adds none (D9). |
| `security.md:184-198` approval gates | The exact stored call executes; timeouts deny. | Unchanged, and applied to proposals (D13). |
| `security.md:404-410` deployment principles | "Bind to localhost… Tailscale serve handles external HTTPS exposure." | Add a Kubernetes principle: every Butlers namespace default-denies ingress; the only internet-reachable Butlers workload is the public server, reached only through the gateway. |
| `v1.md:3-4` | "If a capability is not named here, it waits." | Name the public read surface and `propose_fact` in the v1 capabilities list. Name the auto-approval batch as not v1. |
| RFC 0008 `:149`, `:178` | "no service needs to bind to anything other than localhost" | Add a Kubernetes-ingress amendment: on k3s the binding rule is met by default-deny NetworkPolicy, and the only internet-reachable workload is this RFC's server, through the gateway. |
| RFC 0010 `:137` MUST NOT item 3 | "two named on-demand carve-outs exist … both are recorded in vision.md non-negotiable 3" | "three named on-demand carve-outs (the QA-specific amendment, RFC 0030's system-plane views, and RFC 0039's public read surface)". |
| `security.md` Public Read Surface (new) | (none) | Add the guarantees, the layers past Cloudflare, and the residual risk that a client model steered by content it reads can read any enabled domain and leak it through its own tools (D15). |

**The four conditions (OD2), which the exception depends on:**

1. **No credential is reachable.** The projection never contains a
   credential, and the public server holds none for the primary (D2, D4, D9).
2. **No LLM is on the request path.** The structural guarantees are in D14.
3. **Writes are owner-gated.** `propose_fact` writes only the inbox. Each
   proposal becomes an effect only through the owner's own approval (D13).
4. **The owner holds kill switches.** There is one per domain, plus a
   whole-surface switch, and each takes effect without a deploy (D16).

### Inbound versus outbound: the bu-mzvel0 rule

The recorded owner answer on `bu-mzvel0`, carried into `bu-krc9su`, says:
"No outside server ever gets a write, send or spend effect." [Observed] That
rule covers the **inbound** direction, where Butlers is the MCP *client* of
outside servers admitted as data sources. This RFC covers the **outbound**
direction, where Butlers is the *server* and the owner's outside AI clients
call it. The literal rule does not apply, but its principle does: no outside
system gets a write effect the owner has not reviewed. `propose_fact` keeps
to that principle for three reasons:

- The inbox INSERT writes no RFC 0038 D6 store, no domain table, no goal and
  no schedule. It is not an effect in the D4 sense. It is an untrusted
  message held in quarantine, like an inbound email that has not been read.
- The only effect is the owner's approval tap: an owner-origin `record` of
  the exact stored call, executed deterministically. That mirrors D5's
  `third_party` park-only grant ("can only produce a parked effect awaiting
  per-event owner approval").
- The importer refuses any proposal that would touch identity channels,
  overrides, owner-profile facts or private custody, before it parks
  anything (D13).

A strict reader of RFC 0038 D4 might classify "propose an insight" as
`record`. The doctrine text must therefore say plainly that an inbox row is
not an effect. The future auto-approval batch *would* cross the principle,
because outside-authored content would become a write without a per-event
tap. That is why it needs its own owner decision (D13).

## Owner decisions

The owner recorded these on 2026-11-04. They are authoritative for this RFC.
OD10 to OD13 answer the four items the conformance review returned to the
owner. OD14 and OD15 followed the homelab apply of the CNPG owner acts to
`postgres-dev` (homelab PR Tzeusy/homelab#40).

| | Decision |
|---|---|
| OD1 | A projection database, not views: logical replication from one manifest. Keep upkeep low (one manifest, generated DDL, CI drift checks). A deterministic exporter is only the fallback for sources that cannot be row-filtered. |
| OD2 | The doctrine change is accepted: integration with outside products is worth the change of positioning. |
| OD3 | Finance and health are on the same single server and process. No tier servers, no time-boxed or manual unlock, no passkey elevation. Defense in depth beyond Cloudflare is required: assertion, bearer, default-deny namespace with projection-only egress, read-only role, no replicated credentials, DLP and canaries, audit, budgets, and per-domain kill switches (for incidents, not routine gating). |
| OD4 | Generalized read-only tools plus an explicit `propose_fact`, approved by hand on the dashboard approvals page and applied by deterministic code. Automatic LLM approval may come later as a bounded offline batch, never on the request path, after its own owner decision. |
| OD5 | Gateway-side setup is the owner's job. It does not block Butlers-side work. |
| OD6 | ChatGPT Dots can use custom MCP servers. The ChatGPT `search`/`fetch` contract is v1. |
| OD7 | Excel is deferred. Assume MCP is enabled in claude.ai and ChatGPT. |
| OD8 | No LLM in the loop: no butler fronts a request, no recursive LLM-to-LLM call. |
| OD9 | Dedupe the work; queue behind RFC 0038 only where a real dependency exists. |
| OD10 | The projection is a second database on the existing cluster `postgres-<env>`, not a separate CNPG cluster. The owner weighed a separate cluster's stronger isolation against its upkeep and chose lower upkeep (D3). |
| OD11 | Alerts reach the owner through the existing homelab Uptime Kuma, which polls flag series in Prometheus and notifies on its Telegram notification. No new Alertmanager receiver (D16). |
| OD12 | Live `dev` data from the first client connection is accepted. Kill switches remain the incident control (D16, owner act 8). |
| OD13 | The upkeep of the trigger-maintained source-owned materializations is accepted, with the parity tests and CI drift checks as its mitigation (D2). |
| OD14 | The publisher has no credential. It logs in only by local peer authentication through the CNPG `local` `pg_ident` map; no password or certificate exists, and every TCP login as the publisher is rejected. The slot, the subscription (created by the superuser with `password_required = false`, `create_slot = false` and conninfo on the local socket, then owned by `mcp_subscription_owner`), `REFRESH PUBLICATION` and the narrowing purge become one owner step: Butlers generates one idempotent SQL file per manifest change, and the owner pipes it through the same `kubectl exec ... psql -U postgres` path as `init-db.sql`. Publication DDL in `butlers` stays with the migration user in the migration Job. The public server fails closed while the owner step is pending, so a narrowed manifest never keeps serving rows that should have been purged (D3, D4, D9, D17). |
| OD15 | `mcp_subscription_owner` is kept as created: `LOGIN`, with its three password copies. No Butlers workflow needs it to log in (D9). |

## Decisions

Each gives the contract, the reasoning and the rejected alternatives;
evidence is labelled [Observed], [Inferred] or [Unknown].

### D1. A standalone deterministic server, not a module and not dashboard-api

**Contract.** Three packages, wired in a fixed import direction:

| Package | Role | May import |
|---|---|---|
| `butlers.knowledge_read` | Pure read functions over the projection. Each takes an asyncpg connection and validated arguments and returns typed results. They write nothing, keep no state outside the call, and never touch the network. Owns `projection.toml`. | stdlib, asyncpg, pydantic |
| `butlers.capability_host` | The request host: bearer and assertion checks (D6, D7), the tool allowlist, argument validation, budgets, kill switches, the DLP scan, audit. Reusable, with no Butlers domain knowledge. | stdlib, `cryptography`, `rfc8785`, `butlers.knowledge_read` types |
| `butlers.mcp_public` | A thin FastMCP entrypoint (`stateless_http=True`, JSON responses, no SSE GET). Registers the tools in the D10 table and nothing else. | the two packages above, `fastmcp` |

The process runs from its own image entrypoint, `butlers-public-mcp`, in its
own Deployment and namespace (D8). It is not a butler: it has no roster
entry, no schedule, no spawner, no state store and no switchboard client.

**Why.** [Observed] Butler daemons serve FastMCP on `0.0.0.0` with no
caller authentication (`src/butlers/daemon.py:616,628`), all in one
`butlers up` process; an internet-facing surface there would sit one hop
from every unauthenticated tool. Vision Rule 2 says modules only add tools,
and a module owning its own transport, auth and second database pool is core
infrastructure in disguise. Rule 7 puts transport in edge processes.

**Rejected.** *A `dashboard_read`-style module on Concierge*: [Observed]
`module-dashboard-read` reads live views through the Switchboard (RFC 0030);
its source envelope is reused here, its live data source and in-daemon
placement are not. *Routes on dashboard-api*: [Observed] it holds the
credential pool and never runs `SET ROLE` (`src/butlers/api/deps.py:578-582`),
its `POST /api/butlers/{name}/mcp/call` forwards any tool with no allowlist
(`src/butlers/api/routers/butlers.py:1253`), and it is passkey-gated
in-process (`src/butlers/api/owner_auth/http.py:179-196`). An OAuth
`resource_owner` in front of Butlers MCP maps to `actor_type="human"`
(`src/butlers/modules/approvals/module.py:241-242`), so a ChatGPT session
could approve parked sends. Never put an OAuth layer in front of a butler
daemon's or dashboard-api's MCP.

### D2. A projection database fed by logical replication

**Contract.** The primary has exactly one publication, `butlers_public_v1`,
with per-table column lists and row filters generated from the manifest
(D4). The projection has one subscription, `butlers_public_sub`. DDL is
never replicated: the owner step (D3) creates the subscriber tables first,
with the published columns only. Subscriber-side extras (GIN trigram and tsvector
indexes, read views in schema `mcp_read`, the local inbox table) never exist
on the primary.

PG15+ rules the generator enforces [Observed: PG17 docs, mechanics review]:
(1) with UPDATE/DELETE published, a row filter may use only replica-identity
columns; (2) column lists must include the replica-identity columns; (3)
`REPLICA IDENTITY FULL` defeats a narrowing column list and is forbidden;
(4) row filters allow only simple immutable built-ins, so allowlists are
literal `IN (...)` lists; (5) a table in two publications of one
subscription needs identical column lists, hence exactly one publication;
(6) PG17 does not publish generated columns, so the subscriber defines its
own (e.g. `relationship.entity_facts.object_entity_id`).

Each row-filtered table gets a non-partial unique index over `(id,
<filter columns>)`, with every column `NOT NULL`, set with `REPLICA IDENTITY
USING INDEX`. The owning chain's migration creates it, through one helper:
`butlers.migrations.projection.ensure_replica_identity`. Because the filter
columns are part of the replica identity, a row that stops matching (a fact
reclassified to `pii`, an episode restricted by the owner) arrives at the
subscriber as a DELETE. A later restriction therefore removes the row from
the projection [Observed: PG row-filter UPDATE transformation semantics].

**No JSONB is ever published.** No manifest entry may publish a JSONB
column or extract a JSONB key; a column list cannot narrow a JSON document,
and PG17 does not publish generated columns (rule 6). A field that lives only
inside JSONB, or behind an overlay a row filter cannot see, reaches the
projection only through a **source-owned materialization**: a plain column or
a small table on the primary, owned by the source's migration chain, kept equal
to its source by deterministic code, and covered by a parity test. There is
still only one transport, replication. A materialization is not a second pipe
into the projection; it is the OD1 fallback for sources that cannot be
row-filtered or column-narrowed. v1 needs five, and any future source of the
same kind uses the same pattern:

- **Chronicler corrected visibility.** [Observed] Owner privacy and tombstone
  corrections live in `chronicler.overrides` and are applied by the
  `v_episodes_corrected` view; the canonical `episodes` row never changes. A
  row filter on `episodes.privacy` would publish rows the owner later
  restricted, and replicating `overrides` would publish the restricted
  payload. Chronicler therefore owns `chronicler.episodes_public` (`id,
  started_at, ended_at, kind, title, category, effective_privacy,
  tombstoned`). Deterministic Chronicler code rewrites the affected rows from
  the corrected view in the same transaction as each override write, plus a
  15-minute sweep. The manifest publishes it filtered to
  `effective_privacy = 'normal' AND tombstoned = false`. The rollups behind
  `time_use` get the same treatment (`chronicler.rollups_public`).
- **Entity flags.** [Observed] The merge pointer, the soft-delete stamp and
  the unidentified-sender marker are all `public.entities.metadata` keys
  (`merged_into`, `deleted_at` and `unidentified`;
  `alembic/versions/core/core_002_identity.py:124-127`,
  `src/butlers/identity.py:459`). Plain columns `merged_into UUID NULL`,
  `deleted BOOLEAN NOT NULL` and `unidentified BOOLEAN NOT NULL` are kept
  equal to them by a `BEFORE INSERT OR UPDATE` trigger (design.md S1).
- **Finance overlays.** [Observed] The merchant and category the finance
  tools show are overlays, read as
  `COALESCE(metadata->>'normalized_merchant', merchant)` and
  `COALESCE(metadata->>'inferred_category', category)`
  (`roster/finance/tools/spending.py:181,197`). A `BEFORE INSERT OR UPDATE`
  trigger on `finance.transactions` keeps two plain columns,
  `display_merchant` and `display_category`, equal to those expressions
  (design.md S7).
- **Health values.** [Observed] Health measurements, medications and
  conditions are memory facts in the `health` schema whose values are
  `metadata` keys, next to free-text `notes`
  (`roster/health/tools/measurements.py:133-135`,
  `roster/health/tools/medications.py:96-108`,
  `roster/health/tools/conditions.py:88-92`). An `AFTER INSERT OR UPDATE OR
  DELETE` trigger on `health.facts` keeps three small tables,
  `health.measurements_public`, `health.medications_public` and
  `health.conditions_public`, holding only allowlisted keys and never `notes`
  (design.md S7).
- **Graph withheld counts.** [Observed] Live edges of any sensitivity carry
  `predicate` and `object_entity_id`; only withheld stubs have NULL payloads
  (`alembic/versions/core/core_215_entity_graph_edges.py`). Edges are
  therefore published filtered to `sensitivity = 'normal'`, and a trigger on
  `public.entity_graph_edges` maintains `public.entity_graph_withheld_counts
  (entity_id, withheld)`, the number of each entity's non-normal edges, so
  the tools can report withheld counts without any non-normal row leaving
  the primary.

**Upkeep cost of materializations.** [Inferred] One trigger function per
source, one parity test each, and a sweep only where the source is an overlay
(Chronicler). Triggers fire on every write path, so a new writer needs no
change. A source migration that renames a JSONB key the trigger reads fails
the parity test in its own PR. This is upkeep beyond "one manifest". The owner
accepted it (OD13), with the parity tests and the D4 drift checks as its
mitigation.

**Why.** OD1. [Observed] On the primary, `butler_secrets` stores
`secret_value TEXT` in every schema (`src/butlers/core/credential_store.py:148-163`).
`public.entity_info` holds `secured=true` rows. Runtime roles get DML on all
of `public` (`scripts/init-db.sql:445-451`). Three `SECURITY DEFINER`
functions are granted to PUBLIC, among them `resolve_owner_triple`, an owner
email and phone oracle. A role that can connect to the primary inherits all
of that through PUBLIC. A role that can connect only to a database that never
received those objects cannot reach them.

**Rejected.** *Read views on the primary*: fresher, with RFC 0030 as
precedent, but they need the PUBLIC grants migrated, a dedicated view-owner
role and a perpetual contract test against a database that holds the
secrets, where one missed grant leaks them (OD1). *An exporter for
everything*: a second code path per source with its own scheduling and
drift; kept only for sources that cannot be row-filtered. *Whole-schema
replication*: `FOR TABLES IN SCHEMA` cannot narrow columns and would publish
every future table by default.

### D3. Projection placement: a second database on the existing cluster

**Contract (OD10).** The projection is the separate database `butlers_public`
on the primary's CNPG cluster `postgres-<env>`, never the primary database
`butlers`. A connection to `butlers` as `mcp_public_ro` fails
(REQ-public-mcp-projection-008 is the normative text). The boundary between
the two databases is the database layer, not the network:

1. **`pg_hba`.** CNPG orders its fixed rules first, then the user rules, then
   its catch-all. The user rules admit each projection login to
   `butlers_public` only, then reject it everywhere else, and reject every
   TCP login as the publisher. As applied on `postgres-dev` [Observed:
   `pg_hba_file_rules`, homelab PR #40]:

   ```text
   local   all            all                           peer map=local     (CNPG fixed)
   hostssl postgres/replication streaming_replica ... cert                 (CNPG fixed)
   hostssl all            cnpg_pooler_pgbouncer ... cert                   (CNPG fixed)
   hostssl butlers_public mcp_public_ro           10.42.0.0/24 scram-sha-256
   hostssl butlers_public mcp_proposals_reader    10.42.0.0/24 scram-sha-256
   host    all            mcp_public_ro           all reject
   host    all            mcp_proposals_reader    all reject
   host    all            butlers_projection_publisher all reject
   host    all            all                     all scram-sha-256        (CNPG catch-all)
   ```

   Roles are cluster-global, so without the reject lines the catch-all could
   admit either projection role to `butlers`, `postgres` or `template1`. A
   contract test connects as each role to those three databases and expects
   refusal.

   **The address range does not separate pods.** [Observed] The cluster is
   one node whose pod range is `10.42.0.0/24`, so the public pod and the
   `butlers-<env>` pods share it. Roles do the separating: of the workload
   namespaces, only `butlers-public-<env>` holds the `mcp_public_ro`
   password and only `butlers-<env>` holds the `mcp_proposals_reader`
   password (D9). A second node would give its pods a range outside
   `10.42.0.0/24`, so a projection login from it fails closed until the
   owner widens the rule.
2. **Grants.** `CONNECT` and `TEMPORARY` on `butlers_public` are revoked from
   PUBLIC (D9). Neither role is granted `CONNECT` on any other database, is a
   member of any primary role, or owns anything outside `butlers_public`.
3. **No monitoring role.** The owner of `mcp_read.replication_as_of()` never
   holds `pg_read_all_stats`: on the shared instance that role would expose
   every primary session's query text, which can carry secret literals.

**Network.** The public server's egress targets the `postgres-<env>` instance
pods on 5432 (D8). Those pods also serve the primary, so the network layer
cannot separate the projection from the primary: a stolen `mcp_public_ro`
password reaches a socket that also fronts `butlers`, and `pg_hba` plus grants
are what refuse it. The importer needs no new network path, because the
`butlers-up` pod already reaches the cluster for the primary.

**Same-instance subscription.** The publication and the subscription live on
one instance. [Observed: PostgreSQL docs, CREATE SUBSCRIPTION notes] A
subscription that connects to its own cluster can be created only if its
replication slot is not created by the same command; otherwise
`CREATE SUBSCRIPTION` hangs. The slot is created separately with
`pg_create_logical_replication_slot` and the `pgoutput` plugin, and the
subscription with `create_slot = false`.

**The owner step (OD14).** The publisher has no password (D9), so the
subscription's conninfo names the local socket and carries no secret.
PostgreSQL then requires `password_required = false` on the subscription,
which only a superuser can set. [Inferred: PostgreSQL 16+
`subscriptioncmds.c`] A non-superuser also cannot `ALTER` (including
`REFRESH PUBLICATION`) or `DROP` a subscription whose `password_required` is
false. Creating, refreshing and dropping the subscription, and creating the
slot, are therefore superuser work, and they form one owner step:

- **The file.** `butlers public-mcp owner-sql` prints one SQL file computed
  from the manifest alone. It opens no database connection, holds no secret,
  and the same manifest always yields the same bytes. The owner runs it from
  a checkout at the deployed revision through the same path as
  `scripts/init-db.sql`:
  `butlers public-mcp owner-sql > owner-step.sql`, then
  `kubectl exec -i -n postgres-<env> <instance> -- psql -X -U postgres -d butlers -f - < owner-step.sql`.
  That is peer authentication over the instance's local socket.
- **Control flow.** The file sets `ON_ERROR_STOP`, so the first failure ends
  it. It moves between the two databases with psql `\connect` (to
  `butlers_public` and back to `butlers`) and branches with `\gset` and
  `\if`. Every query feeding `\gset` returns exactly one row (`SELECT
  EXISTS (...) AS x`, `count(*)` or an aggregate), because psql treats a
  `\gset` query that returns zero rows or several as a failed command, which
  `ON_ERROR_STOP` would turn into an abort on the first sync [Observed: psql
  `\gset` semantics]. `REFRESH PUBLICATION` and `DROP SUBSCRIPTION` of a
  subscription that has a slot cannot run inside a transaction block
  [Inferred: PostgreSQL 17 docs, ALTER SUBSCRIPTION and DROP SUBSCRIPTION],
  so those two are never put in a `DO` block. A `DO` block is allowed for
  statements that may run in a transaction, such as the bounded wait for a
  slot to be released in step 4.
- **Order.**
  1. *Guard, in `butlers`.* Stop unless the current user is a superuser,
     `mcp_surface.manifest_state` carries the file's digest, and the
     publication itself matches the manifest: the file embeds the expected
     `pg_publication_tables` rows (schema, table, attnames and rowfilter) and
     stops on any difference. The digest row alone is not trusted, because
     the check on the publication is what proves that the migration Job
     reconciled it for this manifest (D4). Then run the database half of the
     no-credential check (D9 control 2) and stop if it fails.
  2. *Slot state, in `butlers`.* Note whether the slot `butlers_public_sub`
     exists and whether it is invalidated (`wal_status = 'lost'`).
  3. *Rebuild decision, in `butlers_public`.* A rebuild is needed when no
     applied record exists (the first sync, or an earlier run that stopped
     midway), when the slot is invalidated, or when any table already
     applied has a changed or removed manifest entry (columns, row filter or
     the table itself), compared entry by entry with the manifest recorded
     by the last owner step. A table that is only added needs no rebuild.
  4. *Rebuild, when needed.* Each action runs only if its object exists.
     Delete the applied record first, so the public server refuses from this
     point on (D17). Note the subscription's oid with `\gset`, then disable
     the subscription, set its `slot_name` to `NONE`, drop it, and truncate
     every mirrored table. In `butlers`, terminate any `active_pid` still
     holding the old slot `butlers_public_sub` or a table-sync slot named
     `pg_<old oid>_sync_%`, wait a bounded time until each is inactive, then
     drop each of them. After `slot_name = NONE`, `DROP SUBSCRIPTION` leaves
     both the slot and any unfinished table-sync slots on the primary
     [Observed: PostgreSQL 17 docs, DROP SUBSCRIPTION], and the walsender
     releases a slot asynchronously [Inferred], so a drop without the wait
     could fail and stop the run. On the first sync these find nothing to
     do. [Inferred] PostgreSQL has no command that re-copies one table of a
     live subscription, so a narrowing purge rebuilds the whole subscription;
     the projection is a small subset of the primary, so a full re-copy is
     cheap.
  5. *Slot, in `butlers`.* Create it if absent, with
     `pg_create_logical_replication_slot('butlers_public_sub', 'pgoutput')`.
  6. *Publisher grants, in `butlers`.* Sync the publisher's `USAGE` to
     exactly the schemas and its `SELECT` to exactly the tables of
     `butlers_public_v1` as `pg_publication_tables` lists them, revoking both
     everywhere else. This is the same sync that `scripts/init-db.sql` runs
     (D9), so a manifest change needs no `init-db.sql` rerun. The initial
     copy runs `COPY (SELECT ...)` as the publisher, which needs `USAGE` on
     each source schema as well as `SELECT` on the table; [Observed:
     `postgres-dev`, 2026-11-04] the publisher holds `USAGE` on no Butlers
     schema today.
  7. *Subscriber DDL, in `butlers_public`, under `SET ROLE
     mcp_projection_owner`.* Converge the subscriber side to the manifest,
     acting only where the catalog differs: drop removed tables and columns,
     create added ones with the published columns only, replace an
     `mcp_read` view with `CREATE OR REPLACE VIEW` only when its definition
     changed (a view is dropped only when its table leaves the manifest), and
     re-apply the projection bootstrap and grants (D9). Then `RESET ROLE`.
     `SET ROLE` changes the current user that the superuser check reads, so
     without the reset step 8 would run as `mcp_projection_owner` and fail.
  8. *Subscription.* Check again that the current user is a superuser and
     stop if not. If the subscription is absent, `CREATE SUBSCRIPTION
     butlers_public_sub CONNECTION 'host=/controller/run dbname=butlers
     user=butlers_projection_publisher' PUBLICATION butlers_public_v1 WITH
     (connect = false, create_slot = false, slot_name =
     'butlers_public_sub', enabled = false, password_required = false)`,
     so the command never waits on a slot. Then `ALTER SUBSCRIPTION
     butlers_public_sub OWNER TO mcp_subscription_owner`, `ENABLE`, and
     `REFRESH PUBLICATION`, which registers the published tables and starts
     the initial copy of every table not yet copied.
  9. *Record, last.* When the applied row already carries this manifest's
     digest and canonical text, keep it. Otherwise replace the row of
     `mcp_local.manifest_applied` in `butlers_public` with the manifest's
     digest, its canonical text and the time. Nothing after a failed
     statement runs, so a step that stops midway never records a digest.
- **Rerun.** With an unchanged manifest and a healthy slot, a second run
  finds the slot, the subscription and every table in place. It rebuilds
  nothing, truncates nothing, replaces no view, leaves the subscription, the
  slot and the applied record unchanged, and exits successfully. Idempotent
  `GRANT`, `REVOKE`, `OWNER TO`, `ENABLE` and `REFRESH PUBLICATION`
  statements still run and change nothing.
- **Cadence.** The owner runs the step once at the first sync, once after
  each manifest change the migration Job has applied, and once after each
  slot invalidation (D9), when the step rebuilds the slot and the
  subscription. That is the owner's one recurring upkeep for the projection,
  and it is small because manifest changes are deliberate and rare (OD1).
  Until it runs after the first sync or a manifest change, the public server
  refuses every tool except `describe` with `projection_apply_pending` (D17)
  and the warning flag is down (D16). An invalidated slot surfaces
  differently: the applied record still matches, so the server refuses with
  `projection_stale` once replication is 15 minutes old, and the slot and
  lag alerts fire (D16, D17).

**Shared fate and capacity.** The projection shares the primary's instance,
storage volume, WAL, backups and restore drill.

- A `postgres-<env>` outage takes the projection down with the primary (D17).
- The slot's retained WAL and the projection's tables and indexes both use the
  primary's volume. `max_slot_wal_keep_size = 10GB` caps only the WAL that
  slots hold back (D9 slot safety). [Inferred] Two more terms sit outside that
  cap: logical-decoding spill files under `pg_replslot/<slot>/` for
  transactions larger than `logical_decoding_work_mem` (with the PG17
  subscription default `streaming = off`, a large transaction is spilled in
  full), and the extra WAL that the same-instance apply and initial copy
  write into the same `pg_wal`, which also counts against `max_wal_size`.
  `streaming = on` would not help here: it moves the spill to the
  subscriber, which is the same instance and the same disk.
- **Shared disk.** [Observed: homelab report, 2026-11-04] The data
  directories of `postgres-dev` and `postgres-prod` share one 1.4T disk,
  which is 90 % full with 144G free. Dev holds 24G of data and 577M of WAL.
  The 80Gi and 350Gi PVC sizes are not enforced, so neither cluster is held
  to its claim. The 10GB slot cap is about 7 % of the free space. A full disk
  stops dev and prod together, whatever fills it, so the projection's tables,
  the slot's retained WAL, any spill files and the apply WAL add to a risk
  that is already shared. The
  retained-WAL alert at 50 % of the cap (D16) is the Butlers-side bound; host
  disk headroom stays the owner's.
- The projection needs no backups of its own: every mirrored table rebuilds
  from the publication, and the inbox is transient (D13), so losing undrained
  proposals is an accepted loss. CNPG's cluster backups include
  `butlers_public` anyway. Nothing restores from that copy, and the restore
  drill tolerates or drops it: a restored instance drops `butlers_public` or
  keeps its subscription disabled, so a restored copy never replicates from a
  live primary [Inferred].
- [Inferred] Public reads share the instance's CPU and connections.
  `mcp_public_ro` carries a connection limit of 10 [Observed] and the
  2-second `statement_timeout` (D9), which bound the load a compromised
  public pod can put on the primary.
- [Observed: homelab report] Prod (`postgres-prod`) is also PG 17.8 with
  `wal_level=logical`, one instance and no standbys, so the same placement
  works there when a separate owner act enables it.

**Why.** OD10. [Inferred] The second database keeps the isolation that
condition 1 rests on: the public role connects only to a database that never
received the primary's secrets, PUBLIC-granted definer functions or runtime
grants (D2 Why). It costs no second cluster to run, back up, upgrade and give
its own policy set.

**Rejected.** *A separate CNPG cluster, `postgres-public-<env>`*: stronger
isolation, because the public pod's egress could then select only the
projection cluster's pods, and a stolen password would never reach a socket
that fronts the primary. It adds a second cluster to run, upgrade and police
with its own NetworkPolicy set. The owner weighed that upkeep against the
isolation and chose the second database (OD10). *A Postgres outside CNPG*:
more upkeep, no gain.

### D4. One allowlist manifest, with generated DDL and drift checks

**Contract.** `src/butlers/knowledge_read/projection.toml` is the only place
where exposure is decided. Example entry:

```toml
version = 1

[[table]]
source   = "relationship.entity_facts"
domain   = "relationship"
columns  = ["id", "subject", "predicate", "object", "object_kind", "conf",
            "verified", "validity", "content_authority", "created_at"]
replica_identity = ["id", "predicate", "validity"]
row_filter = { validity = ["active"], predicate = ["knows", "colleague-of", "friend-of",
               "co-attended", "visited", "works-at", "member-of"] }
uri      = "butlers://relationship/fact/{id}"
```

Column names in the example follow `relationship/migrations/013`; `src` is
deliberately not published, because it may name a source system. The
generated artifacts, the primary-side `CREATE/ALTER PUBLICATION` statements
and the owner-step file with its subscriber DDL, are never checked in. The
work is split by who may do it (OD14):

- **`butlers public-mcp reconcile`** runs in the migration Job after Alembic,
  as the migration user, and is idempotent. In `butlers` it creates or alters
  the publication `butlers_public_v1` to equal the manifest, and writes the
  manifest's SHA-256 into `mcp_surface.manifest_state`, which replicates. The
  publication statements and the digest write run in one transaction, so the
  subscriber receives the new digest in the same commit as the publication
  change. Only the migration user can write `manifest_state` (D9). Reconcile
  touches no slot, no subscription and nothing in `butlers_public`.
- **`butlers public-mcp owner-sql`** prints the owner-step file (D3): the
  slot, the subscriber DDL, the subscription, the purge and re-copy, and the
  applied digest. The owner runs it as the superuser.

The pod refuses tools when its built-in manifest digest differs from the
replicated one (deploy skew), and when it differs from the applied digest the
last owner step recorded (owner step pending). Both refusals are in D17.

**Narrowing purges.** Removing a table, removing a column or tightening a
row filter changes only future changes: rows and column values already copied
stay on the subscriber, and a tightened filter does not delete rows that never
change again. The migration Job narrows the publication, and the owner step
purges the subscriber: any changed or removed entry makes it delete the
applied record, drop and truncate, and re-copy from the publication (D3 step
4). The canary probe (D15) then runs before tools serve again (D17).
Narrowing on the publisher side may run before the owner's purge. A pod
shipping the new manifest fails closed in that window: the applied digest no
longer matches, so it serves no tool but `describe` until the owner step has
purged and re-copied. A pod still shipping the old manifest refuses with
`projection_version_mismatch` once the new digest replicates; until then,
for at most the replication lag, which the 15-minute stale rule caps, it
serves the old rows (D17). The leak runbook therefore starts with the kill
switch. It is: kill switch, narrow the manifest,
migration Job reconcile, owner-step purge, probe clean, re-enable (D16).

[Inferred] A widening that adds a column to a table already subscribed can
stop the apply worker until the owner step adds the
subscriber column, because the subscriber table lacks a column the
publication now sends. Replication then stalls for every table, so the slot
retains WAL meanwhile, bounded by `max_slot_wal_keep_size`. The pending-step
warning (D16) and the slot alerts cover the window, and the surface already
refuses.

**Drift checks (CI, on a PG17 testcontainer with all migrations applied):**
1. Every manifest table and column exists; filter columns are `NOT NULL`;
   the replica-identity index exists and matches.
2. After reconcile and the owner-step file, `pg_publication_tables`
   (attnames, rowfilter) equals the manifest exactly, and the subscriber
   tables have exactly the published columns plus declared local extras.
3. Denylist: no table is `butler_secrets`, `entity_info`, `credentials`,
   `oauth_*`, `*_tokens`, `pending_actions`, `approval_events`, a
   private-custody table, a raw message table or location fixes; no published
   column has a JSONB type; no column name matches a denylist entry; every
   table with a sensitivity or privacy column filters it to a literal safe
   value. The column denylist is `secret`, `token`, `password`, `credential`,
   `otp`, `iban`, `account_number`, `card`, `cvv`, `cancellation_url`, `raw`,
   `body`, `metadata`, `tags`, `embedding`, `confirmation`, `pnr`, `seat`,
   `address`, `passport`, `document_number`, `last_four`, `phone`, `email`,
   `description`, `memo`, `note`, `notes`, `receipt_url`, `external_ref`,
   `source_message`, `source_value`, `source_schema`, `source_table`,
   `source_id`, `blob_ref` and `payment_method`. Matching is case-insensitive
   on underscore-separated name segments: an entry matches when its segments
   appear as a contiguous run of the column's segments. So `access_token`
   matches `token` and `source_message_id` matches `source_message`, while
   `withdrawal` does not match `raw` and `direction` passes.
4. Canaries planted in excluded sources (D15) are absent after a full sync.
5. A migration that drops or renames a published or filter column fails the
   drift check in its own PR.

**Upkeep.** [Inferred] A new table is never published unless someone adds
it. A new column on a published table stays unpublished until someone adds
it. Both are safe defaults with zero routine upkeep. The only maintenance
events are adding data on purpose (one manifest edit, plus a
replica-identity migration if it is row-filtered) and a source migration
that touches a published column, which the drift check flags in its own PR.
This is the owner's maintenance concern (OD1), met by having one file and no
generated code to keep in sync. Two additions remain: the source-owned
materializations of D2, whose upkeep is listed there, and the owner step
(D3), which the owner runs once at the first sync, once per manifest change
and once per slot invalidation.

**Initial contents (v1 defaults, conservative, reversible by manifest edit).**

| Domain | Source | Exposed | Never exposed |
|---|---|---|---|
| knowledge | memory `facts` in `general`, `lifestyle` and `education` | `id, subject, predicate, content, valid_at, sensitivity, content_authority, search_vector`, filtered to `sensitivity='normal'`, `validity='active'` and a literal per-schema predicate allowlist (`predicate IN (...)`, with `predicate` in the replica identity) | `metadata`, `tags`, `embedding`; schemas `relationship`, `home`, `switchboard`, `finance`, `health`; every predicate whose registry scope is `relationship` and every contact-, interaction- or identity-kind predicate (`contact_phone`, `contact_email`, `contact_note`, `birthday`, `lives_in`, `interaction`, `life_event`, `loan`, `gift`, `contact_task`, `reminder`) |
| travel | memory `facts` in `travel` (same columns and filters as knowledge); `travel.trips`; `travel.legs`; `travel.documents` | trips `id, name, destination, start_date, end_date, status`; legs `id, trip_id, type, carrier, departure_city, departure_at, arrival_city, arrival_at`; documents `id, trip_id, type, expiry_date` | `confirmation_number`, `pnr`, `seat`, airport and station columns, `accommodations` (it holds `address`), `reservations`, document `blob_ref`, every `metadata`, raw booking emails, location fixes (bu-s11n0s.7 retention applies) |
| relationship | `public.entities`; `relationship.entity_facts`; `public.entity_graph_edges`; `public.entity_graph_withheld_counts` | entity `id, canonical_name, entity_type, aliases, roles, merged_into`, filtered to `deleted = false AND unidentified = false` (S1); relational-kind facts only; edges `id, subject_entity_id, predicate, object_entity_id, sensitivity, created_at`, filtered to `sensitivity = 'normal'`; withheld counts `entity_id, withheld` (a source-owned, trigger-maintained count of each entity's non-normal edges, so withheld stubs stay countable without publishing them) | `entities.metadata`; `entity_info`; edge `source_schema`, `source_table`, `source_id` and `withheld_reason`; every non-normal edge; contact-kind predicates (`has-email`, `has-phone`, `has-handle`, `has-address`, `has-birthday`, `has-website`); override-kind (`dunbar_tier_override`); interactions, which live as `relationship` memory facts and are not published in v1 |
| time | `chronicler.episodes_public`, `chronicler.rollups_public` | as listed in D2 | `overrides`, raw activity, location |
| finance | `finance.transactions`; `finance.accounts`; `finance.bills`; `finance.subscriptions` | transactions `id, account_id, posted_at, amount, currency, direction, display_merchant, display_category, deleted_at` (D2; `deleted_at` is nullable, so it is not a row filter, and the `mcp_read` view hides soft-deleted rows); accounts `id, institution, type`, from which the view derives `account_label` as institution plus type; bills `id, payee, amount, currency, due_date, frequency, status`; subscriptions `id, service, amount, currency, frequency, next_renewal, status` | account `name`, `last_four` and numbers; transaction `description`, `notes`, `source_message_id`, `external_ref`, `receipt_url`, `payment_method`, `raw_data`, `metadata`, `tags`; `cancellation_url`; `balance_snapshots`; the finance memory-fact layer |
| health | `health.measurements_public`, `health.medications_public`, `health.conditions_public` (D2) | measurements `id, metric, value_numeric, unit, measured_at` (numeric `measurement_*` predicates only; a dict-valued reading yields one row per numeric key, named `<predicate>.<key>`); medications `id, name, dosage, frequency, schedule, active, recorded_at`; conditions `id, name, status, diagnosed_at` | `health.facts` itself, `notes`, clinicians and providers, visit notes, documents |
| proposals | `relationship.external_fact_proposals` | `proposal_id, state, decided_at` | payload, note, evidence URL, submitter ids |
| (surface) | `mcp_surface.controls`, `mcp_surface.manifest_state` | controls `domain, state, changed_at` (`domain` is `all` or a domain name); manifest state `digest` | the actor of a change |

Each published table belongs to exactly one domain, and memory facts take the
domain of their source schema (`travel` memory facts are domain `travel`).
`mcp_surface` belongs to no domain and is read by `describe` and the switch
evaluator. `mcp_local.manifest_applied`, which the owner step writes, is a
subscriber-local table, never published.

[Inferred] Dropping the `relationship` memory schema and filtering the other
schemas by a predicate allowlist matters because sensitivity labels come from
LLM writers and default to `normal`. Relationship-schema memory facts hold
contact details and email-derived interaction summaries (the deprecated
`contact_phone` and `contact_email` predicates are still seeded,
`src/butlers/modules/memory/migrations/002_seed_predicates.py:694-716`), and a
row filter on sensitivity alone would publish them. The allowlist costs one
manifest edit per new predicate the owner wants published; a new predicate
stays unpublished by default.

[Unknown] Whether `entities.aliases` or trip `destination` ever hold an email
address, phone number or street address. The DLP rules in D15 redact those
shapes as a backstop.

`last_four` and balances are excluded by default ([Inferred] low value for
the target use cases). The owner can add them later with a one-line manifest
edit. There is no unlock.

### D5. One server for every domain, with kill switches

**Contract.** Every tool, finance and health included, is served by the one
`butlers` gateway resource and the one process (OD3). A domain is either
`enabled` or `disabled`. **Domains attach to data, not only to tools:** every
published table belongs to exactly one domain (D4), and every read function
excludes rows from disabled domains, so no tool can serve a disabled domain's
rows by another route.

- *Single-domain tools* (every typed tool except `timeline`, plus
  `propose_fact`) return `domain_disabled` when their domain is off.
- *Multi-domain tools* (`search`, `fetch`, `search_filtered`, `timeline`)
  drop items from disabled domains. `search_filtered` and `timeline` list each
  dropped domain in `sources_degraded` with reason `"disabled"`; `search`
  returns `{results: []}` when every spanned domain is off; `fetch` of an id
  whose domain is off returns `domain_disabled` naming that domain.
- `all` disables every tool except `describe`; `describe` belongs to no domain.

Disabled tools stay listed, so `tools/list` is static and cacheable. There is
no time-boxed unlock, no elevation and no per-client tier.

**Why.** OD3. [Observed] The gateway has one OAuth scope (`SCOPE = "mcp"`),
so tiers would need a separate gateway resource, release, ExternalSecret and
connector each. The owner rejected that upkeep. Kill switches are for
incident response, not gating.

**Rejected.** *Tier servers* (`butlers-finance`, `butlers-health`) and
*passkey time-boxed unlock*: OD3. *Hiding disabled tools from
`tools/list`*: it makes the list stateful, and clients cache it.

### D6. The hop bearer

**Contract.** filter-proxy replaces `Authorization` with
`Bearer <UPSTREAM_BEARER>` [Observed: `filter_proxy.py` header handling].
The public pod holds only `SHA-256(bearer)`, file-mounted from a Secret in
`butlers-public-<env>`, compares in constant time, and refuses anything else
with HTTP 401 before parsing the body. The bearer is never an identity and
never maps to an owner principal. During a rotation of the ExternalSecret
value the pod accepts two digests, current and next.

**Why.** [Inferred] The bearer authenticates the hop: it proves the request
passed through the one filter-proxy release. It does not authenticate the
user. traefik-mcp can read Secrets in namespace `mcp`, so the bearer alone
must never be enough to reach data. D7 carries identity.

### D7. Identity: a per-call assertion signed by the Worker

**Contract.** On every forwarded `POST`, the Worker sets
`X-MCP-Assertion: <b64u(header)>.<b64u(payload)>.<b64u(sig)>`. It reads the
body in full (up to its 4 MiB limit) to compute the digest, and signs only
when it has done so.

- `header` is the canonical JSON of
  `{"alg":"EdDSA","kid":"<kid>","typ":"butlers-mcp-assertion+v1"}`.
- `payload` is the RFC 8785 (JCS) canonical JSON of:

```text
v      1
iss    "mcp-gateway"
aud    gateway resource URL, e.g. "https://mcp.tze.how/butlers"
sub    GitHub numeric user id, as a string
cid    OAuth client id (CIMD URL or DCR id), "" when absent
mth    JSON-RPC method, from the parsed body
tool   params.name for tools/call, else ""
path   the query string only: the part after "?", as received, not decoded
       ("" when there is none)
bsha   b64u SHA-256 of the exact body bytes the Worker forwards
jti    b64u of 16 random bytes
iat    integer seconds
exp    iat + 30
```

- `sig` is Ed25519 over `b64u(header) "." b64u(payload)`. This is the
  compact three-segment form that `butlers.core.runtime_probe_control.capability`
  already parses. The verifier reuses that codec and its strict parser,
  which rejects duplicate keys and JSON constants. That follows the
  `bu-pkuny2` guidance to reuse one signed codec rather than write a second.
  The repo already depends on `cryptography` and `rfc8785` [Observed:
  `pyproject.toml`].
- **Per-protocol header sets.** [Observed] The codec's header check is an
  exact field set, `{alg, kid}` (`capability.py:63`). Reusing it means the
  codec gains a per-protocol exact header set: `{alg, kid}` for probe
  capabilities and `{alg, kid, typ}` for Worker assertions, with `typ`
  required and exact. Neither protocol accepts the other's header shape, and
  `typ` never becomes optional. A test proves that a probe capability fails
  the assertion verifier and an assertion fails the probe verifier.
- **Why `path` is the query only.** [Observed] filter-proxy discards the
  incoming path and forwards to `UPSTREAM_URL`'s fixed path plus the incoming
  query (`filter_proxy.py:986-987`). The Worker's path and the pod's path
  therefore differ by design. The path component is fixed by `UPSTREAM_URL`
  and bound by `aud`; `path` binds the query string, which is forwarded as
  received.

**Verification, in order, before any tool code runs.** Any failure answers
JSON-RPC error `-32001` with `{"reason":"unauthorized"}`; the specific cause
goes only to the audit log and metrics.

1. The hop bearer is valid (D6), and the body is a single JSON-RPC object
   (no batch) of at most 1 MiB, parsed with the strict parser.
2. Only `initialize`, `notifications/initialized`, `notifications/cancelled`,
   `ping`, `tools/list` and `server/discover` may arrive without an assertion
   [Inferred: the gateway's catalog probe calls them unauthenticated]. They
   return static content and never open a database connection; an assertion
   present on one is verified anyway. Every other method, `tools/call`
   included, is refused without one.
3. `kid` is in the keyring (current or retiring); the signature verifies;
   the payload bytes equal their JCS re-encoding (non-canonical is refused).
4. `v`, `iss` and `aud` match configuration. `sub` is in the home-side
   allowlist, which duplicates the Worker's on purpose. `cid` matches the
   client policy: an `https` CIMD URL whose host, lowercased, equals an entry
   of the home-side known-domain list (v1: `claude.ai` and `chatgpt.com`, the
   gateway's existing `KNOWN_CLIENT_DOMAINS`), or an opaque DCR id equal to an
   entry of the home-side registered-client list (empty by default). An empty
   `cid` is refused. [Unknown] Which form ChatGPT's connector uses; if it
   registers by DCR, the owner adds its id to the list, and the P3 gate
   verifies ChatGPT against the real form.
5. Clock: `iat ≤ now + 5 s`, `exp ≥ now`, `exp − iat ≤ 60`, and
   `iat ≥ process_start − 5 s`.
6. `jti` is not in the in-memory replay set, which keeps entries until `exp`.
7. `bsha` equals the SHA-256 of the received body bytes, and `mth`, `tool`
   and `path` equal the parsed method, `params.name` (or `""`) and the
   received query string.

**Constraints the gateway must keep (owner acts).** *filter-proxy `FIXES`
stay off*: any body rewrite breaks `bsha`, and the server sets
`structuredContent` itself [Observed: filter-proxy forwards body bytes
unchanged unless a fix rewrites them, `filter_proxy.py:588-628`]. *No
signature on streamed bodies*: a body over 4 MiB streams through the Worker
unread and unsigned, so the home side refuses it as a `tools/call` without
an assertion [Observed: filter-proxy also refuses chunked bodies with 411,
`filter_proxy.py:581`]. *Hash what is forwarded*: the Worker hashes the exact
UTF-8 bytes it forwards and sends them as a byte body. *No forged header*:
the Worker strips every inbound `x-mcp-*` header (`proxy.ts:46`).

**Key custody.** The Ed25519 private key is a Worker secret set by the
owner; it never enters the cluster. The verifier keyring (public keys and
`kid`s, in the `runtime_probe_control` keyring document format) is a
ConfigMap in `butlers-public-<env>`. It is not secret, but its integrity
matters, so it lives only in a Butlers-owned namespace, never in `mcp` or
`mcp-gateway` where gateway-side components could replace it. Rotation: add
the new key as `current` and the old as `retiring`, redeploy the Worker,
then drop the retiring key.

**Why.** [Inferred] Without the assertion, anything that can reach
filter-proxy or the pod can make calls with the hop bearer: a compromised
traefik-mcp, the catalog pod, any pod in namespace `mcp`. With the assertion,
calls require a fresh signature from the Worker, bound to one exact body.
An attacker already on the path can still read the responses that pass
through it; the assertion stops it from minting new queries or replaying a
proposal.

**Known limits.** [Observed] The client id is coarse: a claude.ai connector
also appears in Claude Code sessions on the same account. The replay set
lives in memory, hence `replicas: 1` in v1; more replicas need a shared
replay store (deferred). `iat ≥ process_start − 5 s` stops replays across a
restart, apart from a 5-second grace window for clock skew. The assertion
authenticates the owner's session, not the owner's intent: a client model
steered by content it reads still produces validly signed calls (D15).

**Rejected.** *Trusting `X-MCP-User-Id` headers*: nothing default-denies
ingress in `butlers-<env>` today [Observed], so header trust is unsafe.
*mTLS from the Worker*: the VPC binding ends at cloudflared, not at the pod.
*A JWT library*: a new dependency, when the repo already has the codec.

### D8. Namespace and network placement

**Contract.**
1. The public server runs in a dedicated namespace, `butlers-public-<env>`,
   from its own Helm chart, `deploy/helm/butlers-public-mcp`. It never runs
   in `butlers-<env>`. The chart creates the namespace, following the
   homelab convention that the owning chart creates its namespace, and an
   ExternalSecret that reads the Bitwarden item `MCP_PUBLIC_RO_DB_PASSWORD`
   for the `mcp_public_ro` password [Observed: homelab report; the namespace
   does not exist yet].
2. Default-deny ingress and egress in that namespace. The only allows are:
   ingress on the MCP port from pods in namespace `mcp` with the `butlers`
   filter-proxy release label; ingress on a separate metrics port from the
   monitoring namespace; egress to the `postgres-<env>` instance pods on
   5432; and egress to kube-dns on 53.
3. On the `postgres-<env>` side, the only change is an allow from the public
   server pod to the instance pods on 5432, added without widening anything
   else. NetworkPolicies are additive, and a policy that selects pods no
   policy selected before isolates them. If a default-deny ingress policy
   selects the instance pods, the owner adds one allow policy that selects
   only the instance pods and admits only the public server pod (by namespace
   and pod label) on 5432. If none does, ingress is already open and no policy
   is added, because a new one would cut off every other client of the
   primary. [Observed: homelab report, 2026-11-04] No NetworkPolicy selects
   the `postgres-dev` pods, and there is no cluster-wide or CNI policy, so on
   dev this resolves to "no policy added". The allow is added only if a policy
   later selects the instance pods, and the live check (item 6) watches for
   that. No other peer, port or egress rule
   changes, and no rule exists for the CNPG operator or the instances on the
   projection's behalf: they are the primary's own. The importer and the
   subscription need no new path: the `butlers-up` pod already reaches the
   cluster, and the subscription runs inside the instance. The network cannot
   tell the projection from the primary, so `pg_hba` and grants are the
   boundary (D3). [Observed] Production `butlers up` runs every roster butler
   in one Deployment (`deployment.md`), so what limits the importer's access
   is the `mcp_proposals_reader` grant and its `pg_hba` line (D3, D9), not the
   network.
4. Only the filter-proxy pod carries `mcp.tze.how/exposed=true`. No Butlers
   namespace joins traefik-mcp's watch list, and traefik never routes to a
   Butlers Service; the cross-namespace hop is only filter-proxy's
   `UPSTREAM_URL`.
5. Non-root, read-only root filesystem, no service-account token,
   `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
6. **Live policy check.** The rendered-chart check covers only the policies
   the chart renders. The chart creates `butlers-public-<env>`, but
   homelab-generated or cluster-wide policies (namespace defaults, policy
   engines, CNI cluster-wide CRDs) could widen the union without the chart
   knowing. A live contract check, run after each deploy and on a schedule,
   lists every NetworkPolicy and every cluster-wide policy object that selects
   pods in `butlers-public-<env>` and fails if the set differs from the
   chart's. It also fails if any policy in `postgres-<env>` admits the public
   server's namespace to a pod or port other than the instance pods on 5432,
   and it reports when a policy starts selecting the instance pods, because
   the public pod's allow (item 3) must then be added.

**Why `butlers-<env>` is ruled out.** [Observed] `butlers-tracker-egress`
selects every pod (`podSelector: {}`) and allows egress to all namespaces and
`0.0.0.0/0` (`deploy/helm/butlers/templates/tracker-egress.yaml:15-22`).
NetworkPolicies are additive, so no narrower policy could hold the pod to the
projection (the union trap noted in `bu-kiuh8v`). The unauthenticated daemons
and dashboard-api would also sit one hop away. Separately, traefik-mcp's
namespaced Role grants Secret read in every namespace it watches, which is
why no Butlers namespace may ever join its watch list.

**Precondition (independent, proceeds now).** [Observed] `butlers-<env>`
has no default-deny ingress (`networkpolicy.yaml` selects only dashboard-api,
frontend, connector-owntracks and `butlers-control`). Default-deny ingress
with explicit allows and a contract test must land before any gateway-side
allow rule points into Butlers. The importer's connection is egress only.

### D9. Database roles and grants

**Primary** (`butlers` on `postgres-<env>`):

| Role | Attributes and grants | Notes |
|---|---|---|
| `butlers_projection_publisher` | `LOGIN REPLICATION NOINHERIT`, no password (a CNPG managed role [Observed: homelab PR #40]). `scripts/init-db.sql`, which the owner runs as the superuser, grants it `CONNECT` on `butlers` and syncs its `USAGE` to exactly the schemas and its `SELECT` to exactly the tables of `butlers_public_v1` as `pg_publication_tables` lists them, revoking both everywhere else (the initial copy runs `COPY (SELECT ...)` as the publisher, which needs both); the owner step repeats that sync (D3 step 6). [Observed: `postgres-dev`, 2026-11-04] Today it has no `CONNECT` on `butlers` and `USAGE` on no Butlers schema. No schema CREATE. Not a member of `_all_runtime_roles`, and no role is a member of it: it is granted to no role, the migration user included, because a member could `SET ROLE` to it over TCP and decode WAL through the SQL slot functions, which check the current user's `REPLICATION` attribute [Observed: PostgreSQL 17 `CheckSlotPermissions`]. | A named exception to the `NOREPLICATION` normalization in `scripts/init-db.sql:235,264` and to the restore-drill guards (`bu-viat6h.9`, `bu-mc01y5.7`, `bu-kqnum.17`). Each guard keeps refusing `REPLICATION` on every other Butlers-managed role. A test asserts that it is the only role with `REPLICATION` apart from the CNPG superuser `postgres` and the CNPG-managed `streaming_replica` [Observed in the conformance review: CNPG creates both with `rolreplication = true`]. Declared in the `bu-dss7he.6` grant manifest once that lands. |
| migration user | Gains `CREATE ON DATABASE` so it can own the publication. It already owns the published tables, which `ALTER PUBLICATION … ADD TABLE` requires [Observed: `init-db.sql:239-242`]. Writes `mcp_surface` through `reconcile` and the `butlers public-mcp disable\|enable` CLI. Holds no `REPLICATION` and creates no slot or subscription (OD14). | `reconcile` runs as this user in the migration Job, for the publication and `mcp_surface.manifest_state` only (D4). |
| `mcp_surface` schema | Tables `controls`, `manifest_state`. `manifest_state` is writable only by the migration user, through `reconcile`, because the owner step's guard reads it (D3 step 1). `controls` is writable only by the migration user and by the dashboard API's database role (for its owner route; the route's passkey check is the human gate), which gets no write on `manifest_state`. The publisher gets `USAGE` and `SELECT` through its sync, because both tables are published. The Relationship runtime role gets `USAGE` and `SELECT` on `controls` only, so the importer can honour the `proposals` and `all` switches. Every other runtime role is revoked, and no runtime role can write. | Kept out of `public`, because `public` default privileges make new tables writable by every butler (`init-db.sql:477-486`). |

**The publisher is primary-read-all, and it has no credential (OD14).**
[Observed: PostgreSQL semantics] A role with `REPLICATION` can create a
logical slot with any output plugin (for example `test_decoding`, which
[Observed: homelab report] ships in the CNPG image) and decode every change
in the database, regardless of its table grants: new `butler_secrets` rows,
`entity_info` and OAuth tokens included. A `replication` entry in `pg_hba`
would also allow a base backup of the whole cluster. The publication's
column lists and row filters therefore limit what the subscription receives,
not what the role can read. Four controls follow:

1. **Local peer login only.** The publisher logs in only over the instance's
   local socket (`/controller/run`) with peer authentication, through the
   CNPG `local` `pg_ident` map, which holds exactly `local postgres
   postgres` and `local postgres butlers_projection_publisher` [Observed:
   homelab PR #40]. So only OS user `postgres` inside an instance pod can
   log in as the publisher; the apply worker of the subscription is such a
   process. `host all butlers_projection_publisher all reject` precedes
   CNPG's catch-all, so every TCP login as the publisher is refused, and no
   entry names the `replication` pseudo-database for it. Logical replication
   connections match on the database name, so the subscription works and
   physical replication does not.
2. **No credential exists, and no role can become it.** The publisher has no
   password, and no `pg_hba` rule admits it by certificate, so a client
   certificate for it would be refused even if someone minted one. The proof
   rests on database facts, which a contract test and a live check assert:
   `rolpassword` is NULL; the only `pg_hba` rule that can admit the
   publisher is the CNPG `local ... peer map=local` line; the reject line
   precedes the catch-all; no rule names `replication` for it; the `local`
   map holds exactly the two lines above; and `pg_auth_members` has no row
   whose `roleid` is the publisher, so no role can `SET ROLE` to it. Reading
   `pg_hba_file_rules`, `pg_ident_file_mappings` and `pg_authid` needs the
   superuser, so the database half of the live check runs inside the owner
   step (D3 step 1) and after each `init-db.sql` run. The Kubernetes half is
   limited to what it can check without reading Secret data: the CNPG
   `Cluster` resource's managed-role entry for the publisher names no
   `passwordSecret`, and no ExternalSecret spec in `butlers-<env>` or
   `butlers-public-<env>` names the publisher. It does not list Secrets in
   `postgres-<env>`, because a Secret listing returns Secret data, which
   there includes primary-read-all material (see the residual risk below).
3. **Slot alert.** A critical alert fires when `pg_replication_slots` on the
   primary holds a logical slot that is neither the subscription's slot
   `butlers_public_sub` nor one of its table-sync slots (named
   `pg_<suboid>_sync_<relid>_<sysid>` for the current subscription's oid,
   present only while a table is not yet in state `r`), or a logical slot of
   either kind whose plugin is not `pgoutput`. Every initial copy creates
   table-sync slots [Observed: PostgreSQL 14+ tablesync], so without that
   allowance the alert would fire on each first sync and rebuild. Only
   logical slots count: physical slots that CNPG manages for its own
   standbys are excluded by `slot_type = 'physical'`.
4. **Headroom.** [Observed: homelab report] `max_replication_slots = 32`
   and `max_wal_senders = 10`, with 0 slots in use before the projection,
   leave headroom for the subscription's slot, its table-sync slots and one
   more.

**Residual risk: superuser-equivalent access to the instance.** Whoever can
`kubectl exec` into a `postgres-<env>` instance pod can already run `psql -U
postgres` by peer authentication, which is the superuser [Observed: that is
how the owner runs `scripts/init-db.sql`]. The publisher's peer mapping adds
no access path beyond that. Several permissions already reach
primary-read-all, with or without the publisher, and all of them stay with
the owner [Inferred: CNPG Secret naming; an RWO volume binds per node, not
per pod]:

- `pods/exec`, `pods/attach` and `pods/ephemeralcontainers` (`kubectl
  debug`) on the instance pods;
- creating pods in `postgres-<env>`, since a pod on the same node can mount
  the instance's node-local data volume;
- reading Secrets in `postgres-<env>`: the CNPG `streaming_replica` role
  holds `REPLICATION`, the applied `pg_hba` admits it by client certificate
  (D3), and its key sits in a Secret there, so it is a credential that
  allows a physical base backup of the whole cluster;
- writing the CNPG `Cluster` resource, which can change `pg_hba`,
  `pg_ident`, managed-role `passwordSecret`s or superuser access;
- access to the node or its disk (`/dev/sdb2`).

The publisher adds none of these. [Inferred] The migration Job no longer
needs the `REPLICATION` attribute or any publisher credential, so it can no
longer decode WAL.

**Projection** (`butlers_public` on `postgres-<env>`):

| Role | Grants |
|---|---|
| `mcp_projection_owner` (NOLOGIN) | Owns `butlers_public` [Observed], the mirrored tables, the `mcp_read` views, the inbox and `mcp_local.manifest_applied`. The owner step runs the subscriber DDL under `SET ROLE` to this role, so it owns what the step creates (D3). |
| `mcp_public_ro` (LOGIN, NOINHERIT, connection limit 10) | Starts from `REVOKE ALL` on every schema, table, sequence and function, `REVOKE CONNECT, TEMPORARY ON DATABASE butlers_public FROM PUBLIC`, and `REVOKE CREATE ON SCHEMA public FROM PUBLIC`, so it can create neither tables nor temporary tables. Then: `CONNECT`; `USAGE` on `mcp_read`; `SELECT` on `mcp_read` views only (never the mirrored base tables), including `mcp_read.manifest_applied`, which shows the applied digest and manifest and whether the initial copy is complete (D17); `INSERT (columns…)` on `mcp_inbox.proposals`; `EXECUTE` on `mcp_read.replication_as_of()` only. Role setting `statement_timeout = 2s`, set by `scripts/init-db.sql` with `ALTER ROLE mcp_public_ro SET statement_timeout = '2s'`, because CNPG role specs cannot set it [Observed: homelab report]. The connection limit of 10 [Observed] bounds its load on the shared instance (D3). `pg_hba` admits it to `butlers_public` only (D3). |
| `mcp_proposals_reader` (LOGIN) | `SELECT, DELETE` on `mcp_inbox.proposals`. Used only by the importer; its password is file-mounted in `butlers-<env>`, never stored in `butler_secrets`. `pg_hba` admits it to `butlers_public` only (D3). The importer reads the kill switches on the primary, not here (D16). |
| `mcp_subscription_owner` (LOGIN; member of `pg_create_subscription` and `mcp_projection_owner`) | Owns the subscription. The superuser creates it in the owner step and then runs `ALTER SUBSCRIPTION butlers_public_sub OWNER TO mcp_subscription_owner` (D3). The apply worker runs as the subscription owner and switches to each table's owner, which needs membership in `mcp_projection_owner` [Inferred: PostgreSQL 16+ logical-replication security]. Per OD15 the role keeps `LOGIN` and its password copies, at the owner's choice. No Butlers workload mounts or uses that credential, and no Butlers workflow logs in as it. [Inferred: PostgreSQL 16+ `subscriptioncmds.c`] It cannot `ALTER` or `DROP` the subscription, because `password_required` is false; the `pg_create_subscription` membership is unused, since the superuser creates the subscription. |

Credential custody, names only [Observed: homelab report; keys `username` and
`password`]:

| Credential | Kubernetes Secrets | Bitwarden (dev) | Mounted by |
|---|---|---|---|
| `mcp_public_ro` | `postgres-mcp-public-ro-user` in `postgres-dev` (the CNPG `passwordSecret`); the public chart's ExternalSecret in `butlers-public-dev` | `MCP_PUBLIC_RO_DB_PASSWORD` | the public server only |
| `mcp_proposals_reader` | `postgres-mcp-proposals-reader-user` in `postgres-dev` (the CNPG `passwordSecret`) and in `butlers-dev` | `MCP_PROPOSALS_READER_DB_PASSWORD` | the importer in `butlers-up` only |
| `mcp_subscription_owner` | `postgres-mcp-subscription-owner-user` in `postgres-dev` and in `butlers-dev` | `MCP_SUBSCRIPTION_OWNER_DB_PASSWORD` | nothing (OD15 keeps the copies) |
| `butlers_projection_publisher` | none | none | nothing: no credential exists (OD14) |

The subscription owner's password is still projection-write material. Its
login is not rejected in `pg_hba`, so CNPG's catch-all `scram-sha-256` line
admits it over TCP to any database whose `CONNECT` it holds. [Observed:
`postgres-dev` `has_database_privilege`, 2026-11-04] That is `butlers_public`
and `postgres`, not `butlers`: the `butlers` ACL grants `CONNECT` to named
roles only, with no PUBLIC entry, while `butlers_public` and `postgres` still
have the default ACL. Once the projection bootstrap revokes `CONNECT` on
`butlers_public` from PUBLIC, the role keeps it through its membership in the
database owner `mcp_projection_owner`. [Inferred] Through that membership a
TCP login can rewrite or drop projection tables, which the public server
then serves, and can write `mcp_local.manifest_applied`, so it could forge
an applied digest and defeat the D17 fail-closed rule. Its custody is
therefore listed above, and a rendered-chart check asserts that no Butlers
chart mounts `postgres-mcp-subscription-owner-user`. The D17 guarantee
assumes that password stays unused; a `host all mcp_subscription_owner all
reject` line would make it hold without that assumption. Whether to add the
line is open question 5.

- The projection has exactly two `SECURITY DEFINER` functions:
  `mcp_read.replication_as_of()` (returning only
  `pg_stat_subscription.latest_end_time`, executable by `mcp_public_ro`
  alone) and the inbox cap trigger function (D13, executable by nobody
  directly). Both are declared `SET search_path = pg_catalog, pg_temp` and use
  only schema-qualified references, so no object a session creates can shadow
  a name inside them. The owner of `replication_as_of()` holds only what
  reading that column needs. [Unknown] Whether PG17 shows `latest_end_time`
  to an ordinary role; if it does, the owner holds no extra role. It never
  holds `pg_read_all_stats` (D3). Nothing is granted to
  PUBLIC, so the primary's PUBLIC-granted definer functions have no
  counterpart here.
- Session settings such as `default_transaction_read_only` are conveniences;
  grants are the boundary.
- **Contract test** (testcontainers): `has_table_privilege` for
  `mcp_public_ro` equals the allowlist exactly; no `EXECUTE` on any function
  outside `pg_catalog` and `information_schema` except `replication_as_of()`;
  INSERT only on the inbox columns; UPDATE, DELETE and SELECT on the inbox
  refused; `CREATE TEMP TABLE` refused; both definer functions have the pinned
  `search_path` (a lint over `pg_proc.proconfig`); a connection to
  `butlers`, `postgres` or `template1` as `mcp_public_ro` or
  `mcp_proposals_reader` is refused by `pg_hba` (D3); `mcp_public_ro` has
  `rolconnlimit = 10` and `statement_timeout = 2s` in its role settings.
- **Slot safety (owner act, done on dev).** [Observed: homelab PR #40,
  verified live] `max_slot_wal_keep_size = 10GB` on `postgres-dev`, so a
  stalled slot cannot retain more than 10GB of WAL. The disk it shares with
  prod has 144G free (D3, shared disk). Alert when retained WAL passes 50 %
  of the cap or the slot is inactive for 10 minutes (D16). An invalidated
  slot means rebuilding the projection from a fresh copy by rerunning the
  owner step (D3); the primary is never put at risk to save the projection.

### D10. Tool contract

**Rules for every tool.**
- Annotations `title`, `readOnlyHint: true`, `destructiveHint: false`,
  `idempotentHint: true`, `openWorldHint: false` (`propose_fact`:
  `readOnlyHint: false`). Annotations are hints, never a control.
- An `outputSchema`, with results as `structuredContent` plus the same
  object as one JSON text block.
- Each record item (entity, fact, edge, timeline item, trip, ledger row,
  obligation, medication, condition) carries
  `source: {kind, uri, as_of, content_authority}`: `uri` is
  `butlers://<domain>/<kind>/<id>`, `as_of` is the replication time (D17),
  `content_authority` comes from the source row or is `null`. Aggregate
  outputs (`time_use`, `finance_summary`, `health_series`) carry one
  response-level `source` with `kind`, `as_of` and a `uri` naming the domain
  and tool instead.
- `url` is a passkey-gated dashboard deep link from a configured base URL,
  never carrying a token, argument echo or query secret (traefik logs paths).
- Cursors are opaque HMAC-SHA256 tokens under a key generated at process
  start, bound to `(sub, tool, args digest, position, exp = 15 min)`; a
  restart invalidates them.
- Text arguments are NFC-normalized and length-capped; unknown arguments are
  refused. Typed-tool responses are capped at 256 KiB; a cut response carries
  a top-level `truncated: true` (absent otherwise). `fetch` marks a cut `text`
  with `metadata.truncated: true`. `search` never truncates: 20 results of
  bounded title length fit well inside the cap, so its exact shape never
  gains a field.
- No generic `query`, `sql` or `invoke` tool (RFC 0027 rejected meta-tools;
  a domain `search` over data is not one).

| Tool | Domain | Arguments | Output | Source | Limits |
|---|---|---|---|---|---|
| `search` | (spans knowledge, relationship, time, travel) | `query: str ≤ 256` | Exactly `{results: [{id, title, url}]}` | `mcp_read.knowledge_index` view | 20 results |
| `fetch` | (the id's domain) | `id: str` | Exactly `{id, title, text, url, metadata}`, with `metadata` = `{domain, kind, source, valid_at, truncated}`. A `proposal:<uuid>` id keeps the same shape: `title: "Proposal"`, `text` = the state, `metadata` = `{domain: "proposals", kind: "proposal", source, valid_at: null, truncated: false, state, decided_at}`, with no payload. An id whose domain is disabled returns `domain_disabled`. | index, plus proposal state | `text ≤ 16 KiB` |
| `search_filtered` | (spans as above) | `query`, `domains?`, `kinds?`, `since?`, `until?`, `limit ≤ 50`, `cursor?` | `{items: [{id, title, snippet, domain, kind, url, source}], next_cursor, sources_degraded}` | index | 50 per page |
| `entity_lookup` | relationship | `name ≤ 128`, `type?` | `{matches: [{entity_id, name, type, match: exact\|alias\|trigram, score}]}`; merged entities resolve to their survivor; deleted and unidentified entities are never published | `entities` | 10 |
| `entity_profile` | relationship | `entity_id`, `sections? ⊆ {facts, edges}` (default both) | `{entity, facts[], edges: {known, withheld}, coverage}` | facts, edges, withheld counts; modelled on `dossier_for_entity`. Interactions are not offered in v1: they live as `relationship` memory facts, which v1 does not publish (D4). | 200 facts |
| `graph_neighbors` | relationship | `entity_id`, `predicates?`, `hops 1..2`, `limit ≤ 200` | nodes, edges with receipts (edge id and anchors), withheld counts | `entity_graph_edges` (normal only) and `entity_graph_withheld_counts`; parity with REQ-entity-graph-003/005/009 | 200 |
| `graph_path` | relationship | `from_id`, `to_id`, `max_hops ≤ 4` | `{path: [edge receipts]} \| {path: null}` | parity with REQ-entity-graph-006 | 4 hops |
| `timeline` | time and travel (multi-domain) | `start`, `end` (span ≤ 92 d), `kinds?`, `cursor?`, `limit ≤ 200` | `{items: [{at, end, kind, title, domain, source}], next_cursor, sources_degraded}`; a disabled domain's items are omitted and the domain listed with reason `"disabled"` | `episodes_public`, trip legs | 200 per page |
| `time_use` | time | `start`, `end` (≤ 366 d), `bucket: day\|week\|month`, `group_by: category\|kind` | `{series: [{bucket_start, group, minutes}]}` | `rollups_public` | 400 points |
| `trips` | travel | `status?`, `trip_id?`, `limit ≤ 50` | trips with legs, city-level places, document expiry dates | trips, legs | 50 |
| `describe` | (none) | — | `{server_version, manifest_digest, domains: {name: enabled\|disabled\|stale}, as_of, tools, columns}` | controls, manifest | — |
| `finance_summary` | finance | `start`, `end` (≤ 366 d), `group_by: category\|merchant\|month`, `currency?` | totals per currency, never converted; `count` | transactions | 500 groups |
| `finance_ledger` | finance | `start`, `end` (≤ 366 d), `category?`, `merchant?`, `cursor?`, `limit ≤ 500` | rows `{date, amount (decimal string), currency, direction, merchant, category, account_label}`; `merchant` = `display_merchant`, `category` = `display_category`, `account_label` = institution plus account type, or `null` | transactions, accounts | 500 per page |
| `finance_obligations` | finance | `horizon_days` (1 to 90) | `{items: [{payee, kind: bill\|subscription, amount (decimal string), currency, cadence, next_due, source}]}`; bills by `due_date`, active subscriptions by `next_renewal` | bills, subscriptions | 200 |
| `health_series` | health | `metric` (enum generated from the published `metric` values), `start`, `end` (≤ 366 d), `bucket: raw\|day\|week` | `{unit, points: [{t, value}], source}`, numeric values only; `day` and `week` buckets return the mean per bucket in the unit unchanged | `health.measurements_public` | 2,000 points |
| `health_profile` | health | `sections? ⊆ {medications, conditions}` (default both) | `{medications: [{name, dose, frequency, schedule, status, since, source}], conditions: [{name, status, since, source}]}`; medication `status` is `active` or `stopped` from `active`, `since` is `recorded_at`; condition `since` is `diagnosed_at` | `health.medications_public`, `health.conditions_public` | 200 |
| `propose_fact` | proposals | `subject_entity_id`, `predicate`, `object_entity_id`, `evidence_url?`, `note ≤ 500`, `idempotency_key ≤ 64` | `{proposal_id, status: "received"}` | inbox INSERT | D13 |

**ChatGPT compatibility.** `search` takes one string and `fetch` takes one
id, with the exact result shapes above [Observed in the 2026-11-04
research pass against OpenAI's connector documentation]. These outputs are a
compatibility contract under `interfaces-and-dependencies.md:18-19`, so
changing a field is a versioned change. `search` and `fetch` never return
finance or health records; those come only from typed tools, so a research
agent running broad searches does not sweep the ledger by accident.

**Performance budget** [Inferred; measure before tuning]: p95 ≤ 500 ms for
lookups and pages at current volumes; `statement_timeout` 2 s; per-tool cost
bounded by the limits above.

### D11. Search is lexical in v1

**Contract.** `search` and `search_filtered` rank results with PostgreSQL
full-text search (`websearch_to_tsquery` over the replicated `search_vector`
where the source has one, and subscriber-side `tsvector` indexes elsewhere),
blended with `pg_trgm` similarity on titles and names. Embedding columns are
not replicated. The public pod loads no model.

**Why.** [Observed] `all-MiniLM-L6-v2` is fetched from the HuggingFace hub
at runtime (`src/butlers/modules/memory/embedding.py:9-24`). Semantic search
needs the query embedded in the pod: baked weights, and source and pod on
exactly the same model version, or vectors silently stop matching. Lexical
search covers names, places and topics, the main use cases. **Deferred
alternative**: bake MiniLM in with `HF_HUB_OFFLINE=1`, replicate vectors,
and add a version-equality check to `describe`; reversible, and no doctrine
change, since an embedding model is not an LLM.

### D12. Reads are pure

**Contract.** No `knowledge_read` function writes or calls existing memory
search or recall code, and none stamps `last_fetch_at`-style state. **Why.**
[Observed] Memory search runs `UPDATE … SET reference_count =
reference_count + 1` on every read (`src/butlers/modules/memory/search.py:722`);
external clients must never steer the recall and decay ranking later
Butlers sessions rely on. Such a write would fail under `mcp_public_ro`
anyway; the rule makes the intent explicit and testable.

### D13. The `propose_fact` pipeline

**Target store.** [Observed] `relationship_assert_fact` is the only writer
for `relationship.entity_facts`. It already parks as a pending action, and
the approvals executor has a dedicated branch for it that checks the
stored-arguments digest. Memory facts have no gated replay command. v1
therefore proposes Relationship triples only.

**Proposable predicates (v1).** A literal allowlist, kept in
`src/butlers/knowledge_read/proposable.toml`:
relational-kind predicates only (`knows`, `colleague-of`, `friend-of`,
`co-attended`, `visited`, `works-at`, `member-of`); not the family-gated set
(`family-of`, `partner-of`, `parent-of`, `child-of`), which stays
owner-direct; no contact-kind predicate, because contact predicates feed
identity resolution and owner routing; no override-kind predicate.

Every allowed predicate has `object_kind='entity'` [Observed: rel_014/024
seeds], so v1 proposals take no literal object. The subject must be an
existing, unmerged, undeleted, non-owner entity and the object an existing,
unmerged, undeleted entity. v1 creates no entities.

**State machine.** The inbox row lives on the projection. The authoritative
state lives on the primary, in `relationship.external_fact_proposals`
(`proposal_id` PK).

```text
                 propose_fact (public pod)
                          │ INSERT … ON CONFLICT DO NOTHING
                          ▼
                    [received]  (inbox row, projection)
                          │ importer pass: copy to primary, then DELETE inbox row
                          ▼
                    [imported]
        ┌─────────┬───────┼───────────┬─────────────┐
        ▼         ▼       ▼           ▼             ▼
   [invalid] [duplicate] [conflict] [rate_limited]  [parked] ── pending_action_id
                                                       │
                     ┌───────────────┬─────────────────┼────────────────┐
                     ▼               ▼                 ▼                ▼
                [applied]     [owner_rejected]     [expired]      [apply_failed]
```

1. **Receive** (public pod). The pod first checks the arguments against the
   allowlist and the shape rules. It then computes
   `proposal_id = uuid5(NS, sub ‖ cid ‖ idempotency_key ‖ sha256(JCS(payload)))`
   and inserts it with `ON CONFLICT DO NOTHING`, which needs no SELECT. It
   returns `{proposal_id, status: "received"}`. An identical retry returns
   the same id. A changed payload under a reused key gets a new id, which
   the importer marks `conflict`.
2. **Import** (Relationship butler job `public_mcp_proposal_import`, run
   inside the `butlers-up` pod in Kubernetes,
   `dispatch_mode = "job"`, every 5 minutes, no LLM). The job reads up to
   100 inbox rows as `mcp_proposals_reader`, inserts each into
   `relationship.external_fact_proposals` with `ON CONFLICT DO NOTHING`, and
   then deletes the inbox rows it recorded; copy-then-delete is idempotent
   across crashes. Each row is then checked in order: schema and allowlist
   again (the pod is not trusted); entities exist and are neither merged nor
   deleted, and the subject is not the owner (`invalid`); the note and evidence URL pass the
   DLP secret-shape scan (`invalid`); no identical active fact exists
   (`duplicate`); this proposal is not a later payload under a
   `(sub, cid, idempotency_key)` already used by an earlier one (`conflict`:
   the earliest proposal for a key, ordered by inbox receipt time and then
   `proposal_id`, proceeds, and every later different payload is `conflict`,
   also when both arrive in one pass); per-`(sub, cid)` caps of 20 per day
   and 50 unresolved hold (`rate_limited`). The importer reads the
   `proposals` and `all` rows of `mcp_surface.controls` on the primary (D9)
   at the start of each pass and parks nothing while either is disabled;
   the break-glass ConfigMap and the in-process latch affect only the public
   server.
3. **Park.** For a valid row, the importer calls a new approvals entry point,
   `park_external_proposal`. [Observed] `park_pending_action` is the
   notifying path: with `admission_enabled` it creates a delivery intent and a
   `single` or `cohort` presentation that reaches a provider through the
   Switchboard and Messenger (`src/butlers/modules/approvals/park.py:548-594`).
   `park_external_proposal` uses the same durable admission protocol with one
   standalone `collapsed` presentation, as `park_prepared_action` does, and
   keeps `origin` NULL. It is called with
   `tool_name = 'relationship_assert_fact'`, the stored arguments,
   `deduplication_key = 'external_mcp:<proposal_id>'`, deterministic `why`
   and `agent_summary` templates, `evidence = [{kind: "external_mcp",
   client: <cid label>, url_host: <scheme and host of evidence_url>,
   url_sha256: <SHA-256 of the full URL>}]` and
   `expires_at = now + 14 days`. The full evidence URL and the note stay only
   in `relationship.external_fact_proposals`, and the dashboard card renders
   them from there. [Observed] The Relationship butler loads the approvals
   module (`roster/relationship/butler.toml:168`), whose
   `list_pending_actions` and `show_pending_action` are LLM-callable
   (`src/butlers/modules/approvals/module.py:386-395`); keeping outside text
   out of `pending_actions` keeps it out of model context. In the same
   transaction it writes
   `relationship.fact_approval_context` with a park-time frozen report:
   `content_authority = 'third_party'` and `src = 'external_mcp:<proposal_id>'`,
   set server-side through `FactWriteContext`, never from arguments. Three
   further rules: **no standing rule may match** an external proposal (the
   importer parks through a path that skips rule matching, proven by a
   test); **no push**, so the approvals page is the queue and an outside
   client cannot page the owner (a test with `admission_enabled = true`
   asserts that no presentation other than `collapsed` exists for the
   action); and **`pending_actions.origin` is not
   widened** (it stays `NULL | 'prepared'`), so `external_mcp` lives only in
   fact provenance and avoids churn against `bu-cadcr1`'s approvals
   migration.
4. **Approve** (owner). Approval uses the existing one-tap path,
   `POST /api/approvals/actions/{id}/approve`, under the owner's passkey
   dashboard session with the exact displayed call. The approvals card uses
   a typed review, after the precedent in `review-parked-entity-merge-approvals`.
   It shows subject, predicate, object, the submitting client label and the
   evidence link as inert text, and never a raw tool-call preview. The owner
   may edit the proposal (RFC 0021). The executed digest is then the edited
   one. [Observed] The approve path records every manual approval in
   autonomy history, calls the promotion check (threshold 5) and honours
   `create_rule` (`src/butlers/modules/approvals/operations.py:285-320`).
   For an action whose deduplication key starts with `external_mcp:`, the
   approve path records no autonomy history, counts toward and creates no
   promotion suggestion, and refuses `create_rule=true`; the dashboard does not
   offer it. Otherwise five approved outside proposals of one shape would let
   outside clients steer approval policy for internal writes.
5. **Apply.** The executor calls `relationship_assert_fact` directly. No LLM
   is involved [Observed: `daemon.py:1831-1872`, `executor.py:319`]. The
   fact keeps `content_authority = 'third_party'`. Approval adds
   `confirmed_at` and `confirmation_source = 'approved_action'`, so the fact
   is verified, owner-endorsed and still attributed to an outside source. It
   never becomes a rule or an owner-profile fact.
   [Inferred] The existing `record_admitted_approval` hook refuses a fresh
   approval that has no owner-class admitted authority. The implementation
   packet must show that a third-party frozen report plus owner approval
   passes that hook, or amend the hook in a test-first change.
6. **Reconcile.** Each importer pass maps `pending_actions.status` to
   proposal state: `executed` becomes `applied`, `rejected` becomes
   `owner_rejected`, `expired` stays `expired`, and an executor failure
   becomes `apply_failed`. The `state` and `decided_at` columns replicate to
   the projection, so `fetch("proposal:<id>")` reports the outcome. Timeouts
   deny (`security.md:196`).
7. **Retention.** 30 days after a proposal reaches a terminal state, the
   importer clears its `note` and `evidence_url` and keeps the ids, state and
   timestamps. Audit lines carry no content (D15) and follow the log store's
   retention.

**Inbox hardening.** CHECK constraints bound every length. A `BEFORE
INSERT` trigger (a definer owned by `mcp_projection_owner`, not executable
directly) refuses new rows once 1,000 are undrained, capping a flood from a
compromised pod. The public role cannot read the inbox, so no client sees
another's proposals.

**Future auto-approval (not v1).** It is blocked by an owner decision of its
own, plus `bu-u5euwb.3` (the switch) and `bu-u5s4w8` (the runner). It would
be a bounded, offline batch: an RFC 0038 goal with origin `third_party` that
writes tainted `record` effects, with per-run and per-day caps, a predicate
subset no wider than v1's, a corroboration requirement (an existing source
agrees), reversibility by retraction, and no access to the request path. The
decision bead must also offer the non-LLM alternative: a
template-pinned standing rule on external proposals, created with step-up
(RFC 0038 D13). The owner may prefer that rule over any model.

### D14. Structural guarantees that no LLM runs

These hold by construction, not by convention. Each one has a test.

1. **No import route to a model.** A contract test fails if
   `butlers.mcp_public`, `butlers.capability_host` or `butlers.knowledge_read`
   imports, directly or transitively, `butlers.core.spawner`,
   `butlers.core.runtimes`, `butlers.modules.memory.embedding`, `anthropic`,
   `openai`, `google.genai`, `claude_agent_sdk`, `sentence_transformers`,
   `transformers`, or a switchboard, notify, route or delegate client.
2. **No network route to a model.** Egress allows only the PostgreSQL port
   of the instance that holds the projection, and DNS (D8).
3. **No write route that wakes reasoning.** The role cannot write
   `route_inbox`, `goal_inbox`, schedules, `ingestion_events` or captures; it
   can connect only to another database (D3), and its one writable table is read only by a
   deterministic job. Its contents reach no model context: `pending_actions`
   holds only the client label and a digest of the evidence URL (D13 step
   3). [Observed: grep] No LISTEN/NOTIFY wake path exists.
4. **No sampling or elicitation.** A test asserts the declared server
   capabilities are exactly `tools`.
5. **Deterministic importer and apply.** The importer is a `dispatch_mode =
   "job"` handler; approval execution calls the tool function directly.
6. **Allowlist pinning.** A test pins the registered tool set to the D10
   table by name and schema digest.

This is distinct from RFC 0038 D18 dashboard fast answers (`bu-u5euwb.28`),
which are a model-use kind. This surface uses no model.

### D15. DLP, canaries, audit and budgets

**Response DLP** runs on every tool result before it is serialized. It scans
text fields only; numeric money and measurement fields are skipped, which
limits false positives on amounts. Each text field is first normalized with
NFKC, and characters of Unicode categories Cc and Cf (controls, zero-width
and format characters) are removed, so full-width digits or zero-width joiners
cannot split a match.

- *Secret rules* (redacted and counted): Luhn-valid 13- to 19-digit runs,
  matched after removing spaces and dashes; IBANs passing mod-97; `AKIA…`,
  `ghp_…`, `xox…`, `sk-…` and JWT-shaped tokens; PEM blocks; URLs with
  `token`, `code`, `sig` or `key` query parameters, or with a path segment of
  20 or more token characters; high-entropy tokens (20 or more base64 or hex
  characters with at least 3.5 bits of entropy per character);
  password-reset and OTP phrasing next to a 4- to 8-digit code; a
  6-character alphanumeric booking code next to booking words (PNR,
  confirmation, booking, reservation); passport and national-id shapes next
  to document words; 8- to 17-digit account numbers next to account words.
- *Personal-contact rules* (redacted, not counted toward the drop
  threshold, so ordinary content still serves): email addresses, and phone
  numbers in E.164 and common local shapes.

A match is redacted to `[redacted:<rule>]`. A response with more than 5
counted redactions is dropped whole, with the D17 error. Labels in Butlers
come from LLM writers [Observed: `store_fact` defaults to `normal`], so the
scan is load-bearing, not cosmetic.

**Canaries.** Unique fake tokens are planted in excluded sources: a
`butler_secrets` row, a `secured` `entity_info` row, a `pii` memory fact, a
`restricted` Chronicler override, a fake IBAN in finance `metadata`, a fake
code in `travel.legs.confirmation_number`, and a fake channel value in
`public.entities.metadata.source_value`. The pod holds their values. A canary
in a response blocks that response, latches every tool except `describe` off
in-process until restart, and fires a critical alert. The CI drift check (D4
check 4) and a 30-minute projection probe also search the projection for
them; a canary in the projection means the manifest is wrong and is handled
by the leak runbook (D16).

**Audit.** Each call writes one structured JSON log line to stdout (into
Loki): `request_id`, `jti`, `sub`, the `cid` label, `tool`, argument SHA-256,
`outcome`, `rows`, `bytes`, `redactions`, `latency_ms`, and the opaque ids of
returned items. Never bodies, argument values, assertions, bearers or
cursors. Prometheus counters mirror it. There is no audit table: the pod has
no write grant except the inbox.

Bearer refusals (HTTP 401, D6) are counted by a Prometheus counter with no
request content, so a bearer probe is visible even though it never reaches
the audit line.

**Budgets.** In-process token buckets (one replica), keyed per client so one
steered client cannot spend another's budget: 60 calls per minute per
`(sub, cid)` on any tool; on `finance_ledger` and `health_series`, 20 calls
per minute and 20,000 returned rows per day per `(sub, cid, tool)`; 20
`propose_fact` per day per `(sub, cid)` (the importer enforces the same cap
independently); 600 calls per minute for the whole server. Exhaustion returns
the D17 error with `retry_after_s`. Defaults are configuration, measured after
launch. [Observed] The gateway's per-IP `EDGE_LIMIT` keys on the vendors'
shared egress IPs, so it is not a per-user budget.

**Residual risk: steered clients.** [Inferred] The most likely real-world
leak is not a forged call but a genuine one. ChatGPT agents and claude.ai
read attacker-controlled pages; injected instructions can make the client
call `finance_ledger`, `health_series` or `entity_profile` under the owner's
valid session and then send the results out through the client's own tools.
Every layer past Cloudflare authenticates the owner's session, not the
owner's intent, and Butlers cannot see the client's intent. Per-client
budgets, volume alerts (D16), audit and per-domain kill switches bound and
detect this; they do not prevent it. OD3 accepts the exposure; this RFC and
`security.md` record it as a residual risk.

### D16. Kill switches and alerting

**Switches** (the first is the normal owner path; all are for incident
response; the rest need no replication):

| Switch | Who flips it, and how | Effect | Depends on replication |
|---|---|---|---|
| `mcp_surface.controls` row per domain, plus `all` | Owner, on the dashboard Settings page under a passkey session (dashboard-api owner route), or `butlers public-mcp disable <domain>` | Domain tools return `domain_disabled`; the importer reads the same rows on the primary and stops parking while `proposals` or `all` is off | Yes, for the public server. Bounded by the 15-minute stale cutoff in D17. |
| `disabled_domains` file in a ConfigMap in `butlers-public-<env>` | Owner, with `kubectl` or Helm (break glass) | Same on the public server only; re-read every 10 s | No |
| In-process latch | The canary detector | Every tool except `describe` off until the pod restarts | No |
| Scale the Deployment to 0, or delete the namespace's allow policy | Owner, `kubectl` | The surface is unreachable | No |
| Worker `MAINTENANCE`, or remove `butlers` from `MCP_SERVERS` | Owner (homelab) | The gateway answers 503 or 404 | No |

The owner accepted live `dev` data from the first client connection (OD12),
so these switches are the incident control from the first call.

**Leak runbook.** For a canary hit or a wrongly published column: disable
the domain (or `all`), narrow the manifest, let the migration Job's reconcile
narrow the publication, run the owner step to purge and re-copy (D3, D4),
confirm the canary probe is clean, then re-enable. The surface refuses from
the moment the publication narrows until the owner step completes (D17).

**Alerting (OD11).** Alerts never go through the Switchboard or Messenger,
because they must survive a Butlers outage and must never start a model.

- **Conditions.** Prometheus alerting rules are the single source of every
  condition, and keep their labels (domain, client label, slot name) for
  diagnosis. Critical: a canary hit, and the canary latch while it holds;
  slot retained WAL over 50 % of `max_slot_wal_keep_size`; any
  logical slot on the primary other than the subscription's slot and its
  table-sync slots, or any logical slot whose plugin is not `pgoutput`
  (D9 control 3, which defines the allowance); an owner step pending after a
  narrowing for more
  than 24 hours. Warning: an owner step pending, of either direction, from
  the moment the server sees it (D17); any DLP drop or more than 20 redactions per
  hour; more than 10 assertion failures in 5 minutes; more than 20 bearer
  refusals in 5 minutes; more than 2,000 finance or health rows served in an
  hour, or one `(sub, cid)` reading more than 25 % of a domain's published
  rows in a day; replication lag (the `as_of` age) over 10 minutes; slot
  inactive over 10 minutes; inbox undrained above 500; any `apply_failed`.
- **Owner-step pending.** The server exports a gauge,
  `butlers_public_mcp_owner_step_pending`, labelled `change="narrowing"` or
  `change="widening"`, that is 1 while it refuses with
  `projection_apply_pending` (D17). The warning rule fires on either label.
  The critical rule fires on `change="narrowing"` held for 24 hours. The bound
  is a choice: the surface already refuses, so what remains is rows sitting
  in `butlers_public` that the manifest no longer allows, which a stolen
  `mcp_public_ro` password could still read through the views. A day leaves
  room for a manual step without letting it be forgotten.
- **Flags.** Two recording rules collapse the conditions into one series per
  severity: `butlers_public_mcp:critical_ok` and
  `butlers_public_mcp:warning_ok`, each 1 when no rule of its severity is
  firing and 0 when any is.
- **Delivery.** The owner's existing homelab Uptime Kuma polls each flag with
  one `json-query` monitor, declared in the homelab's
  `ansible/roles/proxmox/files/uptimekuma-monitors.toml` and synced by
  Ansible, and its existing Telegram notification delivers. Each monitor
  follows the homelab's established pattern: a Prometheus query URL ending in
  `query=<flag> == bool 1`, `jsonPath = "data.result[0].value[1]"` and
  `expectedValue = "1"`. An empty result or an unreachable Prometheus also
  turns the monitor DOWN and notifies, so each monitor doubles as a dead-man
  check on the alert path.
- **Latency.** Kuma's poll interval bounds detection latency, on top of each
  rule's own window. The critical monitor uses `interval = 60`; the warning
  monitor may poll less often.
- **No new ingress.** Nothing is added to `butlers-public-<env>` for alerting:
  Prometheus serves the flags from what it scrapes on the server's metrics
  port, which the D8 monitoring allow already admits. [Inferred] That
  Prometheus scrapes the Butlers namespaces is not verified from this
  repository: the `butlers` chart ships scrape annotations
  and an opt-in PodMonitor (`deploy/helm/butlers/values.yaml`,
  `connectorMetrics`), and the public chart declares its metrics port the
  same way.

### D17. Freshness, failure modes and the error shape

**Freshness.** `as_of` is `mcp_read.replication_as_of()`, read once per
request. Between 2 and 15 minutes old, results are served with `as_of` and,
where the output shape allows, `sources_degraded: [{domain, reason:
"lagging"}]`. Older than 15 minutes, every domain reports `stale` and the
tools refuse (`describe` still answers), so a kill switch never goes unseen
for more than 15 minutes even if replication stalls. A NULL
`replication_as_of()`, as when the subscription is missing or has never
confirmed a position, counts as stale.

**Owner step pending: fail closed (OD14).** The server ships with its
manifest and that manifest's digest. Once per request it reads
`mcp_read.manifest_applied`, which shows the digest and canonical manifest
the last complete owner step recorded and whether the initial copy is
complete. The copy counts as complete only when the subscription
`butlers_public_sub` exists and is enabled and the set of its relations in
state `r` (`pg_subscription_rel.srsubstate`) equals the applied manifest's
table set, so a missing, disabled or empty subscription is never complete.
The owner step is pending when no applied record exists, when the applied
digest differs from the shipped one in either direction (widening or
narrowing), or when the copy is not complete. While it is pending, every tool except
`describe` refuses with `projection_apply_pending`, and the
`butlers_public_mcp_owner_step_pending` gauge is 1 (D16). The server labels
the change by comparing the applied manifest with its own, using the same
deterministic function that the owner step uses for its rebuild decision: it
is `narrowing` when any applied table, column or row-filter value is absent
from the shipped manifest, or the shipped manifest filters a column the
applied one did not, and `widening` otherwise. When the digests match again
and the copy is complete, the server runs the projection canary probe (D15)
once and serves only if it is clean. A pod shipping a narrowed manifest
therefore never serves rows the owner step has not purged, even when the
migration Job has already narrowed the publication. Two limits remain. A pod
still shipping the old manifest serves the old rows until the new digest
replicates and turns its refusal into `projection_version_mismatch`;
reconcile writes the digest in the same transaction as the publication
change (D4), so that window is the replication lag, capped by the 15-minute
stale rule, and the leak runbook starts with the kill switch (D16). And the
guarantee assumes that no one writes `mcp_local.manifest_applied` but the
owner step: the `mcp_subscription_owner` password could forge the applied
digest over TCP (D9, open question 5).

**Error shape.** A tool-level failure is an MCP tool result with
`isError: true` and `structuredContent`:

```json
{"error": {"code": "domain_disabled", "domain": "finance",
           "retryable": false, "retry_after_s": null,
           "message": "This data source is turned off by the owner."}}
```

The codes are `domain_disabled`, `projection_stale`,
`projection_version_mismatch`, `projection_apply_pending`,
`budget_exhausted`, `invalid_argument`, `not_found`, `too_large`, `blocked`
(DLP or canary) and `unavailable`. `projection_apply_pending` is
`retryable: true` with `retry_after_s: null`, because it clears only when the
owner acts. Auth failures are JSON-RPC `-32001`, so they never look like tool
data. Messages are fixed strings that never echo arguments.

**Precedence.** When several refusals apply, the first in this order wins:
`blocked` (the canary latch), `invalid_argument`, `budget_exhausted`,
`unavailable`, `projection_version_mismatch`, `projection_apply_pending`,
`projection_stale`, `domain_disabled`, then `not_found` and `too_large`.
Deploy skew wins over a pending owner step because the owner step's guard
refuses to run until the migration Job has reconciled (D3).
In `describe.domains`, a domain that any switch layer the pod can see
disables reports `disabled`;
otherwise a stale projection reports `stale`.

| Failure | Behavior | Primary affected? |
|---|---|---|
| `postgres-<env>` down | The projection is down with the primary (D3): `unavailable`; `describe` still answers | Yes: it is the primary's own outage |
| Projection database alone unavailable (connection limit reached, or the database dropped and re-created) | `unavailable`; `describe` still answers | No |
| Replication lagging | Served with `as_of` and degraded entries | No |
| Replication stalled | `projection_stale` after 15 minutes; WAL alert | Only WAL growth, bounded by the cap |
| Slot invalidated by the cap | The applied record still matches, so `projection_stale` after 15 minutes, with the slot and lag alerts; the owner reruns the owner step, which rebuilds the slot and the subscription and re-copies (D3), and `projection_apply_pending` holds from the moment it deletes the applied record until the copy completes | No |
| Manifest digest mismatch (deploy skew) | `projection_version_mismatch` on every tool | No |
| Owner step pending (first sync, manifest change, a rebuild the owner step has started, or a run that stopped midway) | `projection_apply_pending` on every tool except `describe`; warning flag down; critical after 24 hours if narrowing | No |
| Source migration breaks a published column | CI fails in that PR; at runtime, replication errors and goes stale | No |
| Gateway down | Clients cannot connect | No |
| Importer down | Proposals stay `received`; inbox alert | No |
| Public pod compromised | It can read published projection data and insert capped inbox rows. It reaches the shared instance's PostgreSQL port, where `pg_hba` refuses every database but `butlers_public` (D3), and no publisher credential exists to hold (D9). | Shares the instance's CPU and connections, bounded by the connection limit and the 2-second timeout |
| Client steered by injected content | Validly signed calls read enabled domains; budgets, volume alerts, audit and kill switches bound and detect it (D15) | No |

No Butlers daemon depends on the public surface, the projection or the
gateway (vision anti-pattern: an always-on internet dependency).

## Relation to other RFCs

**RFC 0038 (goal runtime).** This RFC adds an in-place amendment to
`generalist-lead-focused-delegation`'s `capability-authorization` spec. It
takes no new D-number (D20 is claimed by `bu-6qaju2.1`; D21 is proposed by
`bu-l8y3fn`). New requirements start at REQ-capability-authorization-022.
The amendment must land before `bu-2ckast` archives that change.

- **D5.** The public surface admits no goal. A future goal triggered by an
  external proposal (the auto-approval batch) has origin `third_party`. An
  inbox row is not admission input.
- **D6.** An applied external proposal is stored with
  `content_authority = third_party` and `src = external_mcp:<id>`, which
  makes it a stamped, non-owner-authority record for every later reader.
  After `bu-pkuny2`, the apply also stamps taint. After `bu-dhloi1.6`, it
  carries the S1 principal kind `external` with an opaque registered
  identity (a digest of `sub` and the client label), and the manifest
  projects the labels.
- **D7.** The public server is not a service-plane caller. It never holds
  or receives a broker assertion. Its Worker assertion uses a distinct
  `typ` and keyring, and no domain accepts it.
- **D13.** A deterministic domain job (the importer) may still park
  pending actions of its own domain's tools after the switch, from
  non-goal input, with no goal lineage (it records no goal origin). Such
  parked actions are approvable only per event, never by a standing rule,
  and never feed standing-rule promotion. The approve call follows the
  dashboard service-plane path in `bu-2c1g7v`'s inventory.

The public server calls no domain capability. Any future read through domain
capabilities needs a new owner decision and an amendment to this RFC and to
vision.md Rule 3.

**RFC 0031 (public entity graph projection).** `graph_neighbors` and
`graph_path` read the replicated `public.entity_graph_edges` with parity to
REQ-entity-graph-003/005/006/009, including count-only withheld stubs. Value
depends on the graph being fed: [Observed: `bu-h40h2b.7`] about 7 edges
over 626 entities. The tools are correct but sparse until that lands.

**RFC 0030 (system-plane read exception).** Its column-allowlist "sixth
guardrail" and its departure from the batch criterion are the template for
D4 and for the architecture.md amendment. RFC 0030 itself is not reused:
domain questions are out of its scope.

**RFC 0027 (tool surface discovery).** No `search_tools` or `invoke_tool`
meta-tools, no generic `query`. Typed tools only.

**RFC 0010 (cross-butler briefing exception).** Its MUST NOT list forbids
writes and real-time queries. This RFC is a separately named exception,
not a reuse of RFC 0010; RFC 0010's item 3 now names it as the third
on-demand carve-out.

**RFC 0008 (deployment network security).** Its Kubernetes-ingress
amendment (2026-11-04) records that on k3s the localhost binding rule is met
by default-deny NetworkPolicy, and that this RFC's server is the only
internet-reachable workload.

**Ambient projection grants** (`add-ambient-projection-grants`,
REQ-ambient-projections-001/002) are precedent for digest-only secrets,
constant-time comparison and non-disclosing refusals. They are not the token
model: they have no expiry, they verify against the live database, and they
accept query-string tokens.

## Rollout and owner acts

| Phase | Work | Gate to the next phase |
|---|---|---|
| P0 Preconditions | Default-deny ingress in `butlers-<env>` (new bead, child of `bu-viat6h`); this RFC, the doctrine sweep and the OpenSpec change; the RFC 0038 amendment | Merged; ingress contract test green |
| P1 Projection | Manifest, `reconcile`, `owner-sql`, replica-identity migrations, Chronicler `*_public` tables, the `mcp_surface` schema, roles and init-db exceptions and grants, drift and grant contract tests, the no-credential publisher checks | Owner acts 9 and 10 run; initial sync matches the manifest in dev; canaries absent |
| P2 Dark server | `butlers.knowledge_read`, `capability_host`, `mcp_public`, the chart and the namespace policies. Smoke test from a test pod in `mcp` with a test keyring. | Assertion, bearer, DLP and kill-switch tests green; import-ban test green |
| P3 Gateway | Owner acts 4-8 below (1 is the chart's; 2 and 3 are done on dev); connect claude.ai, then ChatGPT | First real calls audited; each flag forced to 0 turns its Kuma monitor DOWN and the Telegram notification arrives; live NetworkPolicy check green |
| P4 Finance and health tools | Same server, enabled on deploy (OD3) | DLP false-positive rate reviewed after one week |
| P5 Proposals | Inbox, importer job, typed approvals card, apply | An end-to-end proposal reaches `applied` and `owner_rejected` in dev |
| P6 Successors | Taint and labels; the D19 card; the auto-approval decision | See [Dependencies](#dependencies-and-queueing) |

**Owner acts (homelab side, OD5).** Numbers are stable, because other
documents cite them. Acts 2 and 3 are done on dev by homelab PR
Tzeusy/homelab#40 [Observed: homelab report, 2026-11-04].
1. *Moved to the chart.* The Butlers chart `deploy/helm/butlers-public-mcp`
   creates namespace `butlers-public-<env>` and the ExternalSecret that
   reads `MCP_PUBLIC_RO_DB_PASSWORD` (D8 item 1). No owner act remains here.
2. *Done on dev.* CNPG `postgres-<env>` (D3, D9; no new cluster, OD10):
   - database `butlers_public`, owned by `mcp_projection_owner`;
   - the managed roles `mcp_projection_owner` (NOLOGIN), `mcp_public_ro`
     (LOGIN NOINHERIT, connection limit 10), `mcp_proposals_reader`,
     `mcp_subscription_owner` (LOGIN, member of `pg_create_subscription` and
     `mcp_projection_owner`) and `butlers_projection_publisher` (LOGIN
     REPLICATION NOINHERIT, no password), none of them superuser, `CREATEDB`
     or `CREATEROLE`;
   - the D3 `pg_hba` lines for `mcp_public_ro` and `mcp_proposals_reader` on
     `10.42.0.0/24`, with the three reject lines including the publisher's,
     and no `replication` entry for the publisher;
   - the `local` `pg_ident` map line `local postgres
     butlers_projection_publisher`, so the publisher logs in by local peer
     authentication only (D9, OD14);
   - `max_slot_wal_keep_size = 10GB`; `max_replication_slots` (32) and
     `max_wal_senders` (10) left unchanged with headroom;
   - the password Secrets and Bitwarden items named in D9.
3. *Done on dev (nothing to add).* NetworkPolicy on `postgres-<env>`: only if
   a default-deny ingress policy selects the instance pods, add one allow for
   the public server pod on 5432 and nothing else (D8 item 3). None selects
   the `postgres-dev` pods, so no policy was added.
4. Worker: generate the Ed25519 key pair (private key as a Worker secret);
   add assertion signing (sign the forwarded bytes, never streamed bodies;
   `path` is the query string only); deploy; hand the public key and `kid` to
   the Butlers chart values.
5. `mcp` release `butlers` of `helm/mcp_server`: filter-proxy only,
   `UPSTREAM_URL` at the public Service, `UPSTREAM_BEARER` from an
   ExternalSecret on the **dev** store (not the chart default
   `bitwarden-secretsmanager-prod`), `FIXES` empty, label
   `mcp.tze.how/exposed=true`, egress only to the public pod's port.
6. Add `butlers` to `MCP_SERVERS`, keep `KNOWN_CLIENT_DOMAINS` at
   `claude.ai,chatgpt.com`, redeploy the Worker.
7. Uptime Kuma (OD11): add two `json-query` monitors to
   `ansible/roles/proxmox/files/uptimekuma-monitors.toml`, one per D16 flag
   (`butlers_public_mcp:critical_ok` with `interval = 60`, and
   `butlers_public_mcp:warning_ok`), on the existing Telegram notification,
   and let Ansible sync them.
8. Connect and consent in claude.ai and ChatGPT. This exposes live data from
   the first connection, because `butlers-dev` is the live system
   [Observed]. The owner accepted that (OD12); the D16 kill switches remain
   the incident control.
9. Rerun `scripts/init-db.sql` through `kubectl exec -i -n postgres-<env>
   <instance> -- psql -X -U postgres -d butlers -f -` once it carries the
   publisher's `CONNECT` and `SELECT` grants and `ALTER ROLE mcp_public_ro
   SET statement_timeout = '2s'` (D9).
10. The owner step (D3, OD14), through the same path: once at the first
    sync, once after each manifest change the migration Job has applied, and
    once after each slot invalidation. This is the one recurring owner act.
    Until it runs after the first sync or a manifest change, the public
    server refuses with `projection_apply_pending` and the warning flag is
    down; after an invalidation it refuses with `projection_stale` (D16,
    D17).

## Dependencies and queueing

Per OD9, a blocks edge exists only where a real dependency exists.
[Observed] No live bead duplicates this work (dedupe sweep of 2026-11-04,
781 live beads).

**Proceed now, with no RFC 0038 block:** the P0 ingress precondition (a
security fix, which the freeze admits); this RFC and the amendment (the
amendment blocks `bu-2ckast`; sequence it with `bu-6qaju2.1` and `bu-krc9su`
so `security.md` changes once); the projection (P1, soft dependency on
`bu-dss7he.6` for the grant manifest); the server and assertion (P2, related
to `bu-cadcr1` only for the shared codec); finance and health (P4); and
proposals on today's approvals path (P5, built on `/api/approvals/actions`;
coordinate with `bu-dss7he.5`).

**File now, blocked by:** "External-fact apply carries taint and the S1
`external` principal labels; the manifest projects labels" (`bu-pkuny2`,
`bu-dhloi1.6`); "Proposals card conforms to the D19 approvals dossier"
(`bu-vvsfi8`); "Bounded auto-approval batch" (`bu-u5euwb.3`, `bu-u5s4w8`, and
a new owner-decision bead that also offers the standing-rule alternative).

**Related, no edge:** `bu-cadcr1` (merge churn only: do not widen the
`pending_actions` origin CHECK); `bu-2c1g7v` (note that external-proposal
approval uses the same `/api/approvals/actions` path); `bu-dhloi1.15` (the
new workload needs an egress profile); `bu-viat6h.6` and `bu-kiuh8v`
(NetworkPolicy union trap); `bu-pnqplh` (owner-only CNPG values precedent);
`bu-viat6h.9`, `bu-mc01y5.7`, `bu-kqnum.17` (NOREPLICATION guards);
`bu-h40h2b.7`, `bu-ys9hdx.3`/`.4` (graph and identity sources); `bu-dhloi1.9`
(perception timeline, a possible later `timeline` source); `bu-3vm4xa`
(health retention and erasure propagate by replication DELETE);
`bu-s11n0s.7` (location retention for `trips`); `bu-6ail6p` (region-scope
registry); `bu-jnhxc5` (key-custody pattern); `bu-u5euwb.28` (contrast: a
model lane); `bu-jv1moa` (Dots programme note).

**Do not block on:** M5 (`bu-6yqs2x`), M7 (`bu-4c7nvp.1`), the closure of
the umbrella `bu-4c7nvp`, `bu-bda935`, `bu-krc9su` adoption, or
`bu-atc3xi`.

## Alternatives considered

- **Paste context by hand.** Loses continuous ingestion and moves data to
  vendors with no provenance, minimization or audit.
- **Expose butler daemons' MCP behind filter-proxy `DENY_TOOLS`.** A
  denylist over about 505 tools on unauthenticated daemons, LLM-starting
  tools one typo away, and the `resource_owner → human` approval hazard.
- **A butler (LLM) fronting requests.** OD8: cost, recursion, client-side
  prompt injection.
- **Views on the primary** (OD1, D2); **tier servers or passkey unlocks**
  (OD3, D5).
- **Writing into `public.captures`.** After the cutover captures are
  goal-admission input (RFC 0038) and would start an LLM goal, which OD8
  forbids. A generic `capture`/`note` tool is out of scope: Obsidian owns
  prose writes.
- **Semantic search in v1.** Deferred (D11).

## Open questions

Genuine questions only. Every other choice above is decided and reversible.

1. **Finance breadth.** `last_four` and balance snapshots are excluded by
   default; widening is a one-line manifest edit.
2. **Health profile breadth.** Medications and conditions without notes or
   clinicians is the default; allergies and visits wait until asked for.
3. **Prod rollout.** Rollout targets `dev` (the live system); prod waits for
   a separate owner act. Prod's version is no longer open (closed below).
4. **Knowledge predicate allowlist.** The v1 allowlist is written from the
   predicate registry at implementation time (D4); the owner may widen it.
5. **Subscription-owner login path (OD15).** `mcp_subscription_owner` keeps
   `LOGIN` and three password copies, and no `pg_hba` line rejects it.
   [Observed: `postgres-dev`, 2026-11-04] Its password reaches
   `butlers_public` and `postgres` over TCP, not `butlers`, which grants it
   no `CONNECT`. On `butlers_public` it acts as the database owner's member,
   so it can rewrite the projection and forge the applied digest that the
   D17 fail-closed rule trusts (D9). A `host all mcp_subscription_owner all
   reject` line would close the TCP path without removing the role, and
   would let the D17 guarantee hold without assuming the password stays
   unused. That is a homelab choice beyond OD15, so it returns to the owner,
   with the reject line recommended.

Closed on 2026-11-04: projection placement (a second database on the existing
cluster, OD10, D3) and the alert path (Uptime Kuma polling Prometheus flags,
OD11, D16). The Chronicler materialization shape is decided: source-owned
`episodes_public` and `rollups_public` tables (D2). Prod's PostgreSQL version
is closed [Observed: homelab report]: prod is PG 17.8 with
`wal_level=logical`, one instance and no standbys, like dev (D3). The
publisher credential is closed by OD14: there is none (D9). Whether a
NetworkPolicy selects the `postgres-dev` pods is closed [Observed]: none
does (D8 item 3).

## Traceability

| This RFC | Spec capability (in `add-public-mcp-read-surface`) |
|---|---|
| D1, D10, D11, D12, D14, D17 | `public-mcp-surface` |
| D5, D6, D7, D8, D15 | `public-mcp-authorization` |
| D16 | `public-mcp-authorization`; ADDED `dashboard-settings-console` REQ-001 and MODIFIED Settings Console Page (REQ-002) |
| D2, D3, D4, D9 | `public-mcp-projection` |
| D3 owner step, D4 reconcile split, D17 apply digest (OD14) | `public-mcp-projection` REQ-003 and ADDED REQ-015; `public-mcp-surface` REQ-013 (`projection_apply_pending`); `public-mcp-authorization` REQ-014 (pending-step alerts) |
| D9 publisher with no credential (OD14), subscription owner (OD15) | `database-security` REQ-016; `public-mcp-projection` REQ-008 and REQ-009 |
| D13 | `public-mcp-proposals`; ADDED `module-approvals` REQ-010, `dashboard-approvals` REQ-904, `relationship-facts` REQ-011 |
| D8 precondition | `deployment-hardening` ADDED REQ-013 |
| D9 | `database-security` ADDED REQ-016 |
| RFC 0038 amendment | `capability-authorization` ADDED REQ-022 and REQ-023 in `generalist-lead-focused-delegation` |
