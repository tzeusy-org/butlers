// ---------------------------------------------------------------------------
// CARTO raster basemap style for the Chronicles map.
// Labels are omitted because the heatmap and markers carry the location story;
// labels would compete visually with those overlays.
// ---------------------------------------------------------------------------

import type { StyleSpecification } from "maplibre-gl"

const CARTO_LIGHT_TILES = [
  "https://a.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
  "https://b.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
  "https://c.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
  "https://d.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
]

const CARTO_DARK_TILES = [
  "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
  "https://b.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
  "https://c.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
  "https://d.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
]

const CARTO_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a>'

const configuredCartoBasemapApiKey = import.meta.env.VITE_CARTO_BASEMAP_API_KEY as
  | string
  | undefined

function withCartoApiKey(tileUrl: string, apiKey: string | undefined): string {
  const normalizedApiKey = apiKey?.trim()
  if (!normalizedApiKey) return tileUrl

  const separator = tileUrl.includes("?") ? "&" : "?"
  return `${tileUrl}${separator}key=${encodeURIComponent(normalizedApiKey)}`
}

export function cartoStyle(
  isDark: boolean,
  apiKey: string | undefined = configuredCartoBasemapApiKey,
): StyleSpecification {
  return {
    version: 8,
    sources: {
      basemap: {
        type: "raster",
        tiles: (isDark ? CARTO_DARK_TILES : CARTO_LIGHT_TILES).map((tileUrl) =>
          withCartoApiKey(tileUrl, apiKey),
        ),
        tileSize: 256,
        attribution: CARTO_ATTRIBUTION,
        maxzoom: 19,
      },
    },
    layers: [{ id: "basemap-tiles", type: "raster", source: "basemap" }],
  }
}
