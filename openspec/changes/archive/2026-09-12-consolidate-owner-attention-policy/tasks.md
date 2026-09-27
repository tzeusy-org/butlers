## 1. Contract and regression design

- [x] 1.1 Add failing shared-policy boundary, timezone, and exact-anchor tests.
- [x] 1.2 Add failing API validation and dashboard wording coverage.
- [x] 1.3 Add failing broker, sleep-context, approval-push, and durable
  scheduler non-re-gating regressions.

## 2. Canonical policy implementation

- [x] 2.1 Implement the end-exclusive shared predicate and timezone-aware
  boolean/anchor helpers with fail-open diagnostics.
- [x] 2.2 Rewire listed direct owner-attention readers to use the shared helper
  without local timezone conversions.
- [x] 2.3 Keep the scheduler on stored due rows and preserve urgent-only broker
  bypass behavior.

## 3. Persistence and public surface

- [x] 3.1 Add a guarded, rerun-safe core migration that consolidates legacy
  quiet fields and supports downgrade compatibility.
- [x] 3.2 Validate complete hour pairs and IANA zones at the stable policy API.
- [x] 3.3 Update the Approvals dashboard wording without changing endpoint or
  payload shape.

## 4. Documentation and verification

- [x] 4.1 Reconcile canonical specs, active context delta, RFCs, and topology
  with the consolidated authority and explicit non-goals.
- [x] 4.2 Run focused unit, integration, API, frontend, migration, and OpenSpec
  validation gates; fix regressions.
- [x] 4.3 Run required repository quality gates and record exact evidence.
