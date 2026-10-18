## Why

`run_secrets_lifecycle_check` recorded `suppressed` and dropped the alert on every scan while the
owner-attention policy quiet window held a credential (about 13 identical rows a night at the
30-minute cadence), and the alert was never delivered after the window. The governing intent is
"park, never delete" (bu-kqnum.3). `bu-kqnum.3.8` makes the policy-hold branch park one
window-scoped aggregate envelope instead.

## What Changes

- `core-credentials` requirement `Lifecycle Notification Remediation and Delivery Honesty`: owner
  quiet-hours policy holds park one aggregate envelope per window (atomic supersede) and record
  `deferred`. Context-bus suppression and an unresolvable policy window keep the record-and-drop
  behaviour.
- `core-credentials` requirement `Transition-Debounced Lifecycle Delivery`: a parked state is
  skipped only while its parked row is the newest lifecycle marker and the envelope that carried
  that credential is pending or delivered.
- `core-credentials` requirement `Audit Action Vocabulary for Credential Lifecycle`: adds
  `lifecycle_state_parked`.

## Impact

- `src/butlers/jobs/secrets_lifecycle.py`, `src/butlers/core/temporal/delivery_db.py`.
- No migration: the dedup is a transaction-scoped advisory lock over the existing
  `deferred_notifications` table.
