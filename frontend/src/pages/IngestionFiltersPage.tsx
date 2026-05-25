/**
 * IngestionFiltersPage — route component for /ingestion/filters.
 *
 * Thin wrapper around the existing FiltersTab component. The redesign
 * promotes this to a first-class sub-route without modifying the inner
 * component.
 *
 * Uses Dispatch primitives and IngestionSubNav for consistent navigation.
 * No legacy TabsTrigger shell.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline"
 */

import { IngestionSubNav } from '@/components/ingestion/IngestionSubNav'
import { DispatchLayout, DispatchHeader, DispatchSurface } from '@/components/ingestion/dispatch'
import { FiltersTab } from '@/components/switchboard/FiltersTab'

export default function IngestionFiltersPage() {
  return (
    <DispatchLayout>
      <DispatchHeader
        eyebrow="Ingestion · filters"
        headline="Filters"
        description="Ingestion filter rules — configure routing policy for incoming events."
      />
      <IngestionSubNav />
      <DispatchSurface>
        <FiltersTab />
      </DispatchSurface>
    </DispatchLayout>
  )
}
