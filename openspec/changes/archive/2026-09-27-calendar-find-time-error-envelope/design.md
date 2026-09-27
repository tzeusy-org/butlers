## Context

`CalendarModule._find_free_slots()` catches `CalendarAuthError` and returns the
existing structured module error payload (`status="error"`, sanitized error
metadata, empty `slots`, `duration_minutes`, and `calendar_ids`). The workspace
route only checked the `slots` list, so that typed failure was projected as
`available=true` with no slots.

## Decisions

1. Keep the module's structured error shape and provider algorithm unchanged.
   The API route is the owner of the dashboard response contract and checks the
   explicit `status="error"` discriminator before slot parsing.
2. Map both structured module failures and transport failures to the existing
   typed response with `available=false` and empty `slots`.
3. Use one fixed human-readable reason, `Free/busy lookup unavailable; try
   again shortly.`, at the dashboard boundary. Provider, calendar, exception,
   and credential details remain content-blind and are not copied into the
   response. The module retains its existing sanitized diagnostic for its MCP
   caller.
4. Preserve the successful-empty distinction: only a non-error module result
   with `slots=[]` returns `available=true` and the existing no-open-slots UX.

## Verification

The behavior is pinned at the module, API, and Calendar Workspace seams. The
module test proves the structured failure fixture and credential redaction; the
API test proves typed unavailable projection and no raw-provider leakage; the
workspace test proves successful empty results remain a calm empty state.
