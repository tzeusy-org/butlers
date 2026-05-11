// ---------------------------------------------------------------------------
// ButlerActivityTab — bu-iuol4.16
//
// Activity bespoke tab for the butler detail page. Replaces the "coming soon"
// stub at ButlerDetailPage.tsx (activity TabsContent).
//
// Layout (4-col panel grid, 3 rows):
//   Row 1: KPI quartet (span 4)
//     — sessions count, p50 ms, p95 ms, errors count
//   Row 2: Activity panel (span 4)
//     — RangeToggle toggles between:
//         24h → ActivityStripe (hourly)
//         7d/30d → DayBars (daily)
//   Row 3: Session kind breakdown (span 4)
//     — list of {kind, count} from session-kinds endpoint
//
// Doctrine:
//   - No raw oklch/hex
//   - Sentence case labels
//   - .tnum on all numeric values
//   - ErrorLine pattern for per-panel errors
//   - Outer grid: grid-cols-1 lg:grid-cols-4
//   - KPI grid: grid-cols-2 sm:grid-cols-4
// ---------------------------------------------------------------------------

import { useState } from "react"
import { AlertTriangle } from "lucide-react"

import { Skeleton } from "@/components/ui/skeleton"
import { RangeToggle, type RangeValue } from "@/components/ui/range-toggle"
import { ActivityStripe } from "@/components/butlers/ActivityStripe"
import { DayBars } from "@/components/butlers/DayBars"
import { KpiCell, Panel } from "@/components/butler-detail/atoms"
import {
  useButlerHourlyActivity,
  useButlerDailyActivity,
  useButlerSessionKinds,
  useButlerLatencyStats,
} from "@/hooks/use-butler-analytics"

// ---------------------------------------------------------------------------
// ErrorLine — inline error indicator
// ---------------------------------------------------------------------------

function ErrorLine({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="flex items-center gap-1.5 text-sm text-destructive min-w-0"
      data-testid="error-state-line"
    >
      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="truncate">{children}</span>
    </p>
  )
}

// ---------------------------------------------------------------------------
// KPI quartet (Row 1)
// ---------------------------------------------------------------------------

interface KpiQuartetProps {
  /** Total sessions count from session-kinds data (sum of all kinds) */
  sessionsCount: number | null
  /** p50 latency in ms (null when unavailable) */
  p50Ms: number | null
  /** p95 latency in ms (null when unavailable) */
  p95Ms: number | null
  /** Error session count (null when unavailable) */
  errorsCount: number | null
  isLoading: boolean
  isError: boolean
}

