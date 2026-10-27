## Why

The Calendar module already returns a structured error dictionary when its
provider free/busy lookup cannot run. The workspace `find-time` route currently
parses that dictionary as a successful result with zero slots, so the dashboard
renders “No open slots” even though no availability was checked. This collapses
an unavailable source into a truthful empty result.

## What Changes

- Define the module `calendar_find_free_slots` contract for provider failures:
  it returns its existing structured error dictionary with an empty `slots`
  list and the request context needed by the MCP caller.
- Normalize that typed module error at the workspace API boundary to the existing
  `CalendarWorkspaceFindTimeResponse` degraded envelope (`available=false`, empty
  slots, fixed content-blind `reason`). Do not forward provider/error details.
- Preserve `available=true` for a successful free/busy lookup whose computed
  slots list is genuinely empty.
- Require the Calendar Workspace surface to render those states distinctly.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-calendar`: Find Free Slots Tool structured provider-failure result.
- `dashboard-domain-pages`: Calendar Workspace find-time unavailable versus
  successful-empty presentation.

## Impact

The change is limited to the Calendar module test fixture, the existing
workspace API route, focused module/API/dashboard tests, and OpenSpec deltas.
It does not alter the provider algorithm, OAuth behavior, event mutations,
calendar writes, response fields outside the existing `available`/`reason`
distinction, or MCP diagnostics available to the module caller.
