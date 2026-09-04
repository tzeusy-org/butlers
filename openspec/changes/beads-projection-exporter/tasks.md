## 1. Contract fixtures and red tests

- [ ] 1.1 Add canonical active-Beads fixtures covering eligible decisions,
  malformed/missing decision details, title-marker unlabeled decisions,
  P1/deploy blockers, inactive rows, and every named bounded-field violation,
  including at-limit acceptance and bound-plus-one rejection. Include
  same-source-watermark authoritative active-count/canonical-digest evidence
  plus empty and count-regressed candidates with absent or mismatched evidence.
- [ ] 1.2 Extract pure decision/lint comparison fixtures and add RED parity
  tests proving the existing JSONL calculation and a typed provider snapshot
  produce identical decision order, structured-detail state, escalation rows,
  and lint categories.
- [ ] 1.3 Add RED non-materialization tests asserting that notes, comments,
  history, raw metadata, raw export bytes, host paths, credentials, and raw
  failure text cannot enter the projected row types or provider envelope.

## 2. Projection schema and least-privilege database boundary

- [ ] 2.1 Immediately before creating the migration, fetch the target branch,
  inspect the core chain's single head, and allocate the next core Alembic
  revision with `down_revision` set to that observed head. Do not reserve a
  revision identifier, filename, or predecessor in this planning packet. If a
  later fetch demonstrates a migration conflict, rebase to resolve it and
  repeat the allocation; do not refresh-rebase an otherwise clean PR. Then
  create the `beads_projection` schema, completed snapshot tables,
  active-pointer relation, active reader views, categorical sync-run rows, and
  constraints/indexes for the allowlisted fields.
- [ ] 2.2 Implement the writer-only advisory-lock/publication transaction and
  retention SQL so candidate rows, complete state, and active pointer commit
  together; reject `field_bound_exceeded` candidates without truncation or
  partial rows; require exact source-completeness evidence from the same source
  watermark before publication, from one source-side consistency point rather
  than a recount/digest of the candidate; record
  `source_completeness_unverified` without moving the pointer when evidence is
  absent/mismatched; set a sticky singleton availability override that only a
  later source-complete publication clears; expose only that current override
  through a bounded reader view; retain active plus two prior completed snapshots
  and prune only failed-run categories older than 30 days.
- [ ] 2.3 Add migrated-PostgreSQL integration tests for crash/rollback safety,
  pointer uniqueness, no mixed active rows, deterministic bound rejection,
  failed-run categorization, retained-pointer source-completeness failure,
  retention, and a fresh/core-only database.
- [ ] 2.4 Add actual-role `SET ROLE` tests proving the exporter identity can
  write only the projection surface, runtime reader roles can select only the
  bounded active views, and neither side can access the other application's
  schemas, snapshot history, pointer state, or writer capability.

## 3. Tracker-host exporter

- [ ] 3.1 Add the deterministic tracker-host exporter entry point and typed
  parser/normalizer with PEP 723 metadata; its only tracker operation is local
  export staging and its only network operation is the future TLS PostgreSQL
  writer connection.
- [ ] 3.2 Implement active-status filtering, allowlisted issue/dependency
  normalization, decision-only description/options/default derivation,
  bounded categorical failures, exact same-source-watermark completeness
  validation, and strict lint-result normalization without retaining raw
  metadata or subprocess output.
- [ ] 3.3 Add focused exporter tests for malformed JSON, duplicate ids,
  endpoint/timestamp/bound validation, at-limit acceptance and bound-plus-one
  rejection for every named field and snapshot limit, advisory-lock contention,
  database failure, safe retry, categorical `field_bound_exceeded` and
  `source_completeness_unverified`, source-complete empty/count-regressed
  acceptance, absent/mismatched-evidence rejection, and no-pointer-move
  behavior.
- [ ] 3.4 Add a dry-run-only operator configuration validator that rejects a
  missing TLS writer configuration, an unverified TLS mode, or a non-tracker-host
  invocation before any export/write attempt. It SHALL require
  `sslmode=verify-full`, a trusted CA bundle, server peer-certificate and
  hostname verification for the configured DNS endpoint, and TLS 1.2 or newer;
  it fails closed for a missing/unreadable/empty/untrusted/expired CA,
  hostname mismatch, protocol-floor failure, or any unverified TLS mode. Add
  planning regression tests for each rejection and prove no export, connection,
  candidate row, or active-pointer movement occurs. Do not install a service or
  provision a credential in the code change.

