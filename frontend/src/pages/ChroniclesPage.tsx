// ---------------------------------------------------------------------------
// ChroniclesPage — Chronicles dashboard (bu-ig72b)
//
// Widget regions will be filled by follow-up issues:
//   - Gantt area (bu-ig72b.5)
//   - Map area (bu-ig72b.14)
//   - Aggregations area (bu-ig72b.7)
//
// Time window state lives here and flows down to all three widget regions
// via props so each widget can filter its data to the selected range.
// Auto-refresh is gated by `pollingDisabled` from `useTimeWindow`:
//   - Today / recent windows (pollingDisabled=false): 30s polling by default.
//   - Older windows (pollingDisabled=true): no polling.
// ---------------------------------------------------------------------------

import { useTimeWindow } from "@/hooks/use-time-window"
import type { TimeWindow } from "@/hooks/use-time-window"
import { TimeWindowPicker } from "@/components/chronicles/TimeWindowPicker"
import { MapWidget } from "@/components/chronicles/MapWidget"
import { SourceStateBadgeStrip } from "@/components/chronicles/SourceStateBadgeStrip"
import { useAutoRefresh } from "@/hooks/use-auto-refresh"
import { AutoRefreshToggle } from "@/components/ui/auto-refresh-toggle"

// ---------------------------------------------------------------------------
// Widget-region placeholder — accepts the active time window
// ---------------------------------------------------------------------------

interface WidgetRegionProps {
  label: string
  description: string
  timeWindow: TimeWindow
}

// timeWindow is accepted so widget implementations can destructure it once
// they replace the placeholder. The prop is intentionally unused here.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function WidgetRegionPlaceholder({ label, description, timeWindow: _tw }: WidgetRegionProps) {
  return (
    <section aria-label={label} className="rounded-lg border bg-card p-6 min-h-48">
      <h2 className="text-sm font-medium text-muted-foreground mb-2">{label}</h2>
      <p className="text-sm text-muted-foreground italic">{description}</p>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChroniclesPage() {
  const timeWindow = useTimeWindow()
  const autoRefreshControl = useAutoRefresh(30_000)

  // When the time window ends more than 24h ago, disable polling entirely.
  // Otherwise use the user-configured interval (pause/resume still respected).
  // Widget regions (Gantt, Aggregations) will consume this once implemented.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const refetchInterval = timeWindow.pollingDisabled
    ? (false as const)
    : autoRefreshControl.refetchInterval

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Chronicles</h1>
          <p className="text-muted-foreground mt-1">
            Retrospective view of lived past time reconstructed from butler evidence.
          </p>
        </div>
        {!timeWindow.pollingDisabled && (
          <AutoRefreshToggle
            enabled={autoRefreshControl.enabled}
            interval={autoRefreshControl.interval}
            onToggle={autoRefreshControl.setEnabled}
            onIntervalChange={autoRefreshControl.setInterval}
          />
        )}
      </div>

      {/* Source adapter state badge strip */}
      <SourceStateBadgeStrip />

      {/* Time window picker */}
      <TimeWindowPicker window={timeWindow} />

      {/* Gantt area */}
      <WidgetRegionPlaceholder
        label="Gantt area"
        description="Timeline / Gantt widget — coming soon."
        timeWindow={timeWindow}
      />

      {/* Map area */}
      <section aria-label="Map area" className="rounded-lg border bg-card p-6">
        <h2 className="text-sm font-medium text-muted-foreground mb-4">Map area</h2>
        <MapWidget points={[]} />
      </section>

      {/* Aggregations area */}
      <WidgetRegionPlaceholder
        label="Aggregations area"
        description="Time aggregations widget — coming soon."
        timeWindow={timeWindow}
      />
    </div>
  )
}

