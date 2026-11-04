# Adopted doctrine amendment: public MCP read surface

**Status:** Doctrine change accepted by the owner on 2026-11-04 (OD2). The
design is [RFC 0039](../rfcs/0039-public-mcp-read-surface.md); conforming text
is adopted once the PR's independent conformance review passes. The four
items the conformance review returned to the owner were answered the same day
(OD10 to OD13) and are adopted with the rest. OD14 and OD15, recorded after
homelab PR Tzeusy/homelab#40 applied the CNPG owner acts to `postgres-dev`,
are adopted the same way.
**Date:** 2026-11-04
**Baseline:** `9f723b9468`
**OpenSpec change:** `add-public-mcp-read-surface`

## Decision

Butlers admits one deterministic, internet-reachable, read-only MCP server
through which the owner's own outside AI clients (ChatGPT, including its Dots
agent, and claude.ai) read Butlers data on demand. The server reads only a
separate projection database that one-way PostgreSQL logical replication fills
from one checked-in allowlist manifest; the projection is a second database
on the existing cluster, behind `pg_hba` and grants (OD10). Its only write is
`propose_fact`, an inbox row that changes nothing until the owner approves it
per event on the dashboard approvals page. It is a third named exception to vision.md
Non-Negotiable Rule 3. It does not generalize.

## Owner decisions (verbatim)

Recorded by the owner (tze) on 2026-11-04:

1. **Projection database, not views.** Reads are served from a separate projection database fed one-way by
   Postgres logical replication (publication column lists + row filters generated from one checked-in
   allowlist manifest). Owner is "slightly worried about maintenance overhead" — the design must minimise
   upkeep (single manifest, generated publication/DDL, CI drift checks; deterministic exporter only as a
   fallback for sources that cannot be row-filtered, e.g. chronicler corrected views).
2. **Doctrine change accepted.** Owner: "OK with the doctrine change; I'm aware this is a different positioning
   from our initial asks, but think there's significant value to be added here in integration with outside
   products." Conforming doctrine/RFC/spec text is approved under the owner's standing rule (independent
   conformance review in the PR; no exact-text sign-off). Text that goes beyond these decisions returns to the owner.
3. **Finance and health ARE exposed, on the SAME single gateway server and process as everything else.**
   NO separate tier servers (no butlers-finance / butlers-health), NO time-boxed or manual unlocking, NO
   passkey elevation step. Owner: "have them all follow the same gateway process with no manual unlocking needed."
   Owner also: "need to be very sure that the MCP layer is authorized securely (cloudflare + oauth becomes our only
   layer of defense?)" -> defense in depth beyond Cloudflare is required: Worker-signed per-call assertion verified
   home-side, gateway bearer, dedicated default-deny namespace with egress only to the projection DB, read-only
   DB role (INSERT-only on the proposals inbox), credentials never replicated, response DLP + canaries, audit,
   per-tool budgets, per-domain kill switches (finance, health, ...) for incident response (not routine gating).
4. **Generalized read-only tools + an explicit `propose_fact`.** Proposed triples go to manual review/approval on
   the dashboard approvals page (https://tzeusy.parrot-hen.ts.net/butlers-dev/approvals). Approved proposals are
   applied by deterministic code. "As this matures, we can eventually shift to automatic LLM approval" — that is a
   later, bounded, offline batch, never on the MCP request path; it needs its own owner decision before enablement.
5. **Gateway-side setup is the owner's** (homelab: mcp release, ExternalSecret, MCP_SERVERS entry, Worker signing
   key + deploy, namespace, CNPG replication role/slot cap). Not blocking; record as owner acts.
6. **ChatGPT Dots can access custom MCPs** (ChatGPT Pro, >= $100 tiers). The ChatGPT search/fetch contract is v1.
7. **Excel deferred.** Assume claude.ai and ChatGPT clients have MCP enabled.
8. **No LLM in the loop** for MCP calls: fully systematic/deterministic, no butler fronting requests, no recursive
   LLM-to-LLM, to avoid runaway costs.
