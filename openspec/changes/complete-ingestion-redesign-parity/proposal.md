## Why

The archived `redesign-ingestion-dispatch-console` change did not close the
visible redesign. The live `/ingestion` route still renders a conventional
card/table/tab surface, while the prototype in `pr/overview/ingestion-redesign/`
defines a Dispatch-language ledger, roster, connector-detail page, and pipeline
surface with bespoke hairline layouts.

This matters because `/ingestion` is the operator's audit surface for external
signals. A partial implementation that satisfies data plumbing but not the
visible operational model is misleading: it suggests the redesign landed while
the user still cannot inspect ingestion as a ledger, compare connector state as
a roster, or reason through the pipeline gates.

## What Changes

- Add a new capability, `dashboard-ingestion-dispatch-console`, as the binding
  page-level contract for `/ingestion`.
- Treat `pr/overview/ingestion-redesign/INGESTION_HANDOFF.md`,
  `DESIGN_LANGUAGE.md`, and the React prototype files as implementation inputs,
  not archival inspiration.
- Require first-class routes:
  - `/ingestion`
  - `/ingestion/connectors`
  - `/ingestion/connectors/:connectorType/:endpointIdentity`
  - `/ingestion/filters`
- Remove the legacy four-tab page shape from the redesigned path. There is no
  redesigned `/ingestion/history` tab; history is a Timeline range/view concern.
- Require bespoke Dispatch visual primitives for the ingestion surface. The
  ingestion page SHALL NOT be considered complete while its primary surfaces use
  shadcn `Card` chrome or the old `TabsTrigger` page-level tab switcher.
- Require live visual verification against the prototype assets before closure:
  desktop and mobile screenshots of the real app, route smoke coverage, and
  explicit checks that the old card/table shell is gone.
- Carry only the API work needed for the visible redesign: event ledger data,
  replay/payload access, connector roster/detail data, pipeline stats, priority
  contacts, channel defaults, and route/filter mutation surfaces.

## Capabilities

### New Capabilities

- `dashboard-ingestion-dispatch-console`: Dispatch-language page contract for
  the ingestion dashboard, including routes, visual primitives, Timeline ledger,
  Connectors roster/detail, Filters pipeline, data states, and visual parity
  verification.

### Modified Capabilities

- `dashboard-shell`: the full route map explicitly includes the ingestion
  sub-routes so route ownership is not hidden inside a legacy tab page.

### Related Capabilities

- `ingestion-event-registry`: supplies the canonical event and request context.
- `ingestion-policy`: supplies rule semantics for the Filters pipeline.
- `connector-base-spec`: supplies connector registry and detail state.
- `connector-oauth-scope-surface`: supplies the OAuth scope and reauth contract
  for `ReauthCallout` and `ScopeList` once ratified.

## Impact

**Frontend**
- Replace `frontend/src/pages/IngestionPage.tsx` with a route shell and
  Dispatch sub-nav.
- Replace or bypass the legacy card-heavy ingestion components:
  `TimelineTab.tsx`, `ConnectorsListPage.tsx`, `ConnectorsTab.tsx`,
  `ConnectorCard.tsx`, `FiltersTabContent.tsx`, and the switchboard
  card-based `FiltersTab`/`BackfillHistoryTab` where they leak into the
  redesigned route.
- Add ingestion-specific visual primitives and route components under
  `frontend/src/components/ingestion/`.
- Update `frontend/src/router.tsx` to mount all sub-routes and preserve
  backwards-compatible redirects from legacy `?tab=` URLs.

**Backend and API**
- Fill endpoint gaps only where the UI contract requires real data. Stubbed,
  synthetic, or forever-loading sections do not satisfy this change.
- Raw payload access remains audited and gated because it can expose PII.
- Replay actions remain idempotency-aware and channel-safe.

**Testing and verification**
- Component tests cover the route shell and each sub-route.
- API tests cover any new endpoint or response shape.
- Playwright covers live route navigation, drawer deep-linking, and legacy
  redirects.
- Screenshot artifacts compare the implemented live page with the prototype
  intent across desktop and mobile.

**Out of scope**
- Rewriting unrelated dashboard pages to the Dispatch language.
- Building a general design-token migration.
- Adding new connector providers.
- Implementing OAuth scope drift beyond the separately-owned
  `add-connector-oauth-scope-surface` change.

## Source References

- `pr/overview/ingestion-redesign/INGESTION_HANDOFF.md`
- `pr/overview/ingestion-redesign/DESIGN_LANGUAGE.md`
- `pr/overview/ingestion-redesign/ingestion-app.jsx`
- `pr/overview/ingestion-redesign/ingestion-v4.jsx`
- `pr/overview/ingestion-redesign/ingestion-connectors-a.jsx`
- `pr/overview/ingestion-redesign/ingestion-connector-detail.jsx`
- `pr/overview/ingestion-redesign/ingestion-filters.jsx`
- `about/heart-and-soul/design-language.md`
- `about/legends-and-lore/rfcs/0003-switchboard-routing-and-ingestion.md`
- `openspec/changes/archive/2026-05-19-redesign-ingestion-dispatch-console/`
- `openspec/changes/add-connector-oauth-scope-surface/`
