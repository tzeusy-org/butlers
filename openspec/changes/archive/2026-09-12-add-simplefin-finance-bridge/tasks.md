## 1. Contract and failing behavior tests

- [x] 1.1 Strictly validate the narrow SimpleFIN OpenSpec delta before code work.
- [x] 1.2 Add focused migrated-DB and mocked-HTTP tests for absent/malformed configuration, request shape, sanitized upstream failures, and no-write freshness behavior; observe expected RED failures.
- [x] 1.3 Add focused tests for first-response account provisioning,
  provider-metadata binding, complete-response validation, settled-only
  provenance, replay idempotency/counting, date windows, and advisory-lock
  contention; observe expected RED failures.

## 2. Finance bridge implementation

- [x] 2.1 Implement the Finance-owned SimpleFIN v2 parser, safe URL/request
  boundary, sanitized results, first-response one-account provisioning, exact
  later-run binding, and dedicated session advisory lock.
- [x] 2.2 Add the internal-only transaction source seam so the bridge records normal Finance transactions with `source="aggregator"`, stable `external_id`, and safe provenance without changing the public MCP signature.
- [x] 2.3 Implement full-response-before-write handling, settled-only transaction selection, first/retry windows, replay convergence, and success-only `last_synced_at` updates.

## 3. Scheduling and operator documentation

- [x] 3.1 Export the Finance job, register the deterministic scheduler handler, and add the daily off-top-of-hour TOML schedule.
- [x] 3.2 Document the exact `/secrets` Finance-target setup, automatic
  first-response account provisioning, degraded behavior, rollback, and v1
  limits without exposing credential material.
- [x] 3.3 Amend the Finance manifesto to include explicitly opted-in, read-only
  bank-feed evidence without changing its no-surprise financial-clarity scope.

## 4. Verification and handoff

- [x] 4.1 Run focused Finance tests, Ruff check/format, strict OpenSpec validation, and the required final merge gate.
- [x] 4.2 Review the scoped diff for public-signature, no-migration, no-secret, and no-live-activation compliance before commit and PR handoff.
