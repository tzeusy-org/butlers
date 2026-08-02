## Why

The System verdict can currently render an all-clear before the database and
data-egress sources have settled, or after either ordinary source has failed.
That turns incomplete observability into false calm. An egress 403 is different:
it is an expected owner-only visibility boundary, not a service failure.

## What Changes

- Make the System verdict wait for the existing database and egress queries to
  settle before it can render an all-clear.
- Surface a named unavailable-source clause for a database failure or a
  non-403 egress failure.
- Keep the egress hook's existing `isForbidden` 403 state settled and
  non-failing, without changing its authorization behavior.
- Add focused frontend regression coverage and narrow specification scenarios
  for source settlement and error honesty.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `system-overview-page`: the computed System verdict must truthfully account
  for database and egress source settlement.
- `deployment-and-drift`: the System verdict's source-honesty contract must
  cover the database and egress sources alongside migration drift.

## Impact

- `frontend/src/components/system/SystemVerdictBanner.tsx`
- `frontend/src/pages/SystemPage.test.tsx`
- `openspec/specs/system-overview-page/spec.md`
- `openspec/specs/deployment-and-drift/spec.md`

No endpoints, persistence, retries, authorization semantics, database-size
health rules, or egress-activity alarms change.
