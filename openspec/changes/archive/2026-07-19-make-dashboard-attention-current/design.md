## Context

The Overview and the server-generated briefing are intentionally coupled by a shared
attention contract, but their input semantics have drifted from the page's purpose. A
grouped audit row can remain visible for a day or longer, notification statistics default
to lifetime totals, and a completed QA dispatch is treated as an active fault. The
briefing cache also has no invalidation producer for a successful circuit-breaker reset.

The existing dashboard API already exposes the required bounded state: issues include
`last_seen_at`, notification statistics accept `since`, and QA summary includes
`kpis.active_cases_now`. The change stays within the existing read endpoints and
in-process cache boundary.

## Goals / Non-Goals

**Goals:**

- Make attention and briefing state represent live state or a recent, bounded failure.
- Keep historical evidence available without allowing it to misstate current health.
- Make breaker reset visible on the next briefing read without waiting for the five-minute
  cache TTL.
- Preserve the frontend/backend shared attention-contract test model.

**Non-Goals:**

- Adding an incident table, resolution workflow, or new dashboard aggregation endpoint.
- Deleting, dismissing, or mutating historical audit and notification data.
- Changing notification delivery behavior, QA dispatch behavior, or the briefing response
  schema.

## Decisions

### Use fixed, source-specific operational horizons

Audit groups are current only when `last_seen_at` falls in the closed interval
`[now - 12 hours, now]`; notification delivery pressure is current only when a failure
falls in the closed interval `[now - 24 hours, now]`. Each source captures its end
boundary once per composition or render, and drill-down links preserve both boundaries.
A group with missing or unparseable recency is history, never current.
The two windows reflect source semantics: an error group needs a bounded operational
horizon, while the dashboard design contract explicitly recognizes a delivery failure in
the last 24 hours as attention-worthy.

Alternative considered: special-case a model-not-found group when a later catalog verify
succeeds. Rejected because it would solve one error class while leaving every other stale
group and lifetime aggregate misleading.

### Use active QA cases instead of completed dispatch count

QA attention remains ordered as breaker tripped, recent failed patrol, then
`active_cases_now`. A completed dispatch or novel finding is routine QA activity and can
remain visible with time-bounded wording outside attention, but it must not change the
briefing state class. A failed patrol is bounded to the preceding 24 hours so a historic
failure cannot remain a permanent high-severity alert.

Alternative considered: retain every 24-hour dispatch as attention with better copy.
Rejected because it still tells the owner something needs intervention when the system is
successfully doing its own follow-up work.

### Invalidate only after a successful breaker reset commit

The QA reset route calls the existing `BriefingCache.invalidate_all()` only after the reset
marker write succeeds. The current cache is process-local and keyed per owner; all-owner
invalidation is the narrowest correct operation exposed by the cache API. A generation fence
prevents a briefing request that missed the cache before the reset from repopulating its
pre-reset snapshot after the invalidation. A reset request that finds no tripped breaker or
fails to commit leaves the cache intact.

Alternative considered: shorten the global cache TTL. Rejected because it increases model
work for every ordinary briefing request and does not make operator actions immediately
truthful.

### Keep frontend and backend contract fixtures aligned

The existing shared attention scenarios gain historical-only, bounded notification, and
active-QA cases. Backend classification and frontend row composition must agree on which
signals are current, including the resulting quiet state when only history remains.

## Risks / Trade-offs

- [A 12-hour audit cutoff can defer a still-unresolved error to the Issues page] → The
  Issues page retains the group and Overview rolls it into older history rather than
  hiding it.
- [Frontend and backend boundaries can differ by milliseconds] → Tests use explicit
  fixture timestamps and each surface applies one captured, closed interval per request
  or render.
- [The cache has no owner-specific invalidation method] → Invalidate all cached briefings
  only for a successful manual reset; the cache is small, in-process, and repopulates on
  demand.

## Migration Plan

1. Add focused regressions and shared contract fixtures before implementation.
2. Update briefing composition, reset invalidation, Overview query/model behavior, and
   wording without changing public response schemas.
3. Run targeted backend/frontend suites, lint/build checks, and the OpenSpec verification
   pass.
4. Roll back by reverting the isolated change; historical source data is untouched.

## Open Questions

None. The existing endpoints expose sufficient bounded state, and the selected horizons
are explicit and reversible.
