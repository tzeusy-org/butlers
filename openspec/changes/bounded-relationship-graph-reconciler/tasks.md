## 0. Adoption gate and ordering

- [ ] 0.1 After #4646 merges, merge `origin/main` and re-read RFC 0031 and `entity-graph/spec.md`; confirm no conflicting edit.
- [ ] 0.2 Confirm `entity-chronicles-and-commitment-doors` still carries no `entity-graph` or `dashboard-domain-pages` delta.
- [ ] 0.3 Confirm `generalist-lead-focused-delegation` still leaves `dispatch_mode='job'` as a native deterministic dispatch.
- [ ] 0.4 Confirm no active change owns `relationship_assert_fact` or `entity_facts` writers.
- [ ] 0.5 Owner answers Q1, Q2 and Q3 in the proposal and adopts this change. Until then bu-h40h2b.7 stays blocked.

## 1. Writer repair (after adoption)

- [ ] 1.1 Failing test first: an identical reassert with a deleted projection leaves it missing; then make the `unchanged` path upsert its source-keyed edge in the same transaction (REQ-entity-graph-014).

## 2. Reconciler, receipt and job (after adoption; lands with 1)

- [ ] 2.1 Reconciler with add, correct and remove, Relationship-only filter, batch and lock limits (REQ-entity-graph-013, -016).
- [ ] 2.2 Receipt table migration, receipt writes for all four statuses and bounded pruning (REQ-entity-graph-015).
- [ ] 2.3 `[[butler.schedule]]` entry, `dispatch_mode = "job"`, `job_name = "relationship_graph_reconcile"`, and the job function (REQ-entity-graph-016).
- [ ] 2.4 Tests for idempotence (clean rerun reports zero), concurrency (`skipped_lock`), partial runs, failure receipts and no cross-source effects.

## 3. Read-only receipt presentation (after 2 is stable)

- [ ] 3.1 Additive receipt fields and presentation of never run, failed, partial, skipped and in sync (REQ-entity-graph-017).

## 4. Rule ratio copy (independent of 1-3, after adoption)

- [ ] 4.1 Failing test first, then RuleDetailPage renders `No applications yet` at `applied_count = 0` and the stored value otherwise (REQ-dashboard-domain-pages-053).

## 5. Documentation

- [ ] 5.1 Update RFC 0031's stale slice status and `docs/concepts/identity-model.md` for the projection versus source boundary only.
- [ ] 5.2 Named fleet evidence documentation belongs to RFC 0032 and bu-h40h2b.8, not here.
