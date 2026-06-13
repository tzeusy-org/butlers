## Context

The Dispatch prototype's `ButlerRow` (`pr/overview/butlers-page.jsx:108-198`) is a
visual direction, not a source-of-truth data model. The live `/butlers` page already
fetches `ButlerSummary` rows, sorts them, separates butlers from staffers, shows
empty/error states, and refreshes via TanStack Query every 30 seconds
(`frontend/src/pages/ButlersPage.tsx:87-94`, `frontend/src/hooks/use-butlers.ts:17-23`).

The list card must therefore be a presentation change over existing data. The only
cross-endpoint join is eligibility, which already exists on the Switchboard registry
surface through `useRegistry()` (`frontend/src/hooks/use-general.ts:24-30`) and
`RegistryEntry.eligibility_state` (`frontend/src/api/types.ts:1055-1063`).

## Goals / Non-Goals

**Goals:**

- Make the butler list denser and more scannable while preserving the current page
  behavior.
- Render card content from existing `ButlerSummary` and registry fields.
- Keep the implementation compatible with the real roster and future unknown
  butler names returned by the API.

**Non-Goals:**

- Add fields to `ButlerSummary`.
- Add cost, last-run, process, or log-tail fields from the prototype.
- Add calendar, memory, or household butlers.
- Change backend routes, database schema, or polling cadence.

## Decisions

1. **Use existing list API fields.** The card uses `name`, `status`, `port`,
   `type`, `description`, and `sessions_24h` from `ButlerSummary`
   (`src/butlers/api/models/__init__.py:101-117`;
   `src/butlers/api/routers/butlers.py:124-131`). This avoids coupling a visual
   redesign to backend work that the Epic 05 contract explicitly excludes.

2. **Use registry data only for eligibility.** The eligibility chip is sourced from
   `useRegistry()` and matched by butler `name`. If a registry row is absent, the
   card should degrade to an explicit unknown/unavailable eligibility chip rather
   than inventing an eligibility state.

3. **Allow sparkline-or-count for sessions.** `sessions_24h` is a scalar count, not
   a time series. The first implementation may render the count directly; a
   sparkline is acceptable only if it can be derived without new API fields.

4. **Keep roster data API-driven.** The prototype names calendar, memory, and
   household, but the production list must render the butlers returned by
   `GET /api/butlers`. Fixtures may use real current roster names, but code must not
   hardcode the roster.

## Risks / Trade-offs

- **Eligibility request failure** -> show butler cards from the list API and render
  an unavailable eligibility chip instead of blocking the page.
- **Sparkline expectation exceeds data shape** -> render the `sessions_24h` count;
  adding a history series requires a separate API/spec change.
- **Prototype-only fields tempt backend expansion** -> spec names the allowed field
  sources and rejects cost, last-run, PID, and mockup-only butlers for this change.
