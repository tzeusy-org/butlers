## 1. Spec Landing

- [ ] 1.1 Land this proposal, design, specs, and tasks via standard OpenSpec review
      workflow (PR review, owner confirmation).
- [ ] 1.2 Confirm `GET /api/settings/general` (already implemented) is the correct
      frontend-accessible read path for `preferences:general_timezone`. No new endpoint
      needed for Phase 1.
- [ ] 1.3 Once merged, run `openspec sync owner-timezone-frontend-contract` to promote
      `owner-timezone-context/spec.md` into `openspec/specs/owner-timezone-context/spec.md`
      and the `dashboard-shell/spec.md` delta into `openspec/specs/dashboard-shell/spec.md`.

## 2. Follow-Up Bead: GET /api/preferences endpoint

- [ ] 2.1 Verify whether a `GET /api/preferences` endpoint exists in the backend. As of
      this writing, it does not; the `user-preferences` spec describes the memory-module
      fact store but no dashboard REST endpoint.
- [ ] 2.2 If absent, create a follow-up bead (see bead created by bu-v1tt2.8 worker):
      "Add GET /api/preferences endpoint for dashboard frontend". The new endpoint should
      return active preference facts for the owner, including `preferences:general_timezone`.
      This supersedes `GET /api/settings/general` as the long-term source of truth.
- [ ] 2.3 Link the follow-up bead to bu-v1tt2.8 (discovered-from dependency).
- [ ] 2.4 Once the endpoint exists, update `TimezoneProvider` to prefer
      `GET /api/preferences?predicate=preferences:general_timezone` over
      `GET /api/settings/general` (or use a unified preferences hook).

## 3. Implementation: TimezoneContext infrastructure (builds on bu-v1tt2.2)

- [ ] 3.1 Create `frontend/src/lib/timezone-context-internal.ts`:
      `TimezoneContext = createContext<string>("UTC")`.
- [ ] 3.2 Create `frontend/src/lib/use-timezone.ts`:
      `useTimezone(): string` reads from `TimezoneContext`.
- [ ] 3.3 Create `frontend/src/lib/timezone-context.tsx`:
      `TimezoneProvider` reads `useGeneralSettings()`, applies the three-step fallback
      chain (`?tz=` URL param → general settings timezone → `"UTC"`), and injects the
      resolved value via `TimezoneContext.Provider`.
- [ ] 3.4 Validate `?tz=` URL param: check against `Intl.supportedValuesOf('timeZone')` if
      available (the API is optional — treat absence as if `supportedValuesOf` returned an
      empty set and fall through to Step 2). If available and the param is not in the list,
      treat the param as absent. Do NOT crash on browsers that lack `supportedValuesOf`.
- [ ] 3.5 Mount `TimezoneProvider` in `frontend/src/App.tsx` inside `QueryClientProvider`
      and outside `RouterProvider`.

## 4. Implementation: `<Time>` consumption wiring (bu-v1tt2.2 acceptance criteria update)

- [ ] 4.1 Confirm `<Time>` calls `useTimezone()` internally (per bu-v1tt2.2 implementation).
      If not yet wired, add the `useTimezone()` call inside `<Time>`.
- [ ] 4.2 Confirm `<Time>` renders a loading skeleton when the resolved timezone is still
      in the `"UTC"` pending state and the settings fetch is in-flight.
- [ ] 4.3 Update bu-v1tt2.2 acceptance criteria via:
      `bd update bu-v1tt2.2 --append-notes "Contract documented in openspec/changes/owner-timezone-frontend-contract (bu-v1tt2.8). <Time> MUST call useTimezone() internally; owner-timezone-context spec is the authority."`

## 5. Tests

- [ ] 5.1 Unit test `TimezoneProvider` fallback chain:
      - `?tz=America/New_York` → returns `"America/New_York"`.
      - `?tz=invalid` → falls through to settings value.
      - No URL param, settings returns `"Europe/London"` → returns `"Europe/London"`.
      - No URL param, settings returns empty → returns `"UTC"`.
      - No URL param, settings loading → returns `"UTC"`.
- [ ] 5.2 Unit test `useTimezone()`:
      - Returns context value when inside provider.
      - Returns `"UTC"` (not browser locale) when outside provider.
- [ ] 5.3 Integration test: `<Time>` renders in the timezone from context, not browser locale.

## 6. Chronicles Consolidation (follow-up, not this change)

- [ ] 6.1 Create a follow-up bead: "Consolidate Chronicles timezone provider to use
      shell-level TimezoneContext". Link to bu-v1tt2.8 as parent context.
- [ ] 6.2 When that bead is actioned, remove `ChroniclesTimezoneProvider` from
      `ChroniclesPage.tsx` and make Chronicles children call `useTimezone()` instead of
      `useChroniclesTimezone()`. Mark `useChroniclesTimezone()` and `ChroniclesTimezoneProvider`
      as deprecated, then delete them in a follow-on cleanup.

## 7. Spec Sync and Reconciliation

- [ ] 7.1 Run `openspec sync owner-timezone-frontend-contract` to merge delta specs.
- [ ] 7.2 Run `openspec validate` to confirm clean pass.
- [ ] 7.3 Verify all acceptance criteria in bu-v1tt2.8 against the delivered artifacts.
- [ ] 7.4 Close reconciliation once all checks pass.

## 8. Open Questions

- [ ] 8.1 Endpoint evolution: when `GET /api/preferences` lands, update the provider to
      prefer it over `GET /api/settings/general`. Document migration in the follow-up bead.
- [ ] 8.2 `?tz=` case normalization: IANA names are case-sensitive by specification. Decide
      whether to normalize (e.g. `asia/singapore` → reject) or accept case-insensitive and
      normalize. Recommendation: reject and treat as absent (simplest, strictest).
- [ ] 8.3 Chronicles consolidation timing: defer to a dedicated follow-up bead (see 6.1).
