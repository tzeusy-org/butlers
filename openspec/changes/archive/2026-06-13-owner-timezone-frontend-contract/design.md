> **RECONCILIATION NOTE (bu-mjcmk, archived 2026-06-13).** Implemented under
> bu-ldj6y (commit `11c01dc51`) with materially different decisions than the
> design below. The delta specs were reconciled to the as-built code and synced
> into `openspec/specs/`. The design text below is preserved for history; the
> synced `owner-timezone-context` / `dashboard-shell` specs are authoritative.
> See the proposal banner for the full divergence list (provider path/name,
> `DEFAULT_TZ="Asia/Singapore"`, no `?tz=` chain, Chronicles consolidated via aliases).

## Context

The dashboard currently has timezone-awareness only in Chronicles. The existing pattern:

1. `ChroniclesPage` calls `useGeneralSettings()` (→ `GET /api/settings/general`) and reads
   `data.timezone`.
2. It passes the IANA name into `ChroniclesTimezoneProvider`.
3. Any Chronicles child calls `useChroniclesTimezone()` to get the value.

That context pair lives under `frontend/src/components/chronicles/`, which means it is
semantically scoped to Chronicles and cannot be reused. The `<Time>` component (bu-v1tt2.2)
is a dashboard-wide primitive that must render in the owner's timezone on every page, so it
needs a dashboard-wide context.

`GET /api/settings/general` already exists and already returns `timezone` (verified in
`frontend/src/api/client.ts:3211` and `frontend/src/api/types.ts:2714-2715`). A `user-preferences`
spec exists (`openspec/specs/user-preferences/spec.md`) and references a
`preferences:general_timezone` predicate stored in the memory module, but no
`GET /api/preferences` dashboard endpoint exists yet. The current and correct data source
for the frontend is `GET /api/settings/general`.

## Goals / Non-Goals

**Goals:**
- Define a single `TimezoneContext` + `useTimezone()` pair that any dashboard page or
  component can consume.
- Document the exact three-step fallback chain with rationale for each step.
- Eliminate browser locale from the fallback chain entirely.
- Specify provider placement in the existing app provider hierarchy.
- Specify how `<Time>` uses the hook (internally, not via props).
- Document the transitional Chronicles isolation and the consolidation path.

**Non-Goals:**
- Implementing `TimezoneContext` in code (that is bu-v1tt2.2's domain and is closed).
- Adding a `GET /api/preferences` dashboard endpoint (tracked as a follow-up bead).
- Changing the `ChroniclesTimezoneProvider` during this change.
- Multi-user or role-scoped timezone preferences.

## Decisions

### Decision 1 — Use a shell-level provider, not a per-page provider

**Chosen:** Mount `TimezoneProvider` once, inside `QueryClientProvider` (so it can use
TanStack Query) but outside `RouterProvider` (so it spans all routes).

**Alternative considered:** Each page fetches its own timezone. Rejected because it
multiplies redundant API calls and means some pages miss the context entirely until
individually updated.

**Alternative considered:** Store timezone in a Zustand or other global store. Rejected
because TanStack Query already caches `GET /api/settings/general` at a 60s stale time;
adding a second store layer duplicates cache management.

### Decision 2 — Three-step fallback chain, FAIL-FAST on unknown

**Chosen:**
```
?tz=<IANA>          (explicit URL override, for shareable/bookmarked links)
  → GET /api/settings/general .timezone
  → UTC             (explicit, deterministic)
```

**Browser locale is deliberately excluded.** Rationale: browser locale is implicit,
silently wrong for the owner (who may be traveling or using a shared device), and
produces different output for the same URL — violating the shareable-link use case.
UTC as the final fallback is explicit and reproducible. Any consumer that sees UTC and
expected something else will investigate, not silently accept a wrong timezone.

**While loading:** render a loading skeleton or display a 'pending' placeholder rather
than guessing with browser locale. This is the FAIL-FAST principle: incorrect assumptions
should be surfaced, not silently smoothed over (`about/craft-and-care/engineering-bar.md`).

### Decision 3 — `useTimezone()` is called internally by `<Time>`, not threaded as props

**Chosen:** `<Time>` calls `useTimezone()` internally. Pages do not accept or pass `tz`
props for timezone resolution (they may accept `tz` as an override for special cases where
a timestamp is explicitly in a butler timezone, not the owner timezone — that is a separate
concern).

**Rationale:** Props threading is a maintenance liability. Every intermediate component
between a page and a leaf `<Time>` would need to participate in the chain. A single
context call at the leaf is clean and explicit.

### Decision 4 — Placement: `frontend/src/lib/timezone-context.tsx`

**Chosen:** New files live under `frontend/src/lib/` (shared utilities and contexts) rather
than `frontend/src/components/ui/` (UI primitives). The context is not a visual component;
it is a data-layer utility that happens to be React context. `lib/` matches the existing
conventions for non-visual shared code.

**Internal modules:** Three files mirror the Chronicles pattern:
- `lib/timezone-context.tsx` — `TimezoneProvider` (thin context injector)
- `lib/timezone-context-internal.ts` — `TimezoneContext = createContext("UTC")`
- `lib/use-timezone.ts` — `useTimezone()` hook

### Decision 5 — Chronicles isolation is transitional, not permanent

The `ChroniclesTimezoneProvider` / `useChroniclesTimezone()` pair is retained as-is.
Chronicles is a self-contained workspace with its own data lifecycle. Consolidation (making
Chronicles read from the shell-level context) is a follow-up task, not part of this contract
change. The spec notes this explicitly to prevent confusion.

## Risks / Trade-offs

**[Risk] `GET /api/settings/general` latency adds a render delay** → Mitigation: the hook
uses TanStack Query with `staleTime: 60_000` (already set in `use-general-settings.ts`).
While loading, `<Time>` renders a skeleton. After first load the value is cached for 60s.

**[Risk] `?tz=` URL param could be spoofed with an invalid IANA name** → Mitigation: the
provider validates the URL param against `Intl.supportedValuesOf('timeZone')` or falls
through to the preferences value on invalid input. Invalid param → behave as if absent.

**[Risk] Two timezone contexts exist simultaneously (Chronicles + shell)** → This is
documented and intentional. The risk is confusion during the transitional period. Mitigation:
the spec explicitly flags this, and the consolidation follow-up bead provides a clear path.

**[Risk] `GET /api/preferences` does not exist yet** → The frontend does NOT call
`GET /api/preferences`. It calls `GET /api/settings/general`, which already exists. The
`user-preferences` spec's `general_timezone` predicate is the backend representation; the
settings endpoint is the frontend-accessible read path. A follow-up bead tracks whether a
unified `GET /api/preferences` endpoint should be added.

## Open Questions

- **Consolidation timing**: When should Chronicles switch from its own provider to the
  shell-level `TimezoneContext`? Defer to a dedicated follow-up bead (documented in tasks).
- **`?tz=` validation**: Should the URL-param override be case-normalized (e.g. `Asia/singapore`
  → reject vs. normalize)? Recommendation: reject invalid strings and treat as absent, since
  IANA names are case-sensitive by specification.
