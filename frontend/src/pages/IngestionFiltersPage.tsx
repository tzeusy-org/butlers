/**
 * IngestionFiltersPage — route component for /ingestion/filters.
 *
 * Thin wrapper around the existing FiltersTab component. The redesign
 * promotes this to a first-class sub-route without modifying the inner
 * component (per D7: "Sub-route wrappers preserve existing FiltersTab").
 *
 * Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
 *       ingestion-ui-information-architecture/spec.md §"Sub-route wrappers
 *       preserve existing components"
 */

import { FiltersTab } from '@/components/switchboard/FiltersTab'

export default function IngestionFiltersPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Filters</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Ingestion filter rules — configure routing policy for incoming events.
        </p>
      </div>
      <FiltersTab />
    </div>
  )
}
