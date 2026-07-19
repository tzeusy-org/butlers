## 1. Real-pool regression

- [x] 1.1 Add a Switchboard integration regression that writes representative
  metadata through `log_notification()` and asserts
  `jsonb_typeof(notifications.metadata) = 'object'` plus decoded mapping
  content.
- [x] 1.2 Run that regression against the pre-fix writer and confirm it fails
  because the stored JSONB value is a string.

## 2. Writer correction

- [x] 2.1 Normalize optional metadata to a JSON-safe Python mapping in
  `log_notification()` and bind the mapping directly through asyncpg without
  pre-serializing it.
- [x] 2.2 Re-run the real-pool regression and the focused Switchboard suite to
  confirm JSONB object persistence and existing delivery behavior.

## 3. Verification and handoff

- [x] 3.1 Validate the OpenSpec change strictly and run the required lint,
  format, and test gates.
- [ ] 3.2 Review the final diff for the write-only slice boundary, then commit,
  rebase if needed, push the branch, and open a PR for exact-head review.
