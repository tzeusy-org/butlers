## 1. Cost evidence

- [x] 1.1 Add failing core and API regressions that separate priced, unpriced-usage, and no-usage sessions without returning raw failures.
- [x] 1.2 Propagate cost evidence and no-usage counts through ingestion list and rollup models.
- [x] 1.3 Render truthful timeline and drawer cost/remediation states with focused frontend coverage.

## 2. Passive refresh behavior

- [x] 2.1 Add a failing Timeline regression for background fetching without placeholder data.
- [x] 2.2 Gate ledger dimming on placeholder data while preserving filter-transition and pagination behavior.

## 3. Replay safety

- [x] 3.1 Add failing resolver and endpoint regressions for email, unsafe, unresolved, and safe replay policy.
- [x] 3.2 Implement shared server-authoritative replay-policy resolution and enforce it before both replay actions.
- [x] 3.3 Expose replay policy in timeline data and make row, drawer, and bulk controls fail closed with frontend coverage.

## 4. Verification and handoff

- [x] 4.1 Run focused backend and frontend suites, then fix regressions.
- [x] 4.2 Validate OpenSpec, lint, typecheck, build, and right-sized broader quality gates from the exact branch head.
- [x] 4.3 Commit scoped changes, push the branch, and open a pull request with current verification evidence.
