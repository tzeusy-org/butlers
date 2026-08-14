# Beads Runtime Data Bridge

> **Status:** Owner-approved Option A planning baseline — implementation,
> migration execution, credential provisioning, deployment, and cutover remain
> separately authorized operational work.
>
> **Decision owner:** the Butlers operator / platform owner.
>
> **Purpose:** Define a hardened, multi-host path for runtime consumers to
> read a bounded projection of Beads issue data without giving application
> containers network or credential access to Beads/Dolt.

## Executive summary

Today, Beads is authoritative in a Dolt server bound to the tracker host's
loopback interface. The shipped single-host bridge exports
`.beads/issues.export.jsonl` on that host and bind-mounts only that one file,
read-only, into the `butlers-up` and dashboard API services. The current
`decision_review` consumer is deliberately honest about failure: a missing,
unreadable, or stale export is **unavailable**, not an empty decision queue.

That is a useful single-host compatibility bridge, but it is not a durable
multi-host contract. It depends on a compose-project-local file, cannot be
queried from a different host, and can freeze at an old inode when an export is
atomically replaced. It must not be "fixed" by exposing Dolt to runtime
containers or mounting `.beads/` wholesale; the latter contains tracker
credentials.

The recommended target is a small, deterministic, management-plane
**Beads projection sync service**. It reads Beads locally through `bd export`,
validates a bounded issue/dependency snapshot, and transactionally publishes a
read-only PostgreSQL projection. Runtime consumers read only the last complete
projection through a shared provider. Beads/Dolt remains the source of truth;
the projection is a cache with explicit freshness and failure semantics.

## Scope and invariants

### In scope

- Read-only runtime visibility of Beads data, beginning with the existing
  owner-decision digest and its P1/deploy blocking escalation.
- A path that works when the tracker host and Butlers runtime hosts differ.
- Explicit freshness, provenance, and degraded-mode behavior.
- A least-privilege database and network boundary suitable for the existing
  Docker/egress model.

### Out of scope

- Runtime mutation of Beads issues, dependencies, labels, or metadata.
- Replacing Beads/Dolt as the tracker source of truth.
- A generic Beads REST/MCP API for daemons, LLM sessions, or dashboard users.
- Broad tracker replication for unrelated reporting use cases.
- Deploying a service, creating credentials, changing firewall rules, or
  selecting an operational owner.

### Non-negotiable invariants

1. **Beads/Dolt stays the sole authoritative tracker.** PostgreSQL stores an
   explicitly labelled projection, never a second writable tracker. The
   selected JSONL file is a derived compatibility and rollback path only, never
   a second tracker authority.
2. **Runtime is read-only.** A runtime container, dashboard request, or
   prompted LLM cannot write Beads through this bridge.
3. **No tracker credentials in application containers.** In particular, never
   mount `.beads/` or `.beads-credential-key` into them.
4. **No fabricated all-clear.** An absent, malformed, partially published, or
   source-completeness-unverified or over-age projection reports
   unavailable/stale rather than `0` open decisions.
5. **No broad egress exception.** Normal runtime containers do not receive a
   route to Dolt or the tracker host merely to read issue data.
6. **One completed snapshot at a time.** Readers either see the previously
   completed snapshot or the next completed snapshot, never an in-progress
   mixture.

## Current evidence and problem boundary

The current design is intentionally narrow:

- `bd` talks to the shared Dolt server on the tracker host's `127.0.0.1:3307`.
  A container's loopback is not the host loopback, and the egress firewall
  blocks RFC1918, Tailscale CGNAT, link-local, and other private ranges except
  explicitly allowed hosts.
- `src/butlers/jobs/decision_review.py` reads the exported JSONL file and
  returns `available=False` for a missing, unreadable, or over-age source. It
  uses the same deterministic digest for Switchboard delivery and
  `GET /api/decisions`.
- `docker-compose.yml`, `scripts/compose.sh`, and
  `butlers.core.deploy.materialize_beads_export()` deliberately mount or
  create only `issues.export.jsonl`, never the whole tracker directory.
