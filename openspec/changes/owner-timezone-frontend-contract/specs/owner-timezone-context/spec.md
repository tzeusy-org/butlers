# Owner Timezone Context

## Purpose

The dashboard is a single-tenant, owner-operated interface. Every timestamp it renders
should reflect the owner's configured timezone, not the machine's locale, not the server's
timezone, and not a hardcoded fallback chosen by the component author.

This spec defines the **dashboard-wide timezone contract**: one React context, one hook, one
fallback chain, and one consumption pattern that all pages and components follow.

Non-Negotiable Rule #4 from `about/heart-and-soul/design-language.md` is explicit:

> **Time is a typed primitive.** All timestamps render via a single `<Time>` component that
> knows the user's timezone, the butler's timezone, the desired precision, and the
> relative-vs-absolute mode. `new Date(x).toLocaleString()` in a page file is a bug.

This spec is the contract that backs that rule.

## ADDED Requirements

### Requirement: TimezoneContext and useTimezone hook

The dashboard SHALL provide a `TimezoneContext` React context and `useTimezone()` hook that
any dashboard component can call to get the owner's resolved IANA timezone name.

Implementation location: `frontend/src/lib/timezone-context.tsx` (provider),
`frontend/src/lib/timezone-context-internal.ts` (context object),
`frontend/src/lib/use-timezone.ts` (hook).

#### Scenario: useTimezone returns configured timezone

- **GIVEN** the `TimezoneProvider` is mounted with a resolved IANA timezone
- **WHEN** any child component calls `useTimezone()`
- **THEN** it receives the resolved IANA timezone name (e.g., `"Asia/Singapore"`)

#### Scenario: useTimezone outside provider returns UTC

- **GIVEN** a component is rendered outside a `TimezoneProvider`
- **WHEN** it calls `useTimezone()`
- **THEN** it receives `"UTC"` (the explicit deterministic default)
- **AND** it does NOT receive the browser's `Intl.DateTimeFormat().resolvedOptions().timeZone`

### Requirement: Provider placement in the application hierarchy

The `TimezoneProvider` SHALL be mounted at shell level, inside `QueryClientProvider` (so it
can use TanStack Query) and outside `RouterProvider` (so it spans all routes). This ensures
every page renders within a single timezone context without per-page setup.

#### Scenario: Provider is accessible on all routes

- **WHEN** any page route renders
- **THEN** `useTimezone()` returns the resolved timezone without any per-page provider setup
- **AND** the timezone value is fetched once per session (not once per page navigation)

### Requirement: Three-step fallback chain

The `TimezoneProvider` SHALL resolve the timezone using the following ordered chain. Each
step is attempted in order; the first that yields a valid IANA timezone name wins.

**Step 1 — URL parameter `?tz=<IANA>`:**
An explicit override for shareable and bookmarked links. This allows a URL to be shared
with a specific timezone locked in, independent of the viewer's configured preference.

**Step 2 — `GET /api/settings/general` → `.timezone`:**
The owner's configured timezone as stored in the backend general settings. This is the
primary source of truth for the owner's configured timezone. The endpoint already exists
(`frontend/src/api/client.ts`, `frontend/src/api/types.ts` `GeneralSettings.timezone`).

**Step 3 — `"UTC"`:**
An explicit, deterministic, reproducible default. UTC is chosen because it is unambiguous,
machine-neutral, and visually distinct from most owner timezones — making it obvious when
the preference has not been loaded or configured rather than silently producing plausible-but-wrong output.

**Browser locale is explicitly excluded from the fallback chain.** Browser locale is:
- Implicit: it silently couples the render to the machine the user happens to be on.
- Inconsistent: a shared link renders differently for different viewers.
- Non-FAIL-FAST: it produces plausible-but-wrong output that is hard to notice and debug.

#### Scenario: URL param takes precedence

- **GIVEN** the URL contains `?tz=America/New_York`
- **AND** `GET /api/settings/general` returns `timezone: "Asia/Singapore"`
- **WHEN** `TimezoneProvider` resolves the timezone
- **THEN** `useTimezone()` returns `"America/New_York"`