9. **Dedupe and queue:** queue after super-butler (RFC 0038) work only where a real dependency exists.

Recorded by the owner (tze) later on 2026-11-04, answering the four items the
conformance review returned (see [Answered owner items](#answered-owner-items)):

10. **Projection placement: a second database on the existing cluster `postgres-<env>`, not a separate CNPG
    cluster.** The owner weighed a separate cluster's stronger isolation against its extra upkeep and chose
    the second database. Isolation rests on the database layer (`pg_hba` and grants), not the network.
11. **Alert delivery: the existing homelab Uptime Kuma polls a flag; no new Alertmanager receiver.** Prometheus
    rules stay the single source of the conditions and collapse into flag series; Kuma `json-query` monitors
    (declared in homelab `uptimekuma-monitors.toml`, synced by Ansible) poll them and notify on Kuma's
    existing Telegram notification.
12. **Live dev data from the first client connection: accepted.** Kill switches remain the incident control.
13. **Upkeep of the five source-owned materializations: accepted** (four trigger-maintained, plus
    Chronicler's writer and sweep), with the parity tests and CI drift checks as the mitigation.

Recorded by the owner (tze) later on 2026-11-04, after a homelab session applied homelab PR
Tzeusy/homelab#40 to `postgres-dev` and reported the result (see [Homelab facts](#homelab-facts)):

14. **Adopt the no-credential publisher.** The publisher (`butlers_projection_publisher`) logs in only
    by local peer authentication through the CNPG `local` `pg_ident` map. No password or certificate
    exists, and TCP logins as the publisher are rejected. The slot, the subscription (created by the
    superuser with `password_required = false`, `create_slot = false` and conninfo on the local socket,
    then owned by `mcp_subscription_owner`), `ALTER SUBSCRIPTION ... REFRESH PUBLICATION` and the
    narrowing purge on the subscriber become one owner step. Butlers generates one idempotent SQL file
    for it per manifest change, and the owner pipes that file through the same
    `kubectl exec ... psql -U postgres` path as `init-db.sql`. Publication DDL in `butlers` stays with
    the migration user in the migration Job. The public server must fail closed while the owner step
    is pending, so a narrowed manifest can never keep serving rows that should have been purged.
15. **Keep `mcp_subscription_owner` as created** (`LOGIN`, with its three password copies). The owner
    chose to keep it, though OD14 means no Butlers workflow needs it to log in.
16. **No `pg_hba` reject line for `mcp_subscription_owner`** (RFC 0039 open question 5, answered
    2026-11-04: "Leave it open"). The owner accepts that its password can log in over TCP to
    `butlers_public` and `postgres`, where it could rewrite the projection and forge the applied digest.
    The D17 fail-closed guarantee assumes that password stays unused.

## Homelab facts

Reported by the homelab session for homelab PR Tzeusy/homelab#40 (applied with
`make helm_install_dev`), and partly re-checked by read-only queries on
`postgres-dev-1`. RFC 0039 cites them where they apply.

- Dev and prod are both PG 17.8 with `wal_level=logical`, one instance and
  no standbys. This closes the RFC's open question on prod's version.
- `max_slot_wal_keep_size = 10GB` (verified live); `max_replication_slots`
  (32) and `max_wal_senders` (10) are unchanged.
- Dev and prod share one 1.4T disk, 90 % full with 144G free; the PVC sizes
  are not enforced.
- `pg_hba` admits `mcp_public_ro` and `mcp_proposals_reader` to
  `butlers_public` only, from the single-node pod range `10.42.0.0/24`, and
  rejects them and the publisher on every other TCP path (verified live).
- The CNPG `local` `pg_ident` map holds exactly `local postgres postgres` and
  `local postgres butlers_projection_publisher` (verified live), so OS user
  `postgres` inside an instance pod can log in as the publisher over the
  local socket (`/controller/run`) by peer authentication. No password or
  certificate exists for the publisher, and every TCP login as it is
  rejected. This is the fact OD14 rests on.
- No NetworkPolicy selects the `postgres-dev` pods, so none was added.
- Database `butlers_public` exists, owned by `mcp_projection_owner`.
- CNPG managed roles, all reconciled, none superuser, none with `CREATEDB` or
  `CREATEROLE`: `mcp_projection_owner` (`NOLOGIN`); `mcp_public_ro` (`LOGIN
  NOINHERIT`, connection limit 10); `mcp_proposals_reader` (`LOGIN`);
  `mcp_subscription_owner` (`LOGIN`, member of `pg_create_subscription` and
  `mcp_projection_owner`); `butlers_projection_publisher` (`LOGIN
  REPLICATION NOINHERIT`, no password, no `CONNECT` on `butlers` yet).
- CNPG role specs cannot set `statement_timeout`, so `scripts/init-db.sql`
  sets it for `mcp_public_ro`.
- Secrets, with keys `username` and `password`: in `postgres-dev`,
  `postgres-mcp-public-ro-user`, `postgres-mcp-proposals-reader-user` and
  `postgres-mcp-subscription-owner-user` (the first two are the CNPG
  `passwordSecret`s); in `butlers-dev`, `postgres-mcp-proposals-reader-user`
  and `postgres-mcp-subscription-owner-user`. Bitwarden dev items:
  `MCP_PUBLIC_RO_DB_PASSWORD`, `MCP_PROPOSALS_READER_DB_PASSWORD` and
  `MCP_SUBSCRIPTION_OWNER_DB_PASSWORD`.
- The superuser path is `kubectl exec -i -n postgres-dev postgres-dev-1 --
  psql -X -U postgres -d butlers -f - < scripts/init-db.sql`, peer
  authentication over the local socket with no password; it still works
  after the change (verified live).
- Namespace `butlers-public-dev` does not exist; the Butlers chart creates it.

Re-checked by the Butlers session on 2026-11-04 with read-only queries on
`postgres-dev-1`, during the conformance review of OD14:

- The `butlers` database ACL grants `CONNECT` to named roles only, with no
  PUBLIC entry, so the publisher, `mcp_public_ro` and
  `mcp_subscription_owner` have no `CONNECT` on `butlers`. `butlers_public`
  and `postgres` still have the default ACL.
- The publisher holds `USAGE` on no Butlers schema, and no role is a member
  of it. `mcp_subscription_owner` inherits `mcp_projection_owner`.

## Conditions of the exception

The exception holds only while all four conditions hold. If any condition
stops holding, the exception lapses, and the surface must be disabled until a
new owner-approved doctrine and RFC decision restores or replaces it.

1. **No credential is reachable.** The projection never contains a credential,
   and the public server holds none for the primary database.
2. **No Butlers model runs on the request path.** No session, spawner, runtime,
   model SDK or Switchboard client is reachable from the server.
3. **Writes are owner-gated.** `propose_fact` writes only a quarantined inbox,
   and each proposal becomes an effect only through the owner's per-event
   approval.
4. **The owner holds kill switches** for each domain and for the whole surface,
   effective without a deploy.

## Doctrine lines amended

Numbered rules keep their identifiers; existing citations do not move.

| Location | Amendment |
|---|---|
| `vision.md`, Rule 1 | Owner-operated edge services in front of the surface (the owner's gateway account and the identity used as its allowlist key) serve only the owner and are not shared infrastructure. |
| `vision.md`, Rule 3 | A third named exception paragraph, placed after the identity-projection paragraph. It restates the four conditions and the lapse clause, points to RFC 0039 and this record, and ends "These three named carve-outs do not create a general class of on-demand exceptions." The earlier "These two…" sentence is removed. |
| `architecture.md`, exception mechanism; client-to-butler; single-PostgreSQL anti-pattern | The RFC 0039 departure from the batch criterion; the public server is a client of the projection, not of butler endpoints; a separate database is the boundary against an internet-facing compromise. |
| `security.md`, partially trusted parties | The owner's edge and outside AI clients. |
| `security.md`, Schema Isolation | The publisher role is the one guarded exception. It is primary-read-all, has no credential (OD14) and has no members. What reaches primary-read-all without it (exec, attach and debug on the instance pods, pod creation, Secret read and cluster-configuration writes in `postgres-<env>`, node access) is already superuser-equivalent, and the publisher adds none of it. |
| `security.md`, Public Read Surface (new section) | Guarantees, defense in depth past Cloudflare, and limitations including client-side prompt injection. |
| `security.md`, Deployment principles and anti-patterns | Default-deny ingress on Kubernetes; no OAuth layer or internet route in front of a butler daemon or the dashboard API. |
| `v1.md` | Names the public read surface and `propose_fact` as v1; names automatic approval as not v1. |
| RFC 0008 | Kubernetes-ingress amendment: the localhost binding rule is met on k3s by default-deny NetworkPolicy, and the only internet-reachable workload is RFC 0039's server through the gateway. |
| RFC 0010, MUST NOT item 3 | Three named on-demand carve-outs, adding RFC 0039. |
| RFC 0038, D5, D6, D7 and D13 | Amended in place: the surface admits no goal and calls no domain capability; applied proposals keep third-party authority; non-goal domain jobs may park; no standing rule approves the importer's actions. |

## Answered owner items

The conformance review found four items beyond OD1 to OD9 and returned them to
the owner. The owner answered each on 2026-11-04, and the answers are adopted
contract:

1. **Projection placement** (RFC 0039 D3; formerly open question 1). The draft
   recommended a separate CNPG cluster, `postgres-public-<env>`. OD10 chose a
   second database, `butlers_public`, on the existing `postgres-<env>`, and
   the separate cluster is now the rejected alternative: stronger isolation,
   more upkeep. Isolation rests on ordered `pg_hba` reject lines and grants.
   The public pod's egress reaches the instance that also serves the primary,
   so the network no longer separates the two. The projection shares the
   primary's volume, WAL, backups and restore drill, and goes down with it.
2. **The alert path** (RFC 0039 D16). The draft required an Alertmanager
   receiver outside Butlers. OD11 replaced it: Prometheus rules collapse into
   the flags `butlers_public_mcp:critical_ok` and
   `butlers_public_mcp:warning_ok`, and the owner's Uptime Kuma polls them and
   notifies on Telegram. No ingress into `butlers-public-<env>` is added.
3. **Live data in `dev` from the first connection** (RFC 0039 owner act 8).
   `butlers-dev` is the live system, so connecting a client exposes live data
   immediately. OD12 accepts that; kill switches remain the incident control.
4. **Extra upkeep from source-owned materializations** (RFC 0039 D2, design
   S7). Fields that live only in JSONB (finance overlays, health values,
   entity flags) need trigger-maintained columns or tables, each with a parity
   test. OD13 accepts that upkeep, with the parity tests and CI drift checks as
   its mitigation.

Automatic approval of proposals (OD4) is not decided here. It needs its own
owner decision, blocked by `bu-u5euwb.3` and `bu-u5s4w8`.

## Review and adoption record

- On 2026-11-04 the owner recorded OD1 to OD9 above and accepted the doctrine
  change (OD2).
- Independent conformance, spec-quality and security reviews of the first
  draft returned "not ready to merge" with 2 conformance blockers, 2 security
  blockers and further major and minor findings. The fixes were applied to
  RFC 0039, the doctrine sweep and the OpenSpec change in the same PR; the
  four items above were split out for the owner.
- Later on 2026-11-04 the owner answered the four items (OD10 to OD13). RFC
  0039, the decision record, the OpenSpec change and the topology docs were
  amended in the same PR to match.
- After homelab PR Tzeusy/homelab#40 applied the CNPG owner acts to
  `postgres-dev`, the owner recorded OD14 and OD15. RFC 0039 (D3, D4, D8, D9,
  D16, D17, owner acts and open questions), this record, the OpenSpec change
  and the topology docs were amended to match the homelab facts above. The
  owner then answered RFC 0039 open question 5 (OD16): no `pg_hba` reject
  line for `mcp_subscription_owner`, as an accepted risk.
