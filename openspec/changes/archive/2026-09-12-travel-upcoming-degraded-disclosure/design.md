## Context

`GET /api/travel/upcoming` (`roster/travel/api/router.py::get_upcoming_travel`)
loops over trip rows and, for each, builds legs/accommodations/pre-trip
actions via the shared `_row_to_leg`/`_row_to_accommodation`/`_row_to_trip`
converters. Those converters now route through
`butlers.tools.travel._helpers._row_to_dict` (bu-2jtfw.1, sibling slice),
which raises if a JSONB metadata value cannot be normalized. The fleet-wide
degraded-mode convention
(`docs/api_and_protocols/response-conventions.md`) already has a documented
shape for this: "a named list on the payload itself", mirroring
`SpendSummary.unavailable_butlers`. The frontend already has the matching
primitive, `SourceDegradedNote` (`frontend/src/components/ui/query-boundary.tsx`),
used identically by `SessionsKpiStrip` for both a full-outage and a
partial-degradation banner.

## Goals / Non-Goals

**Goals:**
- Make `GET /api/travel/upcoming` resilient to one bad trip row: exclude it
  and disclose it, instead of 500ing the whole response.
- Make the Trips tab KPI strip honestly represent an upstream failure and a
  partial exclusion, following the `SessionsKpiStrip` precedent exactly.

**Non-Goals:**
- `GET /api/travel/trips/{id}` per-entity degraded envelopes (single-trip
  fetch either succeeds or 404s/500s; no fan-out to degrade).
- Retry/backoff policy, caching, or query staleness handling — reuses
  `useUpcomingTravel`'s existing React Query config unchanged.
- Any change to `travel.legs`/`travel.trips` schema, migrations, or MCP tools.

## Decisions

### Named list on the payload, not a boolean flag

`UpcomingTravelModel.unreadable_trip_ids: list[str] = []` follows the
established `unavailable_butlers`-style convention rather than inventing a
new shape. A boolean `has_degraded_trips` was considered and rejected: the
convention doc is explicit that the flag should let the frontend name what
failed, and a trip id is directly actionable (it round-trips into a support
query) where a boolean is not.

### Catch at the per-trip loop boundary, not per-field

`get_upcoming_travel` wraps each trip's normalization (row conversion +
nested legs/accommodations fetch) in one `try/except`, appending the row's
raw id to `unreadable_trip_ids` on failure and `continue`-ing the loop. A
narrower per-field guard was rejected as unnecessary complexity: the only
realistic failure mode at this seam is `_row_to_dict` raising on
metadata (or a similarly-shaped decode error), and the whole per-trip
unit is what the frontend renders atomically.

### `isError`, not a derived "degraded" boolean, drives the KPI strip

`KpiStrip` takes `isError: boolean` (from `useUpcomingTravel().isError`)
exactly as `SessionsKpiStrip` takes it from `useSessionAggregate`. The
existing `WeekAheadSchedule`/`UpcomingChecklist` panels already receive
`error` from the same hook and render their own inline message; `KpiStrip`
only needed the boolean to switch its numerals to "unavailable" and mount
the `SourceDegradedNote` banner — no new hook, no new query.

## Risks / Trade-offs

- **[Risk]** A trip that fails to normalize disappears from the roster count
  used elsewhere (e.g. `TripRoster`, which calls `useTravelTrips` separately
  and is not part of this slice). **Mitigation**: out of scope here —
  `TripRoster` reads `/api/travel/trips`, a flat list with its own
  converters, unaffected by this `/upcoming`-only change. Filed as a
  follow-up if the same failure mode is later observed there.
- **[Risk]** `unreadable_trip_ids` could silently grow unbounded if a
  systemic data issue reappears. **Mitigation**: each excluded trip is also
  logged server-side (`logger.warning`) so it surfaces in existing log-based
  alerting, not just the dashboard.

## Migration Plan

Pure additive API field plus frontend prop — no data migration, no
backward-incompatible change. Rollback is a code rollback.
