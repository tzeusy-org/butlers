## Why

The dashboard's `<Time>` component (bu-v1tt2.2) and every page that renders timestamps need
to know the owner's configured timezone. Today this contract exists only for the Chronicles
page — it reads `GET /api/settings/general` and passes the result down through
`ChroniclesTimezoneProvider`. Every other page either ignores timezone entirely, or would
have to re-invent the same data-fetch independently. This change documents the
**dashboard-wide** contract so that all future `<Time>` consumers share one source of truth
and one React context, and so that the fallback chain is explicit and deliberate rather than
accidental.

## What Changes

- **New capability**: `owner-timezone-context` — a dashboard-wide React context
  (`TimezoneContext`) and `useTimezone()` hook, placed under `frontend/src/lib/` or
  `frontend/src/components/ui/`, mirroring the shape of the existing
  `ChroniclesTimezoneProvider` / `useChroniclesTimezone()` pair but elevated to shell scope.
- **Modified capability**: `dashboard-shell` — the shell spec gains a new cross-cutting
  contract section documenting how all pages read the owner's timezone: the provider placement
  in the provider hierarchy, the data source (`GET /api/settings/general`), and the
  three-step fallback chain (`?tz=` URL param → preferences general_timezone → UTC).
- **`<Time>` consumption contract**: `<Time>` calls `useTimezone()` internally; pages do NOT
  pass tz props down to their children.
- **Deliberate omission**: browser locale timezone is NOT in the fallback chain. If the
  preference is not yet loaded, the component renders a loading skeleton or 'pending' state.
- **Chronicles transitional note**: `ChroniclesTimezoneProvider` and `useChroniclesTimezone()`
  remain in place as a transitional provider (the Chronicles page is a self-contained workspace
  with its own data lifecycle). Future consolidation is a follow-up.
- **Follow-up bead**: If `GET /api/preferences` (the `user-preferences` spec endpoint) does
  not exist yet, a follow-up bead is created and linked, and the spec documents the endpoint
  shape needed.

## Capabilities

### New Capabilities

- `owner-timezone-context`: Dashboard-wide React context and `useTimezone()` hook. Covers
  provider placement, data source, three-step fallback chain, loading/pending behavior,
  `<Time>` consumption contract, and transitional Chronicles isolation.

### Modified Capabilities

- `dashboard-shell`: Gains a "Owner Timezone Resolution" cross-cutting contract section.
  The shell spec already owns the provider hierarchy (StrictMode → QueryClientProvider →
  RouterProvider); the timezone provider sits inside QueryClientProvider so it can use
  TanStack Query. This is a spec-level behavior addition.

## Impact

- **New files**: `openspec/changes/owner-timezone-frontend-contract/specs/owner-timezone-context/spec.md`
- **Delta file**: `openspec/changes/owner-timezone-frontend-contract/specs/dashboard-shell/spec.md`
  (delta to the existing `openspec/specs/dashboard-shell/spec.md`)
- **Frontend** (implementation bead bu-v1tt2.2, already closed): `<Time>` acceptance
  criteria updated via `bd update bu-v1tt2.2 --append-notes` to reference this contract.
- **No backend API changes** required if `GET /api/settings/general` already exists (it does;
  see `frontend/src/api/client.ts:3211`). The `user-preferences` general_timezone path is
  documented as the future source of truth; a follow-up bead tracks endpoint readiness.
- **No database changes**.
