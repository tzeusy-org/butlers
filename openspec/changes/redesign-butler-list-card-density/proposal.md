## Why

The `/butlers` page currently presents each butler as a roomy utility card, while
the Dispatch redesign prototype uses a denser rule-separated row that makes fleet
scan, status comparison, and detail navigation faster. This change adopts the
visual intent from `pr/overview/butlers-page.jsx:108-198` without expanding the
API contract.

## What Changes

- Modify the existing Butler List Page requirement so each butler card renders a
  compact identity row with name, status pill, description, port, eligibility chip,
  and `sessions_24h` as either a small sparkline or a count.
- Preserve the existing fleet summary, loading, stale-data error, empty-state,
  alphabetical sort, and 30-second polling scenarios.
- Reject mockup-only butlers from the Dispatch data set. Calendar, memory, and
  household butlers are not part of the current roster and MUST NOT be introduced
  by this redesign.
- Explicitly keep the `ButlerSummary` API unchanged. No new fields are added to
  `ButlerSummary`; the card must use fields already available from the butler list
  response plus existing registry eligibility data.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-butler-management`: The Butler List Page card scenario changes from
  the current spacious card layout to the denser Dispatch-style card content while
  preserving existing list behavior.

## Impact

- Frontend implementation target: `frontend/src/pages/ButlersPage.tsx:50-83`
  currently owns `ButlerCard`; `frontend/src/pages/ButlersPage.tsx:87-94` sorts and
  splits butlers/staffers; `frontend/src/hooks/use-butlers.ts:17-23` polls the list
  every 30 seconds.
- Visual source: `pr/overview/butlers-page.jsx:108-198` (`ButlerRow`) shows the
  dense rule-separated row, compact mark, title/status line, metadata cluster, and
  hover inset behavior.
- API field source: `src/butlers/api/models/__init__.py:101-117` defines
  `ButlerSummary`; `src/butlers/api/routers/butlers.py:124-131` constructs list
  summaries with `name`, `status`, `port`, `type`, `description`, and
  `sessions_24h`.
- Eligibility source: `frontend/src/hooks/use-general.ts:24-30` exposes
  `useRegistry()`; `frontend/src/api/client.ts:1137-1140` fetches
  `/switchboard/registry`; `frontend/src/api/types.ts:1055-1063` includes
  `RegistryEntry.eligibility_state`.
- No database changes, no new backend endpoint, no new `ButlerSummary` fields, and
  no hardcoded calendar/memory/household butlers.
