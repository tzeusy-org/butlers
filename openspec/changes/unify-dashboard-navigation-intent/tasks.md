# Tasks

## 1. Spec Draft

- [x] 1.1 Reconcile the dashboard-shell baseline, active deltas, shell capability manifest, both
      predecessor hooks, their current consumers, and the original shaping non-goals.
- [x] 1.2 Add the uniquely named `Unified Navigation Intent Warmup` requirement with exact happy,
      partial, empty, failure, concurrency, cancellation, auth/cache, compatibility, rollback, and
      future verification behavior.
- [x] 1.3 Validate this named change strictly and run the spec-overwrite, countable-task, and full
      repository guard lanes. Record any global warnings or baseline debt truthfully.
- [x] 1.4 Publish the exact spec head as a draft PR with `Tests: +0 ~0 -0` and terminal hosted CI
      evidence.

## 2. Adoption Gates

- [ ] 2.1 Independent semantic review returns GO on the exact draft head or actionable corrections
      are applied and reviewed again.
- [ ] 2.2 Owner approves the exact artifact commit or digest, including
      `OWNER-DECISION-NAV-001` and `OWNER-DECISION-NAV-002`.

## 3. Future Implementation, Outside This Change

- [ ] 3.1 After PR #4042 and `bu-8cdl1.13` are terminal, run a fresh overlap/readiness scan and
      authorize a separate implementation packet.
- [ ] 3.2 Implement and behavior-test the unified primitive, migrate all named callers atomically,
      remove both predecessor hooks, and pass the exact-head frontend gates.