function KpiQuartet({
  sessionsCount,
  p50Ms,
  p95Ms,
  errorsCount,
  isLoading,
  isError,
}: KpiQuartetProps) {
  if (isError) {
    return (
      <Panel title="Activity" span={4} testId="activity-kpi-panel">
        <ErrorLine>Could not load activity metrics.</ErrorLine>
      </Panel>
    )
  }

  const sessionsValue = isLoading ? "…" : sessionsCount != null ? String(sessionsCount) : "—"
  const p50Value = isLoading ? "…" : p50Ms != null ? String(p50Ms) : "—"
  const p95Value = isLoading ? "…" : p95Ms != null ? String(p95Ms) : "—"
  const errorsValue = isLoading ? "…" : errorsCount != null ? String(errorsCount) : "—"

  const errorsTone = errorsCount != null && errorsCount > 0 ? "red" : "fg"

  return (
    <Panel title="Activity" span={4} testId="activity-kpi-panel">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
        <KpiCell
          label="Sessions"
          value={<span data-testid="kpi-sessions">{sessionsValue}</span>}
        />
        <KpiCell
          label="p50 ms"
          value={<span data-testid="kpi-p50">{p50Value}</span>}
          sub={p50Ms == null && !isLoading ? "awaiting endpoint" : undefined}
        />
        <KpiCell
          label="p95 ms"
          value={<span data-testid="kpi-p95">{p95Value}</span>}
          sub={p95Ms == null && !isLoading ? "awaiting endpoint" : undefined}
        />
        <KpiCell
          label="Errors"
          value={<span data-testid="kpi-errors">{errorsValue}</span>}
          tone={errorsTone}
          sub={errorsCount == null && !isLoading ? "unavailable" : undefined}
        />
      </div>
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// Activity panel (Row 2)
//
// Contains the RangeToggle and the chart (ActivityStripe for 24h, DayBars
// for 7d/30d). State is managed locally here.
// ---------------------------------------------------------------------------

interface ActivityPanelProps {
  butlerName: string
  range: RangeValue
  onRangeChange: (value: RangeValue) => void
}

function ActivityPanel({ butlerName, range, onRangeChange }: ActivityPanelProps) {
  const hourlyQuery = useButlerHourlyActivity(butlerName, 24)
  const dailyQuery7d = useButlerDailyActivity(butlerName, 7)
  const dailyQuery30d = useButlerDailyActivity(butlerName, 30)

  const header = (
    <div className="flex items-center justify-between gap-4 px-4 pt-3 pb-2 border-b border-border/40">
      <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
        Activity
      </span>
      <RangeToggle value={range} onChange={onRangeChange} />
    </div>
  )

  if (range === "24h") {
    const { data, isLoading, isError } = hourlyQuery
    const buckets = data?.data?.buckets ?? []
    // Build oldest-first 24-element counts array (API returns newest-first by hour_index)
    const counts: number[] = Array(24).fill(0)
    for (const b of buckets) {
      const idx = 23 - b.hour_index // hour_index 0 = newest → slot 23
      if (idx >= 0 && idx < 24) {
        counts[idx] = b.sessions_count
      }
    }

    return (
      <div
        className="relative flex flex-col border-r border-b border-border/60 col-span-4"
        data-testid="activity-chart-panel"
      >
        {header}
        <div className="flex-1 p-4">
          {isLoading ? (
            <div data-testid="loading-line">
              <Skeleton className="h-[22px] w-full rounded" />
            </div>
          ) : isError ? (
            <ErrorLine>Could not load hourly activity.</ErrorLine>
          ) : (
            <ActivityStripe counts={counts} className="w-full" />
          )}
        </div>
      </div>
    )
  }

  // 7d or 30d
  const windowDays = range === "7d" ? 7 : 30
  const { data, isLoading, isError } = range === "7d" ? dailyQuery7d : dailyQuery30d
  const buckets = data?.data?.buckets ?? []

  // Build a dense counts array for the window (days with no sessions stay 0)
  const counts: number[] = Array(windowDays).fill(0)
  if (buckets.length > 0) {
    const now = new Date()
    for (const b of buckets) {
      const bDate = new Date(b.date + "T00:00:00Z")
      const daysAgo = Math.round((now.getTime() - bDate.getTime()) / (1000 * 60 * 60 * 24))
      const idx = windowDays - 1 - daysAgo
      if (idx >= 0 && idx < windowDays) {
        counts[idx] = b.sessions_count
      }
    }
  }

  return (
    <div
      className="relative flex flex-col border-r border-b border-border/60 col-span-4"
      data-testid="activity-chart-panel"
    >
      {header}
      <div className="flex-1 p-4">
        {isLoading ? (
          <div data-testid="loading-line">
            <Skeleton className="h-8 w-full rounded" />
          </div>
        ) : isError ? (
          <ErrorLine>Could not load daily activity.</ErrorLine>
        ) : (
          <DayBars data={counts} height={48} className="w-full" />
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Kind breakdown panel (Row 3)
// ---------------------------------------------------------------------------

interface KindBreakdownPanelProps {
  butlerName: string
  windowDays: number
}

function KindBreakdownPanel({ butlerName, windowDays }: KindBreakdownPanelProps) {
  const { data, isLoading, isError } = useButlerSessionKinds(butlerName, windowDays)
  const kinds = data?.data?.kinds ?? []

  return (
    <Panel title="By kind" span={4} testId="activity-kind-panel" className="border-r-0">
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="flex items-center gap-2" data-testid="loading-line">
              <Skeleton className="h-3 w-28 rounded" />
              <Skeleton className="h-3 w-10 rounded" />
            </div>
          ))}
        </div>
      ) : isError ? (
        <ErrorLine>Could not load session kind breakdown.</ErrorLine>
      ) : kinds.length === 0 ? (
        <p
          className="text-sm text-muted-foreground"
          data-testid="empty-state-line"
        >
          No sessions in this window.
        </p>
      ) : (
        <ul data-testid="kind-breakdown-list">
          {kinds.map((item) => (
            <li
              key={item.kind}
              className="flex items-center justify-between py-1.5 border-b border-border/40 last:border-b-0"
              data-testid="kind-breakdown-row"
            >
              <span className="text-sm text-foreground truncate" data-testid="kind-label">
                {item.kind}
              </span>
              <span className="font-mono text-sm tnum text-muted-foreground shrink-0 ml-4" data-testid="kind-count">
                {item.count}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

// ---------------------------------------------------------------------------
// ButlerActivityTab — entry point
// ---------------------------------------------------------------------------

export interface ButlerActivityTabProps {
  butlerName: string
}

export default function ButlerActivityTab({ butlerName }: ButlerActivityTabProps) {
  const [range, setRange] = useState<RangeValue>("24h")

  // Determine window_days for session-kinds and latency-stats based on range
  const windowDays = range === "24h" ? 1 : range === "7d" ? 7 : 30

  // KPI data sources
  const kindsQuery = useButlerSessionKinds(butlerName, windowDays)
  const latencyStats = useButlerLatencyStats(butlerName, windowDays)

  // Derive sessions count as the sum across all kinds
  const sessionsCount = kindsQuery.data?.data?.kinds.reduce((s, k) => s + k.count, 0) ?? null

  // Derive error sessions count from trigger_source = "error" kind if available
  // (graceful: null when not available)
  const errorsCount = kindsQuery.data?.data?.kinds.find((k) => k.kind === "error")?.count ?? null

  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4"
      data-testid="butler-activity-tab"
    >
      {/* Row 1: KPI quartet */}
      <KpiQuartet
        sessionsCount={kindsQuery.isLoading ? null : sessionsCount}
        p50Ms={latencyStats.data?.p50_ms ?? null}
        p95Ms={latencyStats.data?.p95_ms ?? null}
        errorsCount={kindsQuery.isLoading ? null : errorsCount}
        isLoading={kindsQuery.isLoading}
        isError={kindsQuery.isError}
      />

      {/* Row 2: Activity chart with RangeToggle */}
      <ActivityPanel
        butlerName={butlerName}
        range={range}
        onRangeChange={setRange}
      />

      {/* Row 3: Session kind breakdown */}
      <KindBreakdownPanel butlerName={butlerName} windowDays={windowDays} />
    </div>
  )
}
