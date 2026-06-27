/**
 * IngestionHistoryPage — route component for /ingestion/history.
 *
 * Thin wrapper around the existing BackfillHistoryTab component. The redesign
 * promotes this to a first-class sub-route without modifying the inner
 * component (per D7: "Sub-route wrappers preserve existing BackfillHistoryTab").
 *
 * Spec: openspec/changes/redesign-ingestion-dispatch-console/specs/
 *       ingestion-ui-information-architecture/spec.md §"Sub-route wrappers
 *       preserve existing components"
 */

import { BackfillHistoryTab } from '@/components/switchboard/BackfillHistoryTab'

export default function IngestionHistoryPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">History</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Backfill and replay history. Track and manage historical ingestion runs.
        </p>
      </div>
      <BackfillHistoryTab />
    </div>
  )
}
