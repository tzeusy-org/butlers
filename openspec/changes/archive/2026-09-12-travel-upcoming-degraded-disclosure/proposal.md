## Why

`GET /api/travel/upcoming` and the Trips tab's KPI strip currently cannot
distinguish "no upcoming travel" from "the upstream fetch failed" or "one
trip's row could not be normalized": on error the strip renders 0 active /
0 planned / 0 open actions, which is indistinguishable from a genuinely quiet
calendar. This violates the fleet-wide degraded-mode convention (a source
that raises or is unreachable must never render as a truthful empty/zero
result) and lets a real outage impersonate an empty travel calendar.

## What Changes

- `UpcomingTravelModel` (`GET /api/travel/upcoming`) gains an additive
  `unreadable_trip_ids: list[str]` field. When a trip row's nested legs or
  accommodations cannot be normalized, the router excludes that trip from
  `upcoming_trips` (rather than 500ing the whole response) and lists its id
  here instead.
- `ButlerTravelTripsTab`'s `KpiStrip` gains an `isError` prop, wired from
  `useUpcomingTravel`'s query state. On error it renders "unavailable" (never
  a numeral, never `0`) for next departure / active / planned / open-actions
  and shows a `SourceDegradedNote` banner naming the endpoint, with a retry
  action.
- When `unreadable_trip_ids` is non-empty (partial degradation, main query
  still succeeded), the KPI strip shows a second-tier `SourceDegradedNote`
  disclosing how many trips were excluded, without hiding the trips that did
  normalize.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `butler-travel`: `GET /api/travel/upcoming` and its KPI strip consumer must
  disclose upstream failure and per-trip normalization failure rather than
  rendering a healthy-looking zero/empty result.

## Impact

- Affected code: `roster/travel/api/router.py`, `roster/travel/api/models.py`,
  `frontend/src/components/butler-detail/ButlerTravelTripsTab.tsx`.
- No schema, migration, MCP tool surface, or scheduled-task change.
