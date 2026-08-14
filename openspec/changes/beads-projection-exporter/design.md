## Context

The current decision path is deliberately narrow but host-local:
`butlers.jobs.decision_review.compute_decision_digest()` parses
`/app/.beads/issues.export.jsonl`, and the Switchboard jobs and
`GET /api/decisions` reuse that result. It distinguishes an unreadable or
stale source from a real empty queue, but it needs compose bind mounts and has
no transactional relation between issues, dependency edges, and the strict
unlabeled-marker lint result.

Beads/Dolt is the sole authoritative tracker and remains reachable only on the
tracker host. The selected JSONL file is a derived compatibility and rollback
path only, never a second tracker authority.
Normal application workloads must not gain a Dolt network route, `bd`, tracker
credentials, or an unbounded Beads API. RFC 0006 requires an explicit schema
and role boundary for a new PostgreSQL surface; RFC 0008 requires the narrowest
network/egress boundary. The owner selected the tracker-host exporter design
with fixed freshness, retention, shadow, and rollback values. This is still a
planning packet: no service, database role, credential, migration, network
rule, or cutover is authorized by this change.

## Goals / Non-Goals

**Goals:**

- Make one complete, bounded active Beads snapshot readable from the existing
  PostgreSQL plane without exposing tracker access to runtime consumers.
- Preserve the existing label-only decision classification, dependency-based
  P1/deploy escalation, and strict unlabeled-marker lint semantics.
- Make source freshness explicit: target at five minutes, warning after ten
  minutes, and unavailable after fifteen minutes.
- Make publication and reading atomic so a reader never combines an old pointer
  with new rows or vice versa.
- Retain exactly the active snapshot plus two prior complete snapshots and
  thirty days of categorical failed-run metadata.
- Require fourteen consecutive shadow days of semantic parity before an
  explicitly selected consumer cutover, then retain an explicit JSONL rollback
  selection for seven days.

**Non-Goals:**

- A live Beads, Dolt, GitHub, REST, MCP, or dashboard bridge.
- Tracker mutation, a general issue browser, raw export replication, or a new
  owner-action surface.
- Retention of Beads notes, comments, history, raw metadata, credentials, or
  raw export files in PostgreSQL.
- Automatic source fallback, automatic JSONL retirement, service installation,
  credential creation/use, firewall/network changes, migration execution,
  deployment, or cutover in this planning change.
- Migration or expansion of `GET /api/beads/{id}`. That separately shipped
  `BeadSnapshotReader` JSONL detail contract has data needs outside this
  Decisions-only projection and requires its own scoped security review.

## Decisions

### D1 — One deterministic tracker-host exporter is the only tracker reader

The future `beads-projection-exporter` runs only in an operator-authorized
tracker-host management workload. It invokes the local Beads export mechanism
into private, mode-restricted staging storage, validates the candidate in
memory, and opens only a TLS-authenticated PostgreSQL connection with its
dedicated writer identity. It is neither a butler nor an LLM task.

For a networked writer, the future configuration requires
`sslmode=verify-full`, a trusted CA bundle, server peer-certificate validation,
hostname verification against the configured DNS endpoint, and TLS 1.2 or
newer. The dry-run preflight fails closed before export, connection, or write
on missing, unreadable, empty, untrusted, expired, hostname-mismatched, or
below-floor TLS configuration, and on every unverified TLS mode; it never uses
system trust, a different hostname, an unverified peer, or plaintext. CA and
writer-credential provisioning remain separately owner-gated.

The exporter has no need for application secrets, application schema grants,
or a listener. Runtime containers retain no `bd` executable, tracker credential,
Dolt port, tracker-host route, or whole `.beads/` directory mount. Until a
separately authorized JSONL retirement, explicitly selected compatibility mode
may retain only the existing read-only `issues.export.jsonl` file mount. The
writer's credential and TLS material are provisioned only at a later
owner-authorized activation step; they are never placed in this repository or
logged.

Rejected alternatives:

- A dashboard-side live `bd`/Dolt client violates the tracker and network
  boundary.
- A Dolt replica is not selected without a demonstrated self-hosted
  replication, auth, recovery, and freshness contract.
- Object storage would add separate credential, lifecycle, and duplicate reader
  semantics without improving the decision join.

