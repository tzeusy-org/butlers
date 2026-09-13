## 1. Recovery contract

- [x] 1.1 Show Regenerate for a typed, proven missing-witness gap while keeping
  outage, unproven, no-data, degraded, unknown, and unsettled states closed.
- [x] 1.2 Preserve exact tuple pending, navigation, failure, and successful
  refetch behavior.

## 2. Split-process execution and silence

- [x] 2.1 Add a Chronicler-only daemon control that reuses the scheduled
  day-close path and returns metadata-only outcomes.
- [x] 2.2 Proxy the dashboard refresh endpoint to that control over MCP.
- [x] 2.3 Reject runtime-recursive control calls and permit only the bounded
  bundle read for the trusted manual-refresh trigger without changing
  scheduled delivery.

## 3. Verification and documentation

- [x] 3.1 Extend focused frontend, API, core-control, and notification behavior
  tests while condensing superseded in-process API coverage.
- [x] 3.2 Update the frontend/backend contract and run strict OpenSpec,
  targeted, planned, frontend, and hygiene gates.
