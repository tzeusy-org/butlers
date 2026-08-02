## 1. Flat approval radar contract

- [x] 1.1 Add failing API coverage for the exact derived stalled predicate and
  limit-independent metadata count across flat endpoint states.
- [x] 1.2 Add the flat endpoint's derived `state=stalled` validation,
  per-pool list predicate, and whole-population `meta.stalled_count` using
  shared eligibility with explicit degraded-source metadata.

## 2. Trust Console verdict

- [x] 2.1 Add failing frontend coverage proving the opener uses metadata rather
  than the bounded history rows and fails closed for degraded sources.
- [x] 2.2 Extend the flat approvals API typing/client and render the stalled
  verdict clause plus explicit incomplete-coverage state without adding a new
  lane or mutation.

## 3. Verification and handoff

- [x] 3.1 Run focused backend/API and frontend tests, relevant lint/build
  gates, and strict OpenSpec validation.
- [x] 3.2 Review the final diff against derived-state, source-truth, and
  non-goal boundaries before handoff.
