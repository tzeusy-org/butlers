## 1. Spoken capture contract

- [x] 1.1 Add failing focused connector/client regressions for episode request
  acquisition, content-kind normalization, boundary behavior, and passive
  envelope privacy.
- [x] 1.2 Implement the isolated spoken-session state, normalization, and
  passive connector submission without altering music session behavior.
- [x] 1.3 Add the narrow global pre-resolved `metadata_only` policy for
  `spotify:spoken:` and behavior-test that it cannot spawn or route a butler
  session while music events remain pass-through.

## 2. Durable evidence surface

- [x] 2.1 Add failing persistence and migration/ACL regressions for bounded,
  idempotent connector-owned spoken evidence.
- [x] 2.2 Add the guarded core migration and connector upsert, including the
  non-fatal evidence-write failure posture.
- [x] 2.3 Exercise core upgrade/downgrade, absent-role guards, and ACL behavior
  against PostgreSQL rather than only asserting migration SQL text.

## 3. Verification and handoff

- [x] 3.1 Run strict OpenSpec validation plus focused connector/client/migration
  tests and relevant lint/type gates.
- [x] 3.2 Review the final diff against the capture-only boundary: no scope,
  routing, UI, transcript, raw payload, LLM, Education, or Chronicler
  projection expansion.
