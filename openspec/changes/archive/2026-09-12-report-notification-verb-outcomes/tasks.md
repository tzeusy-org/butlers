## 1. Outcome reporting

- [x] 1.1 Report retry and escalate outcomes on `NotificationsPage`: success naming the accepting channel, the new attempt's own error when a 200 carries a failed attempt, and the endpoint's `detail` when the call is rejected.
- [x] 1.2 Narrow both notification mutations' `TData` to `NotificationActionResult` so an unreachable `undefined` no longer reads as a delivery failure.

## 2. Contract

- [x] 2.1 Pin the 409 rejection detail as operator-readable copy naming the row's current status, since it is now rendered verbatim.

## 3. Verification

- [x] 3.1 Add frontend coverage for the success, failed-attempt, and rejected outcomes of both verbs.
- [x] 3.2 Add backend coverage asserting the 409 detail names the current status.