### D2 — Project an allowlisted active snapshot, never a raw export

The projection has one schema, `beads_projection`, outside every butler's
default search path. The exporter accepts only active source statuses
`open`, `in_progress`, and `blocked`. It stores the following typed fields:

| Surface | Allowed fields | Explicitly excluded |
|---|---|---|
| Active issue | id, title, status, issue_type, priority, created_at, updated_at, due_at, labels | notes, comments, history, arbitrary metadata, attachments, raw record/blob |
| Decision-only detail | source description only for an eligible non-epic `decision`-label issue; validated ordered options/default; structured-detail availability/reason | description for non-decision issues, raw `metadata`, inferred/rewritten options or defaults |
| Dependency edge | issue_id, depends_on_id, type, created_at | raw source edge object and inactive/end-point-missing rows |
| Lint result | candidate lint status, bounded violation id/title/category fields | raw subprocess output, exception text, raw issue metadata |
| Snapshot/run provenance | UUIDs, timestamps, version, digest, bounded counts, categorical outcome/reason | export contents, host paths, credentials, raw errors |

The exporter validates unique issue identifiers; parseable timestamps; bounded
string, label, option, and edge counts; active dependency endpoints; and valid
typed decision details before a candidate can publish. Normative bounds are
`MAX_BEAD_ID_CHARS = 128`, `MAX_ISSUE_TITLE_CHARS = 512`,
`MAX_STATUS_CHARS = 16`, `MAX_ISSUE_TYPE_CHARS = 64`,
`MAX_TIMESTAMP_CHARS = 64`, `MAX_LABELS_PER_ISSUE = 32`,
`MAX_LABEL_CHARS = 128`, `MAX_DECISION_DESCRIPTION_CHARS = 16_384`,
`MAX_OPTIONS_PER_DECISION = 16`, `MAX_DECISION_OPTION_CHARS = 512`,
`MAX_DEPENDENCY_TYPE_CHARS = 64`, `MAX_LINT_CATEGORY_CODE_CHARS = 64`,
`MAX_CATEGORICAL_REASON_CHARS = 128`, `MAX_PRODUCER_VERSION_CHARS = 64`,
`MAX_SNAPSHOT_ISSUES = 10_000`, `MAX_SNAPSHOT_DEPENDENCY_EDGES = 25_000`, and
`MAX_SNAPSHOT_LINT_VIOLATIONS = 1_000`; priority is `0..4`, timestamps are
valid RFC 3339 within the timestamp bound, and source digests are 64 lowercase
hex characters. Any field or snapshot overflow rejects the entire candidate
without truncation, row skipping, partial publication, or source fallback,
records categorical `validation_failed` / `field_bound_exceeded`, and leaves
the active pointer unchanged. It derives structured
decision fields and lint observations at export time, then discards raw
metadata. A malformed decision remains a readable decision with a named
structured-detail reason, exactly as today; it does not invalidate an otherwise
valid snapshot.

Every candidate also carries source-completeness evidence from the same source
watermark: a source-complete tracker snapshot identity, its authoritative active
count, and a canonical active-record digest. The candidate active count and
digest must match the evidence exactly. Only a bounded source-watermark digest
may be retained; export time or a content digest alone is not sufficient. Thus
an empty candidate or a lower active count than the prior completed snapshot can
publish only with that evidence, not a heuristic or arbitrary numerical
regression threshold. If the evidence is absent or mismatched, the exporter
records `source_completeness_unverified`, retains the pointer, and sets its
sticky availability override so the provider returns unavailable rather than
treating the retained data as a calm all-clear. Only a later source-complete
publication clears that override; a later failed run cannot. The source-side
read, rather than the staged candidate itself, must bind the watermark,
authoritative count, and canonical digest at one consistency point.

### D3 — Publish candidate rows and the active pointer in one transaction

The future core migration creates these implementation-facing relations:

- `beads_projection.snapshots`: one row per complete candidate, including
  `snapshot_id`, `completed_at`, source digest, bounded source-watermark digest,
  authoritative/candidate active counts, canonical active-record digest, schema
  version, and bounded counts.
- `beads_projection.snapshot_issues`, `snapshot_dependencies`, and
  `snapshot_decision_lint`: rows keyed by `snapshot_id`.
