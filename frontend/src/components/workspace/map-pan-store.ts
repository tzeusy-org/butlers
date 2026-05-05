// ---------------------------------------------------------------------------
// Map pan store — bu-ig72b.24 / extracted to workspace as part of bu-e8b5w.2
//
// Provides a lightweight React context that decouples a map pan trigger from
// the MapWidget imperative API. The component that owns the map registers its
// flyTo function via useRegisterMapPan(); any other component in the tree
// requests a pan via useMapPanTo().
//
// Performance note (bu-bhuk7): useMapPanContextValue() wraps the returned
// object in useMemo so that MapPanContext.Provider receives a stable reference
// on every parent render.  Without the memo, a new { register, panTo } object
// literal is produced each render cycle, causing every context consumer to
// re-render even when neither callback has changed.
// ---------------------------------------------------------------------------

import { createContext, useCallback, useContext, useMemo, useRef } from "react"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Static no-op function; provides a stable reference for hooks with no provider. */
const NO_OP = () => {}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Callback signature registered by the map widget. */
export type MapPanFn = (lat: number, lng: number) => void

interface MapPanContextValue {
  /** Called by the map widget on mount to register its flyTo implementation. */
  register: (fn: MapPanFn) => void
  /** Called by any consumer to request a pan. */
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
 * Call this once in the parent component that owns both the trigger and the
 * map widget, then spread the result into <MapPanContext.Provider value={...}>.
 *
 * Example:
 *   const mapPanValue = useMapPanContextValue()
 *   return <MapPanContext.Provider value={mapPanValue}>...</MapPanContext.Provider>
 *
 * The returned object is memoised so the Provider value reference is stable
 * across parent re-renders — both `register` and `panTo` are stable
 * useCallback refs, so the useMemo dependency array never changes.
 */
export function useMapPanContextValue(): MapPanContextValue {
  const fnRef = useRef<MapPanFn | null>(null)

  const register = useCallback((fn: MapPanFn) => {
    fnRef.current = fn
  }, [])

  const panTo = useCallback((lat: number, lng: number) => {
    fnRef.current?.(lat, lng)
  }, [])

  return useMemo(() => ({ register, panTo }), [register, panTo])
}

// ---------------------------------------------------------------------------
// Consumer hooks
// ---------------------------------------------------------------------------

/** Used by the map widget to register its flyTo function with the store. */
export function useRegisterMapPan(): (fn: MapPanFn) => void {
  const ctx = useContext(MapPanContext)
  // Return a no-op if there is no provider (e.g. in tests / standalone usage).
  return ctx?.register ?? NO_OP
}

/** Used by any consumer to request a map pan. */
export function useMapPanTo(): (lat: number, lng: number) => void {
  const ctx = useContext(MapPanContext)
  return ctx?.panTo ?? NO_OP
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

export { MapPanContext }
