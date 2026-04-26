// ---------------------------------------------------------------------------
// Map pan store — bu-ig72b.24
//
// Provides a lightweight React context that decouples the Gantt click handler
// from the MapWidget imperative API.  The MapWidgetInner registers its flyTo
// function via useRegisterMapPan(); any other component in the tree reads it
// via useMapPanTo().
//
// Design constraints (from design.md §D12 / Open Questions):
//   - Calendar episode click → pan map if location parses as "lat,lng".
//   - No geocoding service.  Unparseable locations are a silent no-op.
//   - This store MUST NOT conflict with the bu-ig72b.23 playhead store; it
//     lives in a separate context and separate file.
// ---------------------------------------------------------------------------

import { createContext, useCallback, useContext, useRef } from "react"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Callback signature registered by MapWidgetInner. */
export type MapPanFn = (lat: number, lng: number) => void

interface MapPanContextValue {
  /** Called by MapWidgetInner on mount to register its flyTo implementation. */
  register: (fn: MapPanFn) => void
  /** Called by any consumer (e.g. Gantt click handler) to request a pan. */
  panTo: (lat: number, lng: number) => void
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const MapPanContext = createContext<MapPanContextValue | null>(null)
MapPanContext.displayName = "MapPanContext"

// ---------------------------------------------------------------------------
// Provider factory (hook-based, no class needed)
// ---------------------------------------------------------------------------

/**
 * Returns props for the MapPanContext.Provider value.
 *
 * Call this once in the parent component that owns both the Gantt and Map
 * widgets, then spread the result into <MapPanContext.Provider value={...}>.
 *
 * Example:
 *   const mapPanValue = useMapPanContextValue()
 *   return <MapPanContext.Provider value={mapPanValue}>...</MapPanContext.Provider>
 */
export function useMapPanContextValue(): MapPanContextValue {
  const fnRef = useRef<MapPanFn | null>(null)

  const register = useCallback((fn: MapPanFn) => {
    fnRef.current = fn
  }, [])

  const panTo = useCallback((lat: number, lng: number) => {
    fnRef.current?.(lat, lng)
  }, [])

  return { register, panTo }
}

// ---------------------------------------------------------------------------
// Consumer hooks
// ---------------------------------------------------------------------------

/** Used by MapWidgetInner to register its flyTo function with the store. */
export function useRegisterMapPan(): (fn: MapPanFn) => void {
  const ctx = useContext(MapPanContext)
  // Return a no-op if there is no provider (e.g. in tests / standalone usage).
  return ctx?.register ?? (() => {})
}

/** Used by GanttSwimlaneInner to request a map pan on episode click. */
export function useMapPanTo(): (lat: number, lng: number) => void {
  const ctx = useContext(MapPanContext)
  return ctx?.panTo ?? (() => {})
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

export { MapPanContext }
