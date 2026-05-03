## ADDED Requirements

### Requirement: Map Widget Style-Load Resilience

The map widget SHALL defer source and layer mutations until the
underlying MapLibre tile style has finished loading. Calling
`map.addSource(...)` or `map.addLayer(...)` synchronously after
`new maplibreGl.Map(...)` throws `Style is not done loading` because
the style fetch is asynchronous; that exception bubbles into
`MapErrorBoundary` and renders the user-visible `Failed to load the
map. Try again` fallback even when valid trail or point data exists.

#### Scenario: Trail-only first mount succeeds

- **WHEN** the Chronicles page mounts the map widget for the first
  time with `points = []` and `trailPoints` containing two or more
  coordinate pairs
- **THEN** the map canvas SHALL render the OSM tile layer plus the
  trail line layer
- **AND** the widget SHALL NOT fall through to the
  `MapErrorBoundary` fallback

#### Scenario: Trail data updates after style is loaded use setData

- **WHEN** the map style has already loaded AND `trailPoints` updates
- **THEN** the existing trail GeoJSON source SHALL be updated via
  `setData(...)` rather than re-added
- **AND** no re-mount of the map instance SHALL occur for trail-only
  changes
