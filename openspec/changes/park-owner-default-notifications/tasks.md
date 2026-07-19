## 1. Test-first behavior coverage

- [x] 1.1 Add failing daemon-level tests for eligible policy and context holds:
  full-envelope persistence, deferred result, ledger linkage, exemptions, and
  durable-write failure without immediate delivery.
- [x] 1.2 Add failing helper tests for the policy wake anchor, approval-push
  compatibility, and latest-expiry suppressing-context result.
- [x] 1.3 Add or extend scheduler integration coverage proving a stored
  owner-default envelope flushes verbatim and transport failure stays pending.

## 2. Durable owner-default parking

- [x] 2.1 Extract a compatibility-preserving policy delivery-anchor helper in
  `core.approvals_policy` and retain `approval_push_deliver_at()` behavior.
- [x] 2.2 Add a structured suppressing-context accessor in
  `core.attention_ledger` while preserving string-helper callers.
- [x] 2.3 Replace only the eligible direct `notify()` policy/context drop
  branches with existing deferred-envelope persistence, provenance, and
  retryable persistence-failure behavior.

## 3. Contract and documentation reconciliation

- [x] 3.1 Reconcile the completed `context-bus-producers` artifacts so no
  future sync restores destructive suppression semantics.
- [ ] 3.2 Update RFC 0011, outbound-flow topology, and the OpenSpec deltas;
  sync the completed new change into canonical specs and archive only this new
  change.

## 4. Verification and handoff

- [x] 4.1 Run focused unit, daemon, and scheduler integration tests plus the
  required lint/format and OpenSpec validation checks.
- [ ] 4.2 Run the project-defined final quality gate, inspect the scoped diff,
  commit, push the worker branch, and open a PR against `main`.