- The existing bridge was shipped for the single-host decision desk in
  [PR #3140](https://github.com/Tzeusy/butlers/pull/3140). Later deployment
  hardening added the dashboard mount and deploy-time export materialization;
  it still leaves a local-file contract rather than a multi-host data plane.

This preserves the security intent of RFC 0008: the application egress plane
must not gain arbitrary private-network reachability simply because it needs a
small amount of governance data. It also preserves RFC 0006's schema and role
discipline: a cross-plane data surface needs an explicit migration and grants,
not an incidental application connection to another database.

## Options considered

| Option | Benefits | Costs and failure exposure | Disposition |
|---|---|---|---|
| Keep the single-file JSONL bind mount | Already shipped; no new service; preserves host-only tracker access | Compose/worktree path coupling, stale-inode risk, every runtime host needs the same host file, no transaction/provenance boundary beyond file mtime | Keep only as the current compatibility path and a controlled rollback mode |
| API-side live tracker reader | One apparent source and no copied issue rows | Introduces a privileged tracker endpoint or a Dolt network exception into the runtime plane; ties dashboard and jobs to tracker availability/latency; creates an auth, DoS, and data-exfiltration surface | Reject |
| Direct Dolt SQL client from runtime | Queries canonical tracker data directly | Requires exposing/binding Dolt beyond host loopback, new MySQL/Dolt client/runtime surface beside the PostgreSQL stack, and a private-network allowlist exception; grants application workloads tracker read capability | Reject |
| Self-hosted Dolt read replica | Could keep a tracker-native read model and support multiple readers | The repository has no proven self-hosted replication, failover, freshness, or credential-rotation contract for this topology. It would still need a network/service boundary and an application-side Dolt client. Replica consistency and recovery must be demonstrated before selection | Prototype-only alternative; not the default recommendation |
| Immutable export in object storage | Multi-host fan-out without a tracker TCP route; immutable artifacts aid audit | Adds storage credentials, object lifecycle, reader cache invalidation, and duplicate parsing/query logic. It does not naturally provide transactional relational joins for the decision digest | Viable only if object storage becomes the approved cross-host artifact plane; otherwise not preferred |
| **Management-plane export sync to a PostgreSQL projection** | Uses the already-required Butlers PostgreSQL plane; keeps tracker access outside runtime; permits transactionally complete snapshots, typed queries, metrics, role grants, and multi-host readers | Adds a small service, a new schema/migration, sync lag, and operational ownership | **Selected by the owner (Option A); activation remains separately authorized** |

The Dolt-replica option is deliberately not rejected as impossible. It is
rejected as the default until a time-boxed prototype proves the exact
self-hosted replication, freshness, authentication, recovery, and access
semantics required here. A hosted-product capability or a generic SQL-server
feature is not enough evidence for this deployment.

## Recommended architecture

### Boundary and data flow

```text
Tracker / management plane                  Butlers data plane
--------------------------                  ------------------
bd + local Dolt
       |
       | local bd export; no runtime route
       v
beads-projection-sync  -- TLS, narrow DB credential -->  PostgreSQL
       |                                                  beads_projection.*
       |                                                        |
       | structured logs, metrics, sync state                  | read-only
       v                                                        v
operator / alerting                                  Switchboard + dashboard API
                                                             |
                                                             v
                                                    degraded-honest decision digest
```

`beads-projection-sync` is a deterministic tracker-host management-plane
process, not a butler and not an LLM task. Its only tracker operation is local
read/export. The implementation may not install it or provision its TLS writer
credential until a separate operational authorization selects the concrete
tracker-host workload.

The initial projection should contain only data required by the decision
digest and its escalation query:

- issue identifier, title, status, type, priority, labels, timestamps, and
  native deadline fields for active (`open`, `in_progress`, `blocked`) rows;
- the bounded structured decision context the current digest exposes:
  description, normalized `metadata.decision.options` and
  `metadata.decision.default`, plus per-record structured-details
  availability and unavailable reason;
- dependency edges and their timestamps/type;
- an export/projection provenance record, content digest, bounded
  source-watermark digest, authoritative and candidate active counts,
  completion time, producer version, validation result, and the strict
  decision-convention lint outcome required by the weekly digest.

Raw notes, comments, history, arbitrary metadata, attachments, and raw export
blobs are intentionally excluded. A source description is retained only for an
eligible non-epic decision-labeled issue; the normalized decision fields above
are an explicit bounded exception, not permission to replicate the source
metadata object wholesale. This avoids silently creating a broad governance-
data replica merely because the exporter can read it.

Before publishing, the parser enforces exactly these bounds:
`MAX_BEAD_ID_CHARS = 128`, `MAX_ISSUE_TITLE_CHARS = 512`,
`MAX_STATUS_CHARS = 16`, `MAX_ISSUE_TYPE_CHARS = 64`,
`MAX_TIMESTAMP_CHARS = 64`, `MAX_LABELS_PER_ISSUE = 32`, and
`MAX_LABEL_CHARS = 128`; decision fields use
`MAX_DECISION_DESCRIPTION_CHARS = 16_384`, `MAX_OPTIONS_PER_DECISION = 16`,
and `MAX_DECISION_OPTION_CHARS = 512`; dependency/lint/provenance fields use
`MAX_DEPENDENCY_TYPE_CHARS = 64`, `MAX_LINT_CATEGORY_CODE_CHARS = 64`,
`MAX_CATEGORICAL_REASON_CHARS = 128`, and `MAX_PRODUCER_VERSION_CHARS = 64`;
snapshots use `MAX_SNAPSHOT_ISSUES = 10_000`,
`MAX_SNAPSHOT_DEPENDENCY_EDGES = 25_000`, and
`MAX_SNAPSHOT_LINT_VIOLATIONS = 1_000`. Priority is `0..4`, timestamps are
valid RFC 3339 within their bound, and source digests are 64 lowercase hex
characters. A field or aggregate limit overflow rejects the entire candidate;
it never truncates, skips a row, publishes partial rows, or falls back to a
different source. It records categorical `validation_failed` /
`field_bound_exceeded` metadata and leaves the active pointer unchanged.

Every candidate must also carry source-completeness evidence from the same
source watermark: a source-complete tracker snapshot identity, its authoritative
active count, and a canonical active-record digest. The candidate active count
and digest must match the evidence exactly. The projection retains only a
bounded digest of the watermark; an observed timestamp or content digest alone
does not prove source completeness. An empty candidate, or one with fewer active
records than the prior completed snapshot, may publish only when this same-source
evidence proves it. This is an exact consistency policy, not an arbitrary
numerical regression threshold. The source-side read, rather than a recount or
digest of the staged candidate itself, must bind the watermark, authoritative
count, and canonical digest at one consistency point.

### Snapshot publication protocol

The schema name is `beads_projection` rather than `public`. It keeps this
control-plane cache out of every butler's default search path and enables
targeted grants. The implementation must provide at least:

- `sync_runs`: every attempt, its source/export timestamp, content digest,
  validation outcome, error class, and stable run id;
- `snapshots`: completed candidate snapshots and their lifecycle;
- `issues` and `dependencies`, keyed by snapshot id;
- a singleton `publication_state` that identifies the one active completed
  snapshot, records last success/last failure, and carries the sticky current
  availability override.

For each run, the service must:

1. Generate/read an export in management-plane-local staging storage. The
   staging file is never bind-mounted into application containers.
2. Parse and validate all records before publishing: JSON structure, unique
   issue ids, known dependency endpoints, timestamp parseability, bounded
   record sizes, the fields required by the selected consumers, and matching
   source-completeness evidence from the same source watermark.
3. Preserve the scheduled decision-convention lint contract against the
   candidate's live issues, including unlabeled-marker detection, explicit
   distinction between a clean result and an unavailable/malformed lint
   result, and the normalized per-record result shape. The sync may execute
   the current deterministic linter locally or implement equivalent validated
   logic, but publication and readers must not turn lint failure into a calm
   successful audit.
4. Insert the run and its candidate rows in PostgreSQL, then atomically mark
   the snapshot current and clear its availability override in the same
   transaction. A crash or database error leaves the previously completed
   snapshot current. A source-completeness failure records its categorical run
   and sets the sticky `source_completeness_unverified` availability override
   without moving the pointer; only a later source-complete publication clears
   it.
5. Retain the active completed snapshot and exactly two prior complete
   snapshots. Retain only categorical failed-run metadata for 30 days; no raw
   export or error payload survives with a failed run.

The source does not currently provide a documented, portable export sequence
number. The implementation must therefore prototype and prove a reliable
Dolt/`bd` source watermark that identifies one source-complete read and yields
its authoritative active count plus canonical active-record digest. It must not
substitute a content digest or observed export time for that proof. Until then,
the exporter records `source_completeness_unverified`, retains the active
pointer, and sets the sticky availability override so the provider is
unavailable rather than silently accepting an empty, partial, or count-regressed
candidate as new truth. Only a later source-complete publication clears it.

### Reader contract

A single typed provider (for example, a future
`butlers.core.beads_projection` module) owns projection reads and freshness
classification. `decision_review.py` keeps ownership of decision-label
detection and escalation rules; the provider supplies a complete issue/edge
snapshot and strict-lint result rather than letting each caller parse JSONL or
query tables directly.

The provider must read the publication pointer, candidate metadata, issues,
dependencies, and lint result as one coherent snapshot. It must use either one
SQL statement rooted at the active publication pointer or a repeatable-read
transaction; separate ordinary reads that can straddle a pointer flip are not
allowed. Runtime roles receive access only to a bounded active-snapshot view or
function owned by this provider. They receive no direct `SELECT` grant on
snapshot history, failed runs, candidate rows, or `publication_state`.

Every result must carry at least:

- `available`: whether a complete snapshot exists and is within the configured
  hard freshness limit;
- `source`: `beads_projection` or the explicitly selected legacy mode during
  rollout;
- `as_of`: completed snapshot time and source export time when known;
- `age`: reader-observed projection age;
- `target_met`: whether the age is at or below the five-minute target when the
  selected source is readable;
- `unavailable_reason`: a stable reason such as `projection_missing`,
  `projection_stale`, `projection_read_error`, `projection_schema_mismatch`, or
  `source_completeness_unverified`.

Freshness is fixed: age at or below five minutes meets target; over five
through ten minutes is readable but misses target; over ten through fifteen
minutes is readable with a named warning; over fifteen minutes is unavailable.
The provider reads the active pointer and all active rows under one
repeatable-read, read-only transaction and fails closed if any returned row
belongs to another snapshot id.

`GET /api/decisions` and the Switchboard scheduled jobs must use this same
provider. A healthy empty snapshot can produce a genuine all-clear only after
the source-completeness evidence passes; an unhealthy or unverified source
cannot. The sticky `source_completeness_unverified` availability override
overrides a retained pointer with an unavailable envelope until a later
source-complete publication clears it.
After cutover, there should be no silent automatic
fallback from projection to JSONL: an automatic fallback can hide a broken
control plane behind different freshness semantics. Migration mode may select
one source explicitly and report it in status/telemetry.

### Consumer inventory and retirement boundary

The selected projection is Decisions-only. Its cutover moves only the
Switchboard decision-review jobs and `GET /api/decisions`; it does not migrate
the separately shipped `GET /api/beads/{id}` route. That route currently uses
`BeadSnapshotReader` over the read-only JSONL mount and its bounded detail
allowlist includes `design` and `acceptance_criteria`, fields intentionally
outside the Decisions projection. It is therefore explicitly retained through
this cutover and its seven-day rollback window.

Before any later JSONL mount, parser, or export-materialization retirement, the
retirement packet must produce a complete JSONL consumer inventory. Every
consumer must be either migrated with contract and regression proof for its
bounded source/data needs or explicitly retained with its rationale. Any
expansion or migration of `GET /api/beads/{id}` requires separately scoped
security review; neither this projection packet nor an owner authorization
alone supplies that review.

### Access and network model

- The sync process receives a dedicated PostgreSQL writer identity with access
  only to the projection schema and its required sequences/functions. It does
  not receive Butlers runtime secrets or broad application-schema privileges.
- The projection schema grants runtime roles `USAGE` plus `SELECT` only on the
  bounded active-snapshot surface described above; underlying projection
  tables grant them no direct access. Do not assume existing blanket `public`
  privileges are a safe authorization mechanism; the approved migration must
  verify the live role/grant model and explicitly revoke unnecessary access,
  including direct access to projection history tables.
- Runtime containers receive no Dolt port, `bd` credential, `bd` write tool,
  or whole `.beads` directory mount. Until separately authorized JSONL
  retirement, an explicitly selected compatibility mode may retain only the
  existing read-only `issues.export.jsonl` file mount. Their only new
  dependency is the existing PostgreSQL endpoint they already need.
- If the sync process uses a networked PostgreSQL endpoint, it uses TLS and a
  narrowly provisioned credential. It requires `sslmode=verify-full`, a
  trusted CA bundle, server peer-certificate validation, hostname verification
  for the configured DNS endpoint, and TLS 1.2 or newer. Missing, unreadable,
  empty, untrusted, expired, hostname-mismatched, below-floor, or unverified
  TLS configuration fails closed before export, connection, or publication;
  preflight never falls back to system trust, plaintext, an unverified peer, or
  a different hostname. If it is containerized, its egress policy admits only
  that endpoint; it never adds the tracker/Dolt host to the normal application
  egress allowlist. Provisioning the CA and credential remains separately
  owner-gated.
- No dashboard endpoint exposes arbitrary raw projection queries. The first
  surface remains the existing bounded decision digest.

## Threat model

| Threat | Required protection | Residual / operator signal |
|---|---|---|
| Compromised runtime container or prompted LLM | No tracker route, no `bd` credential, no raw `.beads` mount, and projection grants are read-only | It may read only the bounded data its assigned runtime role can query; unexpected query/permission failures are logged and alerted |
| Compromised sync process | Separate service identity, least-privilege PostgreSQL writer, dedicated host/workload boundary, no application secrets, and local-only tracker access | The process can read tracker export data and write its projection; audit each run, rotate its credential, and treat host compromise as a management-plane incident |
| Network interception or unintended lateral access | `sslmode=verify-full`, trusted CA, peer-certificate and configured-hostname verification, TLS 1.2+ only, minimal egress allowlist, no new Dolt listener exposed to app networks | Missing or unverifiable trust configuration fails closed before source access; firewall/configuration drift must be checked in deployment validation |
| Malformed, oversized, or adversarial export record | Strict parser/schema validation, bounded fields, no shell interpolation from record content, and no publishing on validation failure | Failed run is visible; last complete snapshot remains active |
| Partial write, crash, or two writers | One-writer lease/advisory lock plus transactional candidate-and-pointer publication | Readers retain the previous complete snapshot; duplicate-writer detection is an alert |
| Stale, rolled-back, or unexpectedly empty data | Source/projection timestamps, digest recording, warning and hard TTLs, comparison against prior counts, and explicit rollback validation | Consumers become unavailable at hard TTL; stale data is never an all-clear or escalation trigger |
| Unnecessary sensitive tracker data in the runtime plane | Active-only allowlist, no raw export blob/notes/history/metadata, decision-only description, and an approved change for each added field | Data-scope review is required before any field expansion |
| Projection database unavailable | Reader returns degraded availability; jobs do not send conclusions from unknown data | Dashboard and attention paths name the source failure rather than showing zero decisions |

## Ownership and failure semantics

| Boundary | Owner | Normal responsibility | Failure behavior |
|---|---|---|---|
| Beads/Dolt and `bd` | Tracker/Beads operator | Authoritative issue state and local export capability | No new snapshot; bridge reports source failure without changing tracker state |
| Projection sync | Platform/operator | Export, validate, publish, retain history, emit health | Keep last complete snapshot; record a failed run and retry with bounded backoff |
| `beads_projection` schema | Core migration owner | Tables, constraints, targeted roles/grants, retention job | Schema/version mismatch fails closed before a snapshot is published |
| Shared reader provider | Core/Switchboard owner | Freshness classification and provenance envelope | Returns unavailable, never a synthetic empty snapshot |
| Decision classification/escalation | Switchboard owner | Existing deterministic label and dependency logic | Does not deliver a digest/escalation based on an unavailable or over-age source |
| Dashboard `/api/decisions` | Dashboard API owner | Render the same provider result and degraded envelope | Names the unavailable reason; does not show a calm all-clear |
| Alerting and runbook | Platform/operator | Detect repeated sync failure, TTL breach, writer contention, and permission drift | Creates a named operational incident; no automatic tracker mutation |

The following failure rules are mandatory for the implementation change:

1. An export, parse, or validation failure publishes nothing and leaves the
   active pointer unchanged.
2. A PostgreSQL transaction failure publishes nothing; retrying must be safe.
3. A failed reader lookup or a snapshot older than the hard TTL becomes
   unavailable even if a prior snapshot is still retained for diagnosis.
4. An empty, partial, or count-regressed candidate is not treated as a normal
   all-clear unless its authoritative active count and canonical digest match
   the same source watermark. Otherwise it retains the pointer, emits only
   `source_completeness_unverified`, and the provider returns unavailable until
   a later source-complete candidate publishes.
5. A consumer must not use an unavailable snapshot to send a weekly "no
   decisions" message or an escalation conclusion.

## Implementation and operational plan

This is a packet-complete implementation graph, not authorization to execute
its operational steps.

1. **Record the architecture decision.** RFC 0025 and the
   `beads-projection-exporter` OpenSpec change carry the chosen control-plane,
   schema/role, and deployment-boundary contract. Reconcile implementation
   against RFC 0006 and RFC 0008 in the same code change.
2. **Specify the consumer contract.** Create an OpenSpec change covering the
   provider envelope, freshness semantics, decision-digest parity, dashboard
   metadata, and migration acceptance criteria. Do not make the table layout
   the public API.
3. **Add the database boundary.** A core migration creates the dedicated
   schema, completed-snapshot protocol, indexes, constraints, targeted grants,
   and telemetry/status surface. It must work against a fresh/core-only
   database as well as the deployed one.
4. **Build the sync process in shadow mode.** After separate operational
   authorization, run it in the tracker-host management plane, publish
   candidates, and compare its decision digest/lint semantics with the exact
   current JSONL source for 14 full consecutive days. Keep runtime readers on
   the explicitly selected JSONL source while parity, failure injection,
   freshness, and role tests prove out. Any mismatch resets the clean-day
   counter.
5. **Cut consumers over deliberately.** Switch both Switchboard and dashboard
   through the shared provider under an explicit deployment setting. Expose
   source, as-of, and unavailable reason in existing status surfaces. Do not
   silently fall back across sources.
6. **Prove the hardened multi-host topology.** Run a reader on a host that has
   no tracker file or Dolt route; verify it can read only PostgreSQL. Verify
   the normal application egress firewall still cannot reach the tracker/Dolt
   address and that the sync identity cannot access unrelated application
   schemas.
7. **Keep the local mount for the explicit rollback window.** For the Decisions
   consumers, JSONL remains a documented, explicit configuration rollback mode
   for seven calendar days after a separately authorized cutover. Do not remove
   compose mounts, deploy-time materialization, or legacy parser branches
   without a later, separate owner authorization plus the complete JSONL
   consumer inventory and per-consumer disposition described above.

### Proposed validation gates

- Parser fixtures: valid data, malformed JSON, duplicate ids, unknown
  dependency endpoints, timestamp errors, at-limit acceptance and bound-plus-one
  rejection for every named field/snapshot bound, and empty/count-regressed
  candidates both with and without same-source-watermark completeness evidence.
- PostgreSQL integration tests: one complete snapshot is visible; a crash or
  rejected candidate never replaces it; retention and idempotent retry work.
- Role tests using the actual `SET ROLE` model: reader roles can select only
  their allowed projection surface; the sync role cannot read/write unrelated
  schemas; runtime roles cannot write the projection.
- Digest parity tests: current JSONL and projection input yield the same
  decision order, structured decision context, escalation result, and strict
  live-candidate/unlabeled-marker lint outcome for shared fixtures. Missing,
  malformed, or internally inconsistent lint results fail closed rather than
  becoming a calm audit.
- Snapshot read-consistency tests: a reader cannot observe a publication
  pointer with rows or lint results from another snapshot, including during a
  concurrent pointer flip; runtime reader roles cannot query raw candidate or
  history tables.
- Freshness/failure tests: missing, stale, schema-mismatched, read-error, and
  `source_completeness_unverified` projection results propagate as degraded
  envelopes rather than empty queues; regression coverage must prove a retained
  pointer cannot make that category look healthy.
- Deployment test: no application container mount or egress path reaches
  Beads/Dolt; only the chosen management-plane process has tracker access.
- TLS preflight regression tests: accept only `sslmode=verify-full` with a
  trusted CA, valid peer certificate, configured-hostname match, and TLS 1.2+;
  reject every unverified mode and each absent, unreadable, empty, untrusted,
  expired, hostname-mismatched, or below-floor configuration before export,
  connection, candidate-row insertion, or pointer movement.
- Operational drill: stop the sync process, corrupt a candidate, revoke its
  database grant, and restore it; each branch must produce a named status and
  leave the last valid snapshot intact.

## Fixed design decisions and remaining authorization

The owner selected the following Option A design values:

1. A tracker-host exporter with a TLS, least-privilege PostgreSQL writer.
2. Atomic active-snapshot pointer and rows; all runtime reads use the bounded
   `BeadReadProvider`.
3. Five-minute freshness target, warning at ten minutes, hard unavailable at
   fifteen minutes.
4. Retention of the active plus two prior complete snapshots and 30 days of
   categorical failed-run metadata.
5. Preserved decision lint and dependency projection with no raw notes,
   history, or arbitrary metadata.
6. Fourteen full days of shadow semantic parity and seven days of explicit
   JSONL rollback after cutover.

Still required before executing any operational step: an owner authorization
for the concrete tracker-host workload, TLS writer credential provisioning,
migration/deployment execution, shadow start, cutover, and any later JSONL
retirement. No successful plan, test, or CI result grants those permissions.

## References

- `src/butlers/jobs/decision_review.py` — current deterministic JSONL reader,
  decision classification, escalation rules, and degraded-mode contract.
- `src/butlers/core/deploy.py` and `scripts/compose.sh` — current best-effort
  export materialization before compose mounts.
- `docker-compose.yml` and `scripts/egress-firewall.sh` — current runtime
  mount and private-network egress boundary.
- [RFC 0006: Database Schema and Isolation](../../about/legends-and-lore/rfcs/0006-database-schema-and-isolation.md)
  — schema, role, migration, and cross-boundary requirements.
- [RFC 0008: Deployment Network Security](../../about/legends-and-lore/rfcs/0008-deployment-network-security.md)
  — least-privilege deployment and egress requirements.
- [RFC 0025: Tracker-Host Beads Projection Exporter](../../about/legends-and-lore/rfcs/0025-tracker-host-beads-projection-exporter.md)
  — selected snapshot, freshness, retention, parity, and rollback contract.
- `openspec/changes/beads-projection-exporter/` — strict implementation
  requirements and packet-complete task graph.
- `openspec/changes/owner-decision-desk-decisions-lane/` — decision-desk
  degraded-envelope and digest-consumer contract.
