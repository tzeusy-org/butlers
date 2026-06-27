# Owner Timezone Context

## Purpose

The dashboard is a single-tenant, owner-operated interface. Every timestamp it renders
should reflect the owner's configured timezone, not the machine's locale and not the
server's timezone.

This spec defines the **dashboard-wide timezone contract**: one React context, one hook,
one consumption pattern that all pages and components follow. The owner timezone is fetched
once at shell level and injected into context so individual pages never re-fetch it or
thread it as a prop.

Non-Negotiable Rule #4 from `about/heart-and-soul/design-language.md` is explicit:

> **Time is a typed primitive.** All timestamps render via a single `<Time>` component that
> knows the user's timezone, the butler's timezone, the desired precision, and the
> relative-vs-absolute mode. `new Date(x).toLocaleString()` in a page file is a bug.

This spec is the contract that backs that rule.

## Requirements

### Requirement: AppTimezoneProvider, AppTimezoneContext, and useTimezone hook

The dashboard SHALL provide an `AppTimezoneContext` React context, an `AppTimezoneProvider`
provider, and a `useTimezone()` hook that any dashboard component can call to read the
owner's resolved IANA timezone name.

These live in a single module at `frontend/src/components/ui/timezone-context.tsx`, which
exports `AppTimezoneContext`, `AppTimezoneProvider`, `useTimezone`, and the `DEFAULT_TZ`
constant.

The provider is a thin context injector: it accepts the already-resolved IANA timezone via a
`timezone` prop and does NOT perform any API fetch itself. Resolving the owner's timezone
from the backend is the caller's responsibility (see "Provider placement" below). This keeps
the provider trivially testable without a `QueryClientProvider`.

#### Scenario: useTimezone returns the injected timezone

- **GIVEN** the `AppTimezoneProvider` is mounted with `timezone="Asia/Singapore"`
- **WHEN** any child component calls `useTimezone()`
- **THEN** it receives `"Asia/Singapore"`

#### Scenario: useTimezone outside provider returns DEFAULT_TZ

- **GIVEN** a component is rendered outside an `AppTimezoneProvider`
- **WHEN** it calls `useTimezone()`
- **THEN** it receives `DEFAULT_TZ` (`"Asia/Singapore"`), the context's default value
- **AND** it does NOT call `Intl.DateTimeFormat().resolvedOptions().timeZone`

### Requirement: DEFAULT_TZ deterministic default

The module SHALL export a `DEFAULT_TZ` constant set to `"Asia/Singapore"`. This is the
explicit, deterministic default used both as the `AppTimezoneContext` default value and as
the fallback the App applies when `GET /api/settings/general` has not yet returned a
timezone. `"Asia/Singapore"` matches the `SGT` constant used by the backend briefing logic
(`briefing.py`, a hardcoded UTC+8 offset). Note that the backend general-settings module
(`src/butlers/core/general_settings.py`) uses a different fallback, `DEFAULT_GENERAL_TIMEZONE
= "UTC"`; the frontend `DEFAULT_TZ` and that backend default do not currently align, and a
user-configured timezone (resolved through `GET /api/settings/general`) reconciles the two at
runtime. Browser locale is never used as the default.

#### Scenario: DEFAULT_TZ is the context default

- **WHEN** `AppTimezoneContext` is created
- **THEN** its default value is `DEFAULT_TZ` (`"Asia/Singapore"`)

### Requirement: Provider placement in the application hierarchy

The `AppTimezoneProvider` SHALL be mounted at App level, inside `QueryClientProvider` (so the
App can use TanStack Query to read general settings) and wrapping `RouterProvider` (so it
spans all routes). The App fetches the owner's timezone via `useGeneralSettings()`
(`GET /api/settings/general` → `.timezone`), falls back to `DEFAULT_TZ` when that value is
absent, and passes the resolved string into `AppTimezoneProvider`'s `timezone` prop. Every
page therefore renders within a single timezone context without per-page setup.

#### Scenario: Provider is accessible on all routes

