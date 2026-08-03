## Context

`GET /api/butlers/board` already publishes one canonical `BoardRow.activity`
verdict for each row. `useButlerStatusBoard` maps those rows, while
`BoardHeader` currently calculates healthy as only total minus offline and
quarantined. That permits overdue and unknown liveness rows to compose a calm
healthy count. The hook also has a separate `eligibilityUnavailable` count,
but eligibility availability is not a liveness verdict and therefore cannot
stand in for the canonical `unknown` activity.

The Page shell owns full-page errors, but `ButlersPage` currently passes
status-board header and footer slots even when an initial request failed with
no cached rows. Its cached-refresh path intentionally keeps rows visible with
a banner and must retain those slots.

Finally, `_cadence_label()` uses broad duration ranges: a two-hour interval is
called `hourly`, even though the label appears in operator-facing cadence copy.

## Goals / Non-Goals

**Goals:**

- Keep fleet-health arithmetic aligned with the canonical activity vocabulary.
- Make the initial-error screen contain only error/retry context, while
  preserving cached board context during a refresh failure.
- Make named cadence labels exact and make noncanonical intervals explicit as
  `custom`.
- Cover the boundary behavior through narrow API, hook, header, and page tests.

**Non-Goals:**

- Changing `_derive_board_activity`, registry eligibility derivation, cron
  scheduling, cadence-overdue calculation, or any persisted data.
- Adding a new endpoint, migration, design primitive, animation, or status
  vocabulary.
- Reusing or extending `clarify-butler-schedule-facts`, whose scope excludes
  status-board activity.

## Decisions

### Derive the unknown aggregate from canonical mapped rows

`useButlerStatusBoard` will count rows whose server-provided `activity` is
`unknown` and expose that count in `StatusBoardAggregates`. The header will
subtract `offline`, `quarantined`, `overdue`, and `unknown` from `total`.

This keeps health arithmetic coupled to the same first-match-wins liveness
verdict all board consumers already use. `eligibilityUnavailable` remains a
separate registry-availability diagnostic and is not used as a health input.
Deriving a new API aggregate instead was rejected because it would duplicate a
row-derived fact at the API boundary without improving the header contract.

### Gate chrome only for a no-cache full-page error

The existing no-cache condition is exactly `isError && !hasRows`. The page
will use that condition for both its full-page error and chrome gate. Loading
and normal empty states preserve the Page shell behavior, and cached-refresh
errors have no initial-error condition, so they keep their chrome plus the
stale-data banner.

Gating solely on `hasRows` was rejected because it would unnecessarily remove
the existing empty-board chrome and conflate a valid empty response with a
failed initial request.

### Use exact canonical cadence labels

`hourly`, `daily`, and `weekly` will map only to exactly 1 hour, 1 day, and 7
days respectively. Any other positive interval, including two hours and
quarter-hour cron schedules, maps to `custom`; `None` stays `None`.

Range bucketing was rejected because it turns the tooltip’s descriptive label
into an inaccurate duration claim. Adding new dynamically formatted label
strings was rejected because the existing wire vocabulary deliberately exposes
`custom` for noncanonical schedules and this change does not need a new API
shape.

## Risks / Trade-offs

- [An additive aggregate property requires mock updates] → Update focused
  typed fixtures with `unknown: 0` so tests retain explicit semantics.
- [A broad cadence bucket was previously relied on for a frequent schedule] →
  Preserve the raw `cadence_seconds` and overdue calculation; only the
  human-facing bucket changes, with direct regression coverage for canonical
  and noncanonical intervals.
- [Chrome could disappear for the wrong state] → Derive the gate from the
  existing `pageError` signal and test both initial-error and cached-error
  paths.

## Migration Plan

1. Deploy the additive frontend aggregate and server-side label correction
   together with their consumers.
2. Roll back by reverting this focused code and OpenSpec change; no data or
   schedule state is changed.
3. Future activity verbs must update the canonical activity contract and this
   health arithmetic together.

## Open Questions

None.