- `beads_projection.publication_state`: exactly one row pointing to the active
  completed snapshot and carrying its sticky availability override.
- `beads_projection.sync_runs`: categorical attempt outcomes. Non-published
  rows retain no raw error or export content and expire after 30 days. The
  reader metadata view exposes only the current availability override from
  `publication_state`; it exposes no failed-run history.

The exporter takes a dedicated PostgreSQL advisory lock before staging or
writing. It records a categorical failed run for lock, source, parse,
validation, and database failures, but never moves the active pointer for a
failed candidate. A source-completeness failure also sets the sticky availability
override without moving that pointer. On success it inserts the candidate rows,
marks the snapshot complete, updates `publication_state`, clears the override,
and records its success in one database transaction. A crash or rollback leaves
the prior active snapshot and all of its rows readable.

After a successful pointer flip, retention deletes only completed snapshots
older than the active-plus-two-prior window. The cleanup query must never
delete the current pointer target. Failed-run metadata is pruned at 30 days by
category/time without retaining raw failure text.

### D4 — `BeadReadProvider` is bounded and reads one coherent snapshot

`BeadReadProvider` is the only runtime-facing reader. Its immutable return
value contains `available`, source, snapshot id, snapshot/source timestamps,
freshness and target-met state, unavailable reason, active typed issues, typed
dependency edges, and the typed lint result. `decision_review` continues to own the pure
decision classifier and escalation calculation; it receives the provider's
typed snapshot instead of parsing JSONL.

The provider obtains all active-pointer metadata and rows under one
`REPEATABLE READ, READ ONLY` PostgreSQL transaction through runtime-facing
views. Each view is rooted in the active pointer and carries `snapshot_id`; the
provider verifies every returned row belongs to the selected snapshot. A query
failure, empty/multiple pointer, schema mismatch, or mismatched row snapshot
is `available=false`, never an empty snapshot. Runtime roles receive only
`USAGE` on the schema and `SELECT` on these active views; they receive no
direct table, pointer, history, failed-run, sequence, or writer privilege.

Freshness uses a completed snapshot's `completed_at`:

- age at or below five minutes is `fresh` and meets target;
- age over five and at or below ten minutes is still `fresh` but misses the
  target and is observable in status/metrics;
- age over ten and at or below fifteen minutes is `warning`, remains readable,
  and must be visibly named by consumers;
- age over fifteen minutes, a missing snapshot, or an unreadable projection is
  `unavailable` with a stable categorical reason.

No automatic fallback crosses the JSONL/projection boundary. Source selection
is explicit and reports its source and as-of value to consumers.

### D5 — Preserve lint and dependency behavior as semantic data, not raw input

The exporter runs the existing strict decision convention semantics over the
candidate's active issue set, including labeled decisions and title-marker
unlabeled detection. It persists whether that evaluation was clean, had
violations, or was unavailable; violation identifiers, titles, and approved
category codes are sufficient for the existing attention path. It never
relabels source issues or turns an unavailable lint into a clean result.

It similarly normalizes active dependency edges so the existing decision
digest continues to find an open P1 bug or deploy-marked issue whose `blocks`
edge depends on an eligible decision. The pure digest tests must run once
against a legacy parsed fixture and once against an equivalent provider
snapshot, comparing decision order, structured-detail availability, escalation
rows, and lint state.

### D6 — Consumer migration keeps the API contract honest and additive

The implementation extracts the decision calculation from file I/O. Both the
Switchboard scheduled jobs and `GET /api/decisions` call the same provider and
same pure calculation. The dashboard remains read-only and gets additive
provenance fields: selected source, snapshot-as-of, freshness state, and
whether the five-minute target is met; `export_as_of` remains meaningful for
explicit JSONL mode and source export time when known. Existing decision
summary, malformed-detail, order, and escalation semantics remain unchanged.

At hard unavailability, consumers return their existing degraded response and
must not deliver a no-decisions digest or a conclusion based on stale data. At
warning freshness, consumers retain data but visibly name the source age rather
than presenting it as current.

### D7 — Shadow first, explicit Decisions cutover second, explicit rollback only

For fourteen full days, the exporter publishes projection candidates while
runtime consumers remain in explicit JSONL mode. Every successful cycle
compares legacy and provider semantic results for decision rows, structured
detail state, ordered escalation rows, and lint outcome. A mismatch or source
failure emits a bounded operator signal and resets the consecutive clean-day
counter; it does not change reader mode.