- **WHEN** any page route renders
- **THEN** `useTimezone()` returns the resolved timezone without any per-page provider setup
- **AND** the timezone value is fetched once at App level and not refetched per route navigation

#### Scenario: App resolves the timezone from general settings

- **WHEN** the `App` component renders and `GET /api/settings/general` returns `timezone: "Europe/London"`
- **THEN** the App passes `"Europe/London"` to `AppTimezoneProvider`
- **AND** `useTimezone()` returns `"Europe/London"` for all descendants

#### Scenario: App falls back to DEFAULT_TZ before settings load

- **WHEN** the `App` component renders and `GET /api/settings/general` has not yet returned a timezone
- **THEN** the App passes `DEFAULT_TZ` (`"Asia/Singapore"`) to `AppTimezoneProvider`
- **AND** `useTimezone()` returns `"Asia/Singapore"`

### Requirement: `<Time>` component uses useTimezone internally

The `<Time>` component SHALL call `useTimezone()` internally to resolve the rendering
timezone. Pages and intermediate components SHALL NOT pass a timezone prop to `<Time>` for
the purpose of owner-timezone resolution.

Pages MAY pass an explicit `timezone` prop to `<Time>` when the timestamp is semantically
tied to a specific timezone other than the owner's, or when rendering in isolation outside an
`AppTimezoneProvider`. The explicit `timezone` prop overrides the context value
(`timezone ?? contextTz`). This is an override, not the primary resolution path.

#### Scenario: Time renders in owner timezone by default

- **GIVEN** `AppTimezoneProvider` is mounted with `"Asia/Singapore"`
- **AND** a page renders `<Time value={someIsoString} />`
- **WHEN** the component renders
- **THEN** the time value is formatted in `"Asia/Singapore"` without the page passing any timezone prop

#### Scenario: Explicit timezone prop overrides context

- **GIVEN** `AppTimezoneProvider` is mounted with `"Asia/Singapore"`
- **AND** a page renders `<Time value={someIsoString} timezone="Europe/London" />`
- **WHEN** the component renders
- **THEN** the time value is formatted in `"Europe/London"` (explicit prop wins)

### Requirement: Chronicles timezone consumers read the shared context via aliases

The Chronicles workspace SHALL read the owner timezone from the same shared
`AppTimezoneContext` as the rest of the dashboard. The legacy
`ChroniclesTimezoneProvider` and `useChroniclesTimezone()` names are retained as thin
backward-compatibility re-export aliases so existing Chronicles components and tests
continue to compile:

- `frontend/src/components/chronicles/timezone-context.tsx` re-exports
  `AppTimezoneProvider as ChroniclesTimezoneProvider`.
- `frontend/src/components/chronicles/use-chronicles-timezone.ts` re-exports
  `useTimezone as useChroniclesTimezone`.

Because both names alias the canonical provider and hook, Chronicles and all other
components share one timezone context — there is no separate Chronicles context. New code
SHALL use `useTimezone()` directly; the aliases exist only for transitional compatibility
and are candidates for later cleanup.

#### Scenario: Chronicles consumers see the shell timezone

- **GIVEN** the App-level `AppTimezoneProvider` is mounted with `"Asia/Singapore"`
- **WHEN** a Chronicles child calls `useChroniclesTimezone()`
- **THEN** it receives `"Asia/Singapore"` from the shared `AppTimezoneContext`
- **AND** the value is identical to what `useTimezone()` returns

#### Scenario: ChroniclesTimezoneProvider is an alias

- **GIVEN** a test or component wraps children in `ChroniclesTimezoneProvider`
- **WHEN** the children call `useTimezone()` or `useChroniclesTimezone()`
- **THEN** both return the timezone passed to the provider, because it is the same
  `AppTimezoneProvider`

## Source References

- Non-Negotiable Rule #4 (Time is a typed primitive) — `about/heart-and-soul/design-language.md`
- RFC 0007 (Dashboard and API surface) — `about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md`
- `openspec/specs/user-preferences/spec.md` — `preferences:general_timezone` predicate
- Implemented under bead bu-ldj6y (commit 11c01dc51)