#### Scenario: URL param is validated

- **GIVEN** the URL contains `?tz=invalid-tz-name`
- **WHEN** `TimezoneProvider` resolves the timezone
- **THEN** the URL param is ignored (treated as absent)
- **AND** the fallback chain continues to Step 2

#### Scenario: Preferences value is used when no URL override

- **GIVEN** no `?tz=` URL param is present
- **AND** `GET /api/settings/general` returns `timezone: "Europe/London"`
- **WHEN** `TimezoneProvider` resolves the timezone
- **THEN** `useTimezone()` returns `"Europe/London"`

#### Scenario: UTC fallback when preference is absent

- **GIVEN** no `?tz=` URL param is present
- **AND** `GET /api/settings/general` returns no timezone or an empty string
- **WHEN** `TimezoneProvider` resolves the timezone
- **THEN** `useTimezone()` returns `"UTC"`
- **AND** this is NOT the browser's locale timezone

#### Scenario: Loading state while preference fetches

- **GIVEN** `GET /api/settings/general` has not yet responded
- **WHEN** a component calling `useTimezone()` renders
- **THEN** `useTimezone()` returns `"UTC"` as the explicit pending default
- **AND** any `<Time>` component in pending state renders a loading skeleton
  rather than displaying a time value derived from browser locale

### Requirement: `<Time>` component uses useTimezone internally

The `<Time>` component (bu-v1tt2.2) SHALL call `useTimezone()` internally to resolve the
rendering timezone. Pages and intermediate components SHALL NOT pass timezone props to
`<Time>` for the purpose of owner-timezone resolution.

Pages MAY pass an explicit `tz` prop to `<Time>` when the timestamp is semantically tied
to a specific timezone other than the owner's (e.g., a butler running in a different
configured timezone, or a historical event from a specific location). This is an override,
not the primary resolution path.

#### Scenario: Time renders in owner timezone by default

- **GIVEN** `TimezoneProvider` is mounted with `"Asia/Singapore"`
- **AND** a page renders `<Time value={someIsoString} />`
- **WHEN** the component renders
- **THEN** the time value is formatted in `"Asia/Singapore"` without the page passing any timezone prop

#### Scenario: Explicit tz prop overrides context

- **GIVEN** `TimezoneProvider` is mounted with `"Asia/Singapore"`
- **AND** a page renders `<Time value={someIsoString} tz="Europe/London" />`
- **WHEN** the component renders
- **THEN** the time value is formatted in `"Europe/London"` (explicit prop wins)

### Requirement: Chronicles timezone provider SHALL remain isolated during transition

The `ChroniclesTimezoneProvider` / `useChroniclesTimezone()` pair SHALL remain in place in `frontend/src/components/chronicles/` during the transitional period.
The Chronicles page has its own data lifecycle and is a self-contained workspace.

Future work SHALL consolidate Chronicles to read from the shell-level `TimezoneContext`.
Until that consolidation lands, the two providers MUST coexist without conflict. Components under
`frontend/src/components/chronicles/` continue to use `useChroniclesTimezone()`; all other
components MUST use `useTimezone()`.

#### Scenario: Chronicles components still use their own provider

- **GIVEN** the shell-level `TimezoneProvider` is mounted
- **AND** the Chronicles page mounts `ChroniclesTimezoneProvider`
- **WHEN** a Chronicles child calls `useChroniclesTimezone()`
- **THEN** it receives the timezone from `ChroniclesTimezoneProvider`, not from the shell context

## Source References

- Non-Negotiable Rule #4 (Time is a typed primitive) — `about/heart-and-soul/design-language.md`
- Engineering bar FAIL-FAST principle — `about/craft-and-care/engineering-bar.md`
- RFC 0007 (Dashboard and API surface) — `about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md`
- `openspec/specs/user-preferences/spec.md` — `preferences:general_timezone` predicate
