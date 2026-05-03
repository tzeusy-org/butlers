## ADDED Requirements

### Requirement: Owner Timezone Resolution (cross-cutting shell contract)

The dashboard application shell SHALL provide a dashboard-wide owner timezone context.
This is a cross-cutting contract that all pages and components depend on; it lives here
because the shell spec owns the provider hierarchy and all shell-level infrastructure.

#### Scenario: TimezoneProvider is mounted at shell level

- **WHEN** the application boots
- **THEN** `TimezoneProvider` (from `frontend/src/lib/timezone-context.tsx`) is mounted
  inside `QueryClientProvider` and outside `RouterProvider`
- **AND** every route in the application can call `useTimezone()` without any per-page setup
- **AND** the timezone context is initialized before any route renders

#### Scenario: Provider hierarchy with timezone context

- **WHEN** the `App` component renders
- **THEN** the provider order from outermost to innermost is:
  `StrictMode` > `QueryClientProvider` > `TimezoneProvider` > `RouterProvider`
- **AND** `TimezoneProvider` is placed after `QueryClientProvider` so it can use
  `useGeneralSettings()` (TanStack Query) to fetch the owner's timezone from
  `GET /api/settings/general`

#### Scenario: Timezone source of truth is GET /api/settings/general

- **WHEN** `TimezoneProvider` resolves the owner's timezone
- **THEN** the primary source is `GET /api/settings/general` → `.timezone` (IANA name)
- **AND** the endpoint used is the existing general settings endpoint (not a new endpoint)
- **AND** no additional API endpoint is required for timezone resolution at this time

#### Scenario: Fallback chain is deterministic and explicit

- **WHEN** `TimezoneProvider` resolves the timezone
- **THEN** the three-step fallback chain is applied in order:
  1. `?tz=<IANA>` URL parameter (explicit per-link override for shareable URLs)
  2. `GET /api/settings/general` → `.timezone` (owner-configured preference)
  3. `"UTC"` (explicit, deterministic default)
- **AND** browser locale (`Intl.DateTimeFormat().resolvedOptions().timeZone`) is
  **never** used at any step in the chain
- **AND** an invalid or unrecognized `?tz=` value causes the chain to skip to Step 2

#### Scenario: No browser locale in any fallback step

- **GIVEN** the `?tz=` URL param is absent
- **AND** `GET /api/settings/general` fails or returns an empty timezone
- **WHEN** `TimezoneProvider` resolves the timezone
- **THEN** `useTimezone()` returns `"UTC"`, never the browser's local timezone

#### Scenario: Loading state is explicit, not locale-guessed

- **GIVEN** `GET /api/settings/general` is in-flight
- **WHEN** a component calls `useTimezone()` before the response arrives
- **THEN** the hook returns `"UTC"` as the explicit pending value
- **AND** any `<Time>` component in this state renders a loading skeleton
- **AND** the skeleton is replaced with the real value once the fetch completes

#### Scenario: Full cross-cutting contract reference

- **WHEN** any dashboard page or component renders a timestamp
- **THEN** it uses `<Time>` which calls `useTimezone()` internally
- **AND** pages do NOT thread timezone as a prop to child components
- **AND** the behavior is defined by the `owner-timezone-context` capability spec

For the full specification of `TimezoneContext`, `useTimezone()`, the fallback chain, the
`<Time>` consumption contract, and the Chronicles transitional isolation, see:
`openspec/specs/owner-timezone-context/spec.md` (once synced from this change).
