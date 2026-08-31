## ADDED Requirements

### Requirement: MapLibre and CARTO Raster Basemap Contract

The page SHALL use the BSD-3-licensed `maplibre-gl` map renderer with CARTO's
label-free raster basemaps. The light theme SHALL use the label-free light
style, the dark theme SHALL use the label-free dark style, and both SHALL show
OpenStreetMap and CARTO attribution. An optional CARTO key SHALL remain
browser-visible client configuration and MUST be domain-restricted at the
provider rather than represented as a backend-only secret. Dependency
rationale SHALL remain documented in this spec and in the change's
`design.md`.

ID: REQ-dashboard-chronicles-003
Source: [Observed] `frontend/src/components/chronicles/carto-basemap.ts`; `docs/getting_started/dev-environment.md`
Scope: v1-mandatory

#### Scenario: License and tile source

- **WHEN** the Chronicles map widget renders in the light or dark theme
- **THEN** the renderer SHALL be the BSD-3-licensed open-source `maplibre-gl`
  fork
- **AND** the raster source SHALL use CARTO's matching `light_nolabels` or
  `dark_nolabels` style
- **AND** the map SHALL visibly attribute both OpenStreetMap and CARTO

#### Scenario: Configured key is URL encoded

- **WHEN** a non-blank browser-visible CARTO basemap key is configured
- **THEN** surrounding whitespace SHALL be removed from the configured value
- **AND** every light or dark raster tile URL SHALL append the URL-encoded
  value as its `key` query parameter
- **AND** the configured key MUST be restricted at CARTO to the dashboard's
  allowed browser domains

#### Scenario: Absent or blank key preserves tile URLs

- **WHEN** the CARTO basemap key is absent, empty, or whitespace-only
- **THEN** every light or dark raster tile URL SHALL remain unchanged
- **AND** no empty `key` query parameter SHALL be appended

#### Scenario: Bundle measurement

- **WHEN** the dependency is added
- **THEN** a measurement SHALL be recorded comparing pre-merge and
  post-merge frontend bundle sizes per
  `craft-and-care/performance-discipline.md` measure-before-optimize
- **AND** any regression SHALL be discussed in the PR description

## MODIFIED Requirements

### Requirement: Map Widget Style-Load Resilience

The map widget SHALL defer source and layer mutations until the underlying
MapLibre tile style has finished loading. Calling `map.addSource(...)` or
`map.addLayer(...)` synchronously after `new maplibreGl.Map(...)` throws
`Style is not done loading` because the style fetch is asynchronous; that
exception bubbles into `MapErrorBoundary` and renders the user-visible
`Failed to load the map. Try again` fallback even when valid trail or point
data exists.

ID: REQ-dashboard-chronicles-002
Source: [Observed] `frontend/src/components/chronicles/MapWidgetInner.tsx`
Scope: v1-mandatory

#### Scenario: Trail-only first mount succeeds

- **WHEN** the Chronicles page mounts the map widget for the first time with
  `points = []` and `trailPoints` containing two or more coordinate pairs
- **THEN** the map canvas SHALL render the CARTO raster tile layer plus the
  trail line layer
- **AND** the widget SHALL NOT fall through to the `MapErrorBoundary` fallback

#### Scenario: Trail data updates after style is loaded use setData

- **WHEN** the map style has already loaded AND `trailPoints` updates
- **THEN** the existing trail GeoJSON source SHALL be updated via
  `setData(...)` rather than re-added
- **AND** no re-mount of the map instance SHALL occur for trail-only changes

## REMOVED Requirements

### Requirement: MapLibre Dependency Justification

The page SHALL use `maplibre-gl` (BSD-3 license) for the map widget, with
OpenStreetMap as the tile source. Dependency rationale SHALL be documented in
this spec and in the change's design.md.

ID: REQ-dashboard-chronicles-001
Source: `openspec/changes/archive/2026-04-26-add-dashboard-chronicles/design.md`
Scope: v1-mandatory

#### Scenario: License and tile source

- **WHEN** `maplibre-gl` is added to `frontend/package.json`
- **THEN** the dependency SHALL be the BSD-3-licensed open-source fork
- **AND** the tile source SHALL be OpenStreetMap (no API token, no third-party
  hosted-tile commercial dependency)

#### Scenario: Bundle measurement

- **WHEN** the dependency is added
- **THEN** a measurement SHALL be recorded comparing pre-merge and post-merge
  frontend bundle sizes per `craft-and-care/performance-discipline.md`
  measure-before-optimize
- **AND** any regression SHALL be discussed in the PR description

**Reason**: This requirement's unkeyed OpenStreetMap-only contract is
superseded by `MapLibre and CARTO Raster Basemap Contract`, which records the
shipped CARTO provider, attribution, theme, and optional client-key behavior.

**Migration**: Preserve the existing scenario guarantees under the successor
requirement and use its CARTO-specific tile and key contract. No runtime or
operator migration is required.