After fourteen clean shadow days and separate owner authorization, one
deployment selects projection mode for both scheduled jobs and the Decisions
dashboard. `GET /api/beads/{id}` is not in that cutover: its
`BeadSnapshotReader` remains an explicitly retained JSONL consumer because its
bounded detail allowlist includes `design` and `acceptance_criteria`, which the
selected Decisions projection intentionally excludes. For the Decisions
consumers, the JSONL source selection remains available only as an explicit
rollback selection for seven calendar days. Rollback changes the configured
source selection as one deployment action and names `jsonl` in all provenance;
it is never an in-process fallback.

A later JSONL-retirement packet MUST first create a complete JSONL consumer
inventory. Every consumer must be either migrated with contract and regression
proof for its exact source/data contract or explicitly retained with its
continued mount/parser/materialization rationale. In particular, any expansion
or migration of `GET /api/beads/{id}` requires separately scoped security review
and is not authorized by this packet. JSONL mount and parser retirement require
a separate owner authorization after that window and are not part of this
change.

### D8 — Privilege proof is an implementation gate, not prose

The migration must create targeted role grants and runtime-facing views with
the actual deployment roles, not a synthetic superuser test. The tracker-host
writer can write only `beads_projection` staging/publication relations and
execute only its required functions; it cannot read or mutate public or
butler-specific application tables. Switchboard and dashboard reader roles
cannot write the projection or access its history. The test matrix must prove
these facts under `SET ROLE`, including a fresh/core-only database and a
hardened-role posture.

## Risks / Trade-offs

- **[A valid export is suspiciously empty or regresses in count]** → Require
  same-source-watermark authoritative-count and canonical-digest evidence;
  otherwise retain the prior pointer, record
  `source_completeness_unverified`, and return unavailable rather than an
  all-clear. Regression coverage must prove the policy without an arbitrary
  numerical threshold.
- **[A writer crashes midway through a publish]** → Candidate insertion and
  pointer update share one transaction; readers retain the prior complete
  snapshot.
- **[A reader observes a pointer flip between statements]** → Use one
  repeatable-read transaction and assert one snapshot id across all selected
  views; fail closed on disagreement.
- **[An operator needs an urgent rollback]** → Keep JSONL in an explicitly
  selected, seven-day rollback mode; do not hide a projection failure with
  automatic fallback.
- **[A schema/grant assumption passes as an administrator only]** → Require
  actual-role integration tests and a deployment dry run before activation.
- **[A raw Beads field leaks into the runtime plane]** → Enforce exporter
  allowlists, storage constraints, and non-materialization tests; additions
  require a new spec/RFC review.
- **[The control plane remains healthy but linter execution fails]** → Carry
  an unavailable lint state through the provider so scheduled review records
  the named failure instead of a calm audit.

## Migration Plan

1. Land the migration, exporter, provider, pure decision calculator, role
   tests, and source-selection wiring with reader mode fixed to JSONL.
2. An owner separately approves and provisions the tracker-host workload,
   TLS writer credential, database access, and any required deployment
   configuration; no credential or infrastructure material appears in git.
3. Enable exporter shadow publication only. Keep all runtime readers in JSONL
   mode and observe the target/warning/hard-failure metrics plus parity output
   for fourteen full days.
4. If the fourteen-day parity gate succeeds, obtain an explicit cutover
   authorization and deploy projection mode for both the Switchboard and
   dashboard. Confirm provenance, warning UI, hard-unavailable behavior,
   reader/writer role tests, and no tracker route in runtime containers.
5. For seven days, permit only an explicit configuration rollback to JSONL.
   Do not remove the JSONL mount, parser, or exporter history during that
   period.
6. After the rollback window, require a separate owner decision before any
   JSONL retirement. Retention continues to keep active plus two prior complete
   snapshots and thirty days of categorical failed-run metadata.

## Open Questions

No architecture choices remain open for this packet. The remaining gates are
operational authorizations: selecting the concrete tracker-host workload,
provisioning TLS and the least-privilege writer identity, executing the
migration/deployment, beginning shadow observation, approving cutover, and
later approving any JSONL retirement.
