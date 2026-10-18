## Why

`run_secrets_lifecycle_check` recorded `suppressed` and dropped the alert on every scan while the
owner-attention policy quiet window held a credential (about 13 identical rows a night at the
30-minute cadence), and the alert was never delivered after the window. The governing intent is
"park, never delete" (bu-kqnum.3). `bu-kqnum.3.8` makes the policy-hold branch park one
window-scoped aggregate envelope instead.

## What Changes

- `core-credentials` requirement `Lifecycle Notification Remediation and Delivery Honesty`: owner
  quiet-hours policy holds park one aggregate envelope per window (atomic supersede), record
  `deferred`, and a parked state is not re-alerted once pending or delivered. Context-bus
  suppression and an unresolvable policy window keep the record-and-drop behaviour.

## Impact

- `src/butlers/jobs/secrets_lifecycle.py`, `src/butlers/core/temporal/delivery_db.py`.
- No migration: the dedup is a transaction-scoped advisory lock over the existing
  `deferred_notifications` table.
