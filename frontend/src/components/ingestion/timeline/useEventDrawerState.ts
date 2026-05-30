/**
 * useEventDrawerState — URL-backed drawer open/close state.
 *
 * Opens the event drawer by setting `?event=<id>` in the URL.
 * Closing the drawer removes the `event` query parameter.
 * Compatible with useIngestionUrlState — both use useSearchParams.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Timeline URL opens an event drawer"
 */

import { useCallback } from 'react'
import { useSearchParams } from 'react-router'

/**
 * Read and write the `?event` URL parameter for the event drawer.
 *
 * Returns:
 * - eventId: the currently focused event id, or null when drawer is closed.
 * - openDrawer: set `?event=<id>` to open the drawer.
 * - closeDrawer: remove `?event` to close the drawer.
 */
export function useEventDrawerState() {
  const [searchParams, setSearchParams] = useSearchParams()
  const eventId = searchParams.get('event')

  const openDrawer = useCallback(
    (id: string) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev)
        next.set('event', id)
        return next
      })
    },
    [setSearchParams],
  )

  const closeDrawer = useCallback(() => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('event')
      return next
    })
  }, [setSearchParams])

  return { eventId, openDrawer, closeDrawer }
}