## 4. Bounded provider and pure decision calculation

- [ ] 4.1 Add `BeadReadProvider` typed models and a repeatable-read,
  read-only active-view implementation that verifies one pointer/snapshot id
  across metadata, issues, dependencies, and lint rows and makes the sticky
  `source_completeness_unverified` availability override unavailable despite a
  retained pointer.
- [ ] 4.2 Implement provider freshness classification at five-minute target,
  ten-minute warning, and fifteen-minute hard-unavailable boundaries with
  stable categorical unavailable reasons; expose whether the five-minute
  target is met independently of the warning state.
- [ ] 4.3 Refactor `decision_review` so source I/O is separate from the pure
  label-only classifier, structured-detail mapper, escalation calculator, and
  lint handling; preserve the current JSONL implementation as an explicitly
  selected compatibility provider only.
- [ ] 4.4 Add provider and pure-calculation tests for pointer flips, views with
  mismatched snapshot ids, missing/schema-error data, every freshness boundary,
  warning visibility, hard-unavailable and source-completeness no-all-clear
  behavior, and JSONL semantic parity.

## 5. Shared consumer migration

- [ ] 5.1 Wire the Switchboard decision-review digest and escalation scheduled
  jobs to one configured `BeadReadProvider`; retain attention-ledger behavior
  for lint violations/unavailability and prohibit source fallback.
- [ ] 5.2 Wire `GET /api/decisions` to the same provider/calculation and add
  `beads_source`, `snapshot_as_of`, `beads_freshness`, and
  `beads_target_met` metadata while preserving all existing summary, order,
  structured-detail, and degradation fields; a current
  `source_completeness_unverified` result returns the existing degraded shape
  with `decisions_available=false` and that categorical reason.
- [ ] 5.3 Update the Decisions API/page types and source-as-of plaque so
  projection warning is visible, hard unavailable cannot render an all-clear,
  and explicit JSONL rollback remains identifiable.
- [ ] 5.4 Add focused job, API, frontend, and accessibility regressions for
  JSONL and projection modes, warning/hard-unavailable states, lint outcomes,
  explicit source provenance, retained-pointer source-completeness failure, and
  no new decision mutation affordance.

## 6. Shadow parity and owner-gated activation

- [ ] 6.1 Implement a bounded shadow comparator that records only counts,
  snapshot ids/digests, category codes, and semantic mismatch summaries; it
  compares JSONL/projection results on every successful cycle without changing
  reader mode.
- [ ] 6.2 Add tests and operator documentation proving one mismatch or source
  failure resets the 14-day consecutive parity gate and that a complete clean
  14-day run is required before cutover is eligible.
- [ ] 6.3 After separate owner authorization, provision the tracker-host
  workload, TLS least-privilege writer credential, migration/deployment path,
  and projection mode configuration; verify normal runtime containers still
  cannot reach tracker/Dolt or possess tracker material.
- [ ] 6.4 After separate cutover authorization, switch both consumers together
  to projection and retain explicit JSONL rollback selection for seven days;
  do not retire JSONL mounts, parser code, or export materialization in this
  change. `GET /api/beads/{id}` and its `BeadSnapshotReader` remain an
  explicitly retained JSONL consumer outside the Decisions-only cutover.

## 7. Verification, review, and later retirement gate

- [ ] 7.1 Run strict OpenSpec validation, targeted unit/API/frontend tests,
  migrated-role integration tests, lint/format, and the repository final
  merge-readiness gates from the exact PR head.
- [ ] 7.2 Perform independent security review of raw-field exclusion, TLS
  writer scope, role grants, atomicity, no-fallback semantics, and actual
  network/deployment evidence before any activation approval is consumed.
- [ ] 7.3 File or update the separate owner-gated follow-up for JSONL
  retirement after the seven-day rollback window; it must include fresh
  parity/operational evidence, a complete JSONL consumer inventory, and a
  disposition for every consumer. Each consumer must be either migrated with
  contract and regression proof or explicitly retained with its
  mount/parser/materialization rationale. Any expansion or migration of
  `GET /api/beads/{id}` and `BeadSnapshotReader` requires separately scoped
  security review and may not be inferred from this change.
