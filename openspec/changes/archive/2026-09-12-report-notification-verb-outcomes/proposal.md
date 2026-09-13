## Why

`POST /api/notifications/{id}/retry` and `/escalate` already exist and the
notification feed already offers inline Retry and Escalate verbs (bu-ep4ks.4,
PR #3579). What is missing is the operator seeing what happened.
`NotificationsPage` passed only `onSettled`, so both non-success outcomes were
swallowed: the 409 raised for a row that is no longer `failed`, and a 200 whose
new attempt itself came back `failed`. In both cases the spinner cleared and
nothing else changed, so a verb that had delivered nothing read as one that had
succeeded.

The `Notification Audit Trail` requirement never described the inline verbs or
their outcome reporting at all, so nothing in the spec said which of these
outcomes the operator is entitled to see.

## What Changes

- Report the real outcome of retry and escalate: a success toast naming the
  channel that accepted the attempt, an error toast carrying the new attempt's
  own delivery error when the call returns 200 but the attempt failed, and an
  error toast carrying the endpoint's `detail` when the call is rejected.
- Narrow both mutations' `TData` to `NotificationActionResult`.
  `ApiResponse<T>.data` is non-optional, so the `| undefined` was never
  reachable and forced the page to treat "no result" as a delivery failure.
- Pin the 409 detail as operator-readable copy naming the current status, since
  the page now renders it verbatim.
- Document the inline verbs and their outcome reporting on the existing
  `Notification Audit Trail` requirement.

No endpoint, schema, migration, or transport change.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-visibility`: the notification audit trail requirement gains the
  inline retry/escalate verbs and the outcomes the operator is shown.
