# Adopted doctrine amendment: public MCP read surface

**Status:** Doctrine change accepted by the owner on 2026-11-04 (OD2). The
design is [RFC 0039](../rfcs/0039-public-mcp-read-surface.md); conforming text
is adopted once the PR's independent conformance review passes. Items listed
under [Returns to the owner](#returns-to-the-owner) are not adopted by this
record.
**Date:** 2026-11-04
**Baseline:** `9f723b9468`
**OpenSpec change:** `add-public-mcp-read-surface`

## Decision

Butlers admits one deterministic, internet-reachable, read-only MCP server
through which the owner's own outside AI clients (ChatGPT, including its Dots
agent, and claude.ai) read Butlers data on demand. The server reads only a
separate projection database that one-way PostgreSQL logical replication fills
from one checked-in allowlist manifest. Its only write is `propose_fact`, an
inbox row that changes nothing until the owner approves it per event on the
dashboard approvals page. It is a third named exception to vision.md
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
| `security.md`, Schema Isolation | The publisher role is the one guarded exception, and its credential is primary-read-all material. |
| `security.md`, Public Read Surface (new section) | Guarantees, defense in depth past Cloudflare, and limitations including client-side prompt injection. |
| `security.md`, Deployment principles and anti-patterns | Default-deny ingress on Kubernetes; no OAuth layer or internet route in front of a butler daemon or the dashboard API. |
| `v1.md` | Names the public read surface and `propose_fact` as v1; names automatic approval as not v1. |
| RFC 0008 | Kubernetes-ingress amendment: the localhost binding rule is met on k3s by default-deny NetworkPolicy, and the only internet-reachable workload is RFC 0039's server through the gateway. |
| RFC 0010, MUST NOT item 3 | Three named on-demand carve-outs, adding RFC 0039. |
| RFC 0038, D5, D6, D7 and D13 | Amended in place: the surface admits no goal and calls no domain capability; applied proposals keep third-party authority; non-goal domain jobs may park; no standing rule approves the importer's actions. |

## Returns to the owner

The conformance review found these items beyond OD1 to OD9. They are written
as recommendations, not adopted contract, until the owner decides them:

1. **A separate CNPG cluster for the projection** (`postgres-public-<env>`,
   RFC 0039 D3 and open question 1). OD5 names a namespace and the replication
   role, not a second cluster, and OD1 asks for low upkeep. The fallback is a
   second database on the existing cluster, with weaker isolation (D3).
2. **The alert receiver** (RFC 0039 D16). The specs require only an
   Alertmanager receiver outside Butlers; a Telegram receiver is a suggestion.
3. **Live data in `dev` from the first connection** (RFC 0039 owner act 8).
   `butlers-dev` is the live system, so connecting a client exposes live data
   immediately.
4. **Extra upkeep from source-owned materializations** (RFC 0039 D2, design
   S7). Fields that live only in JSONB (finance overlays, health values,
   entity flags) need trigger-maintained columns or tables, each with a parity
   test. That is the OD1 fallback, but it adds upkeep the owner should see.

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
