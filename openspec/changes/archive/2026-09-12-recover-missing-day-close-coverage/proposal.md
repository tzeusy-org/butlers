## Why

Chronicles can truthfully identify a settled day whose coverage witness is
missing, but the page offers no recovery action in that state. The existing
stale-cache Regenerate control is also incomplete in the split-process dev
deployment: its dashboard route expects an in-process spawner callback that is
never wired. Reusing the scheduled prompt directly would additionally allow a
historical refresh to send an old day-close notification.

## What Changes

- Expose Regenerate for an unavailable day only when the coverage-floor and
  exact-witness reads both succeeded and no owned briefing read is unavailable.
- Proxy manual regeneration from the dashboard API to a Chronicler-only daemon
  control over the existing MCP boundary.
- Reuse the scheduled prompt, token-bounded bundle, writer admission, exact
  date/timezone binding, cache identity, coverage witness, and rate limit.
- Make the trusted manual-refresh execution context notification-silent by
  permitting only its bounded bundle read at the MCP wrapper boundary, while
  preserving ordinary scheduled day-close delivery.
- Return only cache/admission metadata across the MCP and HTTP boundaries.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chronicles`: recover a proven missing-witness gap from the failure
  state without offering regeneration for an outage or unproven state.
- `chronicler-api`: make manual historical regeneration operational in the
  split-process deployment and deterministically owner-silent.

## Impact

- `frontend/src/pages/ChroniclesPage.tsx` and its focused page tests
- `roster/chronicler/api/router.py` and focused refresh API tests
- Chronicler-only core MCP control registration and focused behavior tests
- Manual-refresh MCP tool-policy enforcement and its daemon regression
- `docs/frontend/backend-api-contract.md`
- No migration, automatic backfill, new LLM reasoning path, scheduled-notify
  change, cross-schema read, credential action, or deployment operation
