## Why

The canonical Chronicles dashboard spec still promises an unkeyed OpenStreetMap tile layer even
though the shipped map uses CARTO raster basemaps and can append a browser-visible CARTO key. That
drift obscures the real attribution, theme, and client-key boundaries, while a direct baseline edit
would erase clauses from the archived change ledger.

## What Changes

- Supersede the stale MapLibre/OpenStreetMap dependency requirement through OpenSpec rather than
  rewriting its archived snapshot or the canonical baseline by hand.
- Specify CARTO's label-free light and dark raster styles, visible OpenStreetMap and CARTO
  attribution, and the optional browser-visible key behavior already shipped.
- Require a configured non-blank key to be trimmed and URL-encoded into every tile URL's `key`
  query parameter, while absent or blank keys leave every URL unchanged.
- Reconcile the map style-load requirement's stale OSM wording while preserving all unrelated
  resilience scenarios and clauses.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-chronicles`: replace the superseded OpenStreetMap dependency contract with the
  observed CARTO raster-basemap contract and reconcile the dependent style-load wording.

## Impact

OpenSpec contract artifacts plus focused regression assertions. The shipped implementation lives
in `frontend/src/components/chronicles/carto-basemap.ts`, while the existing coverage is tightened
in `frontend/src/components/chronicles/MapWidget.test.tsx`; no frontend runtime, dependency,
credential value, Compose wiring, deployment, or pre-existing archived OpenSpec snapshot changes
are in scope.
