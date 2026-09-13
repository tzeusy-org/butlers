## 1. Specification and authority gates

- [x] 1.1 Scaffold `reconcile-backup-recovery-truth` with `openspec new change`
  and record observed, inferred, and unknown evidence without inspecting a dump,
  credential, private row, or production system.
- [x] 1.2 Define the additive `backup-recovery-truth` capability and reconcile
  it with `REQ-deployment-hardening-007` through `009`,
  `restore-drill-recovery-truthfulness` `REQ-database-security-006`,
  `artifact-bound-filtered-event-restore-verification`
  `REQ-database-security-011`, `REQ-system-overview-page-005` and
  `006`, `REQ-core-notify-026`, and `REQ-testing-031` and `032` without adding a
  duplicate whole-requirement block.
- [ ] 1.3 Obtain fresh independent security, database, API, and UX review of the
  exact spec head. Any semantic correction invalidates that review.
- [ ] 1.4 After review passes, obtain separate owner adoption naming the exact
  reviewed artifact. Do not treat review, PR state, or merge as adoption.

## 2. Future producer and coverage work after adoption

- [ ] 2.1 Extend `deploy/backup/pg_dump.sh` through an additive producer-first
  rollout so the retained relation set is closed under schema-qualified foreign
  keys in one exported repeatable-read snapshot supplied to `pg_dump`. Lock the
  ordered relation set, recheck the graph, keep the exporter/locks through
  capture, and publish nothing on inconsistency. Never widen the dump role or
  auto-exclude ordinary application data to make a run pass.
- [ ] 2.2 Preserve the canonical artifact's ownership and ACL intent and add the
  governed same-attempt disposable target. It must run bootstrap first and
  verify every expected schema/table/sequence owner, role attribute/membership,
  function owner/definer/body/search path, explicit/default ACL, RLS flag/policy,
  and absence of unexpected privilege state before cleanup.
- [ ] 2.3 Reuse the exact artifact/filtered-event manifest pair from
  `artifact-bound-filtered-event-restore-verification` and advance that one
  sibling to `backup-recovery.v2`. Bind the FK-graph, credential-inventory, and
  ownership-policy digests plus one aggregate credential count; do not add a
  public manifest endpoint or use producer metadata instead of direct integrity.
- [ ] 2.4 Update `docs/operations/backup-restore.md` with exact application and
  credential coverage, schema-qualified dependency closure, bootstrap-first
  ownership prerequisites, scoped scratch claims, failure behavior, and the
  separate production-evidence gate.

## 3. Future protected restore and evidence work after adoption

- [ ] 3.1 Reconcile this capability into `bu-kqnum.8.4` through `bu-kqnum.8.7`
  rather than creating duplicate retry, attention, API/UI, real-PostgreSQL, or
  reconciliation owners.
- [ ] 3.2 Keep one immutable attempt in the existing protected ledger, bind it
  atomically to the exact artifact digest, verified completion time/size,
  manifest/capture, FK, credential, filtered-event, ownership/ACL, cleanup and
  scope results. Extend `latest_result()` from that same row; never compose an
  older pass with a newer filesystem artifact. Preserve crash/retry fencing and
  the future multi-executor exclusion gate.
- [ ] 3.3 Reuse `REQ-core-notify-026` for post-persistence failed-attempt
  attention. Prove that pass, no-artifact, unproven compatibility, and degraded
  API-read states do not fabricate attention or notification provenance.

## 4. Future API and owner experience after adoption

- [ ] 4.1 Add the content-blind `recovery_proof` projection to
  `GET /api/system/backups` without removing or reinterpreting existing artifact,
  run, or drill fields. Implement the exact five-state precedence, closed
  failure-code vocabulary, three legal scopes, and per-state nullability matrix;
  treat absence as `unproven` for compatibility.
- [ ] 4.2 Update `BackupTile` so its narrower green state reads `Artifact
  healthy`, `Last proven restore` is a separate first-glance fact, and proven,
  unproven, failed, stale, and degraded states remain textually distinct,
  accessible, prompt, and non-blocking. Add no run-now control.
- [ ] 4.3 Keep every response, error, audit, attention, log, metric, trace, and UI
  field on an explicit content-blind allowlist; withhold raw client output rather
  than redacting and retaining it.

## 5. Future behavior-executing verification after adoption

- [ ] 5.1 Extend `tests/scripts/test_pg_dump_backup.py` to prove
  schema-qualified FK closure through the exact exported dump snapshot, with
  both orderings of concurrent FK add/drop and relation create/drop. Derive the
  complete Tier-1/Tier-2 credential-store inventory, prove dump-role visibility,
  and compare source/restored aggregate counts for empty and populated synthetic
  stores plus forced visibility loss, without selecting or printing values.
- [ ] 5.2 Extend `tests/scripts/test_pg_restore_definer_ownership.py` for the
  bootstrap-first artifact policy and
  `tests/scripts/test_restore_drill_evidence_backup.py` for exact-attempt
  projection continuity and complete protected/public allowlists. Do not add a
  second gate species for invariants those behavior tests own.
- [ ] 5.3 Implement the already-allocated
  `tests/integration/test_restore_drill_postgres.py` under `bu-kqnum.8.6` with
  real PostgreSQL client tooling, a real dump, isolated scratch restoration,
  exact scoped manifest binding, and a same-attempt bootstrap-first disposable
  target that proves the complete owner/role/function/ACL/RLS matrix and rejects
  one perturbation in every category. Prove cleanup and no live database route.
- [ ] 5.4 Extend `tests/jobs/test_backup_health.py` and
  `tests/jobs/test_restore_drill_executor.py` for fixed failure states,
  serialized execution, crash/retry identity, and no authority widening.
- [ ] 5.5 Extend `tests/api/test_system.py`,
  `frontend/src/components/system/BackupTile.test.tsx`, and
  `frontend/src/pages/SystemPage.test.tsx` from one shared matrix fixture for
  protected-reader unavailable, no attempt, legacy/incomplete binding, newest
  failure after older pass, expired pass, and current exact pass. Assert every
  status/code/scope/nullability combination, precedence, semantic text, and
  accessible detail.
- [ ] 5.6 Plant distinct synthetic sentinels and prove absence from every
  prohibited recovery surface while positively asserting each allowed field
  set. Never inspect real credential or production values.

## 6. Future closeout and operational separation

- [ ] 6.1 Run targeted producer, restore, protected-ledger, API, and frontend
  tests, then repository guards and terminal hosted CI on the exact
  implementation head. Report that implementation's actual test delta.
- [ ] 6.2 Run one cross-change reconciliation against
  `restore-drill-recovery-truthfulness` and
  `artifact-bound-filtered-event-restore-verification`, rebuild any remaining
  active whole requirement against refreshed canonical text, and archive only
  after all implemented requirements have landed.
- [ ] 6.3 Keep `bu-e1410` open until separately authorized `bu-lw18o` production
  evidence establishes whether production artifacts exist and whether any has
  restored. No development, testcontainer, CI, review, merge, or deployment
  result satisfies that gate.
- [ ] 6.4 Obtain separate authority for implementation, merge, deployment,
  migration execution, restore execution, credential access, production
  inspection, and operational evidence. None is implied by this specification.
