// ---------------------------------------------------------------------------
// SettingsSpendPage — /settings/spend  [bu-dvb7i §5.5]
//
// Layout:
//   - 4-cell KPI strip (MTD, projected EOM, ceiling, days remaining)
//   - Hand-rolled SVG forecast chart:
//       solid line = MTD actuals, dashed = projected to EOM, hairline = ceiling
//   - CSS breakdown bars (by butler / model / feature)
//   - Drag-to-reorder routing rules table with saved_7d column
//   - Anomaly detection: TODO placeholder (deferred §D13)
//
// Accepts no props — fetches all data internally.
// No chart library. SVG rendered by hand.
// ---------------------------------------------------------------------------

import { useState, useMemo, useRef, useEffect, useCallback } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card"
import { Page } from "@/components/ui/page"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { apiFetch } from "@/api/client"
import { useSpendStream } from "@/hooks/use-spend-stream"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ForecastDay {
  date: string
  cost_usd: number
  projected: boolean
}

interface ForecastData {
  days: ForecastDay[]
  projected_eom_usd: number
  days_in_month: number
  days_elapsed: number
  mtd_usd: number
  ceiling_usd: number | null
}

interface SpendRule {
  id: string
  position: number
  condition: Record<string, unknown>
  action: Record<string, unknown>
  saved_7d: number | null
  created_at: string
  updated_at: string
}

interface BreakdownData {
  by: string
  breakdown: Record<string, number>
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

function fetchForecast(): Promise<{ data: ForecastData }> {
  return apiFetch<{ data: ForecastData }>("/spend/forecast")
}

function fetchBreakdown(by: "butler" | "model" | "feature"): Promise<{ data: BreakdownData }> {
  return apiFetch<{ data: BreakdownData }>(`/spend/breakdown?by=${by}`)
}

function fetchRules(): Promise<{ data: SpendRule[] }> {
  return apiFetch<{ data: SpendRule[] }>("/spend/rules")
}

function updateCeiling(monthly_usd: number): Promise<unknown> {
  return apiFetch("/spend/ceiling", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ monthly_usd }),
  })
}

function deleteRule(id: string): Promise<void> {
  return apiFetch<void>(`/spend/rules/${id}`, { method: "DELETE" })
}

function reorderRule(id: string, position: number): Promise<unknown> {
  return apiFetch(`/spend/rules/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ position }),
  })
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function fmtUsd(n: number): string {
  if (n < 0.01) return "$0.00"
  return `$${n.toFixed(2)}`
}

function fmtUsdPrecise(n: number): string {
  return `$${n.toFixed(4)}`
}

// ---------------------------------------------------------------------------
// KPI Strip
// ---------------------------------------------------------------------------

interface KpiCellProps {
  label: string
  value: string
  sub?: string
  testId?: string
}

function KpiCell({ label, value, sub, testId }: KpiCellProps) {
  return (
    <div className="flex flex-col gap-1 p-4" data-testid={testId}>
      <span className="text-xs text-muted-foreground font-medium uppercase tracking-wide">{label}</span>
      <span className="text-2xl tabular-nums font-semibold">{value}</span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </div>
  )
}

function KpiStrip({ forecast }: { forecast: ForecastData }) {
  const daysRemaining = forecast.days_in_month - forecast.days_elapsed
  const pct =
    forecast.ceiling_usd != null && forecast.ceiling_usd > 0
      ? Math.min(100, Math.round((forecast.mtd_usd / forecast.ceiling_usd) * 100))
      : null

  return (
    <Card>
      <CardContent className="p-0">
        <div className="grid grid-cols-4 divide-x" data-testid="kpi-strip">
          <KpiCell
            testId="kpi-mtd"
            label="MTD Spend"
            value={fmtUsd(forecast.mtd_usd)}
            sub={`${forecast.days_elapsed} day${forecast.days_elapsed === 1 ? "" : "s"} elapsed`}
          />
          <KpiCell
            testId="kpi-projected-eom"
            label="Projected EOM"
            value={fmtUsd(forecast.projected_eom_usd)}
            sub={`${daysRemaining} day${daysRemaining === 1 ? "" : "s"} remaining`}
          />
          <KpiCell
            testId="kpi-ceiling"
            label="Monthly Ceiling"
            value={forecast.ceiling_usd != null ? fmtUsd(forecast.ceiling_usd) : "—"}
            sub={pct != null ? `${pct}% used` : undefined}
          />
          <KpiCell
            testId="kpi-days-in-month"
            label="Days in Month"
            value={String(forecast.days_in_month)}
            sub={`${forecast.days_elapsed} elapsed / ${daysRemaining} left`}
          />
        </div>
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Hand-rolled SVG forecast chart  [§5.5 — no chart library]
//
// Solid polyline = MTD actuals (projected=false)
// Dashed polyline = projected from today to EOM (projected=true)
// Hairline horizontal line = monthly ceiling
// ---------------------------------------------------------------------------

const CHART_W = 800
const CHART_H = 200
const CHART_PAD = { top: 16, right: 24, bottom: 32, left: 56 }

interface ForecastChartProps {
  days: ForecastDay[]
  ceiling_usd: number | null
}

function ForecastChart({ days, ceiling_usd }: ForecastChartProps) {
  if (days.length === 0) return null

  const innerW = CHART_W - CHART_PAD.left - CHART_PAD.right
  const innerH = CHART_H - CHART_PAD.top - CHART_PAD.bottom

  const maxCost = Math.max(...days.map((d) => d.cost_usd), ceiling_usd ?? 0, 0.001)
  const scaleX = (i: number) => CHART_PAD.left + (i / (days.length - 1 || 1)) * innerW
  const scaleY = (v: number) => CHART_PAD.top + innerH - (v / maxCost) * innerH

  // Split into actual vs projected segments
  const actualDays = days.filter((d) => !d.projected)
  const projectedDays = days.filter((d) => d.projected)

  // Find index of first projected day to connect line segments
  const firstProjIdx = days.findIndex((d) => d.projected)

  function toPoints(subset: ForecastDay[], offset = 0) {
    return subset
      .map((d, i) => `${scaleX(i + offset).toFixed(1)},${scaleY(d.cost_usd).toFixed(1)}`)
      .join(" ")
  }

  // Y-axis ticks
  const tickCount = 4
  const yTicks = Array.from({ length: tickCount + 1 }, (_, i) =>
    (maxCost * i) / tickCount
  )

  // X-axis labels: first day of month + today + last day
  const xLabels: Array<{ i: number; label: string }> = []
  if (days.length > 0) xLabels.push({ i: 0, label: days[0].date.slice(8) })
  if (firstProjIdx > 0) xLabels.push({ i: firstProjIdx - 1, label: "today" })
  if (days.length > 1) xLabels.push({ i: days.length - 1, label: days[days.length - 1].date.slice(8) })

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      width="100%"
      style={{ maxHeight: CHART_H }}
      aria-label="Spend forecast chart"
    >
      {/* Y-axis gridlines + labels */}
      {yTicks.map((v, i) => {
        const y = scaleY(v)
        return (
          <g key={i}>
            <line
              x1={CHART_PAD.left}
              y1={y}
              x2={CHART_W - CHART_PAD.right}
              y2={y}
              stroke="currentColor"
              strokeOpacity={0.08}
              strokeWidth={1}
            />
            <text
              x={CHART_PAD.left - 6}
              y={y}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize={10}
              fill="currentColor"
              fillOpacity={0.5}
            >
              {fmtUsd(v)}
            </text>
          </g>
        )
      })}

      {/* Monthly ceiling hairline */}
      {ceiling_usd != null && ceiling_usd > 0 && (
        <line
          x1={CHART_PAD.left}
          y1={scaleY(ceiling_usd)}
          x2={CHART_W - CHART_PAD.right}
          y2={scaleY(ceiling_usd)}
          stroke="var(--red, #ef4444)"
          strokeOpacity={0.6}
          strokeWidth={1}
          strokeDasharray="4 2"
        />
      )}

      {/* Actual spend — solid line */}
      {actualDays.length > 1 && (
        <polyline
          points={toPoints(actualDays, 0)}
          fill="none"
          stroke="var(--primary, #3b82f6)"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}

      {/* Projected spend — dashed line, connected from last actual */}
      {projectedDays.length > 0 && firstProjIdx >= 0 && (
        <polyline
          points={
            actualDays.length > 0
              ? `${scaleX(firstProjIdx - 1).toFixed(1)},${scaleY(actualDays[actualDays.length - 1].cost_usd).toFixed(1)} ` +
                toPoints(projectedDays, firstProjIdx)
              : toPoints(projectedDays, firstProjIdx)
          }
          fill="none"
          stroke="var(--primary, #3b82f6)"
          strokeOpacity={0.5}
          strokeWidth={2}
          strokeDasharray="6 4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}

      {/* X-axis labels */}
      {xLabels.map(({ i, label }) => (
        <text
          key={label}
          x={scaleX(i)}
          y={CHART_H - 6}
          textAnchor="middle"
          fontSize={10}
          fill="currentColor"
          fillOpacity={0.5}
        >
          {label}
        </text>
      ))}
    </svg>
  )
}

// ---------------------------------------------------------------------------
// Breakdown bars (CSS only, no chart lib)
// ---------------------------------------------------------------------------

interface BreakdownBarProps {
  label: string
  value: number
  maxValue: number
}

function BreakdownBar({ label, value, maxValue }: BreakdownBarProps) {
  const pct = maxValue > 0 ? (value / maxValue) * 100 : 0
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-40 truncate text-muted-foreground font-mono text-xs">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full bg-primary rounded-full transition-all"
          style={{ width: `${pct.toFixed(1)}%` }}
        />
      </div>
      <span className="w-20 text-right tabular-nums text-xs">{fmtUsdPrecise(value)}</span>
    </div>
  )
}

type BreakdownBy = "butler" | "model" | "feature"

function BreakdownSection() {
  const [by, setBy] = useState<BreakdownBy>("butler")
  const { data, isLoading } = useQuery({
    queryKey: ["spend-breakdown", by],
    queryFn: () => fetchBreakdown(by),
    refetchInterval: 120_000,
  })

  const entries = useMemo(() => {
    const breakdown = data?.data?.breakdown ?? {}
    return Object.entries(breakdown).sort(([, a], [, b]) => b - a)
  }, [data])
  const maxValue = entries[0]?.[1] ?? 0

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Spend Breakdown</CardTitle>
          <div className="flex gap-1">
            {(["butler", "model", "feature"] as BreakdownBy[]).map((dim) => (
              <Button
                key={dim}
                variant={by === dim ? "default" : "ghost"}
                size="sm"
                className="h-6 px-2 text-xs"
                onClick={() => setBy(dim)}
              >
                {dim}
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-4 w-full" />)}
          </div>
        ) : entries.length === 0 ? (
          <p className="text-xs text-muted-foreground">No spend data available.</p>
        ) : (
          <div className="space-y-2">
            {entries.map(([label, value]) => (
              <BreakdownBar key={label} label={label} value={value} maxValue={maxValue} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Routing rules table (drag-to-reorder)
// ---------------------------------------------------------------------------

interface RulesTableProps {
  rules: SpendRule[]
  onDelete: (id: string) => void
  onReorder: (id: string, newPosition: number) => void
}

function RulesTable({ rules, onDelete, onReorder }: RulesTableProps) {
  const dragIdRef = useRef<string | null>(null)

  function handleDragStart(e: React.DragEvent, id: string) {
    dragIdRef.current = id
    e.dataTransfer.effectAllowed = "move"
  }

  function handleDrop(e: React.DragEvent, targetPosition: number) {
    e.preventDefault()
    if (dragIdRef.current === null) return
    const dragRule = rules.find((r) => r.id === dragIdRef.current)
    if (!dragRule || dragRule.position === targetPosition) return
    onReorder(dragIdRef.current, targetPosition)
    dragIdRef.current = null
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault()
    e.dataTransfer.dropEffect = "move"
  }

  if (rules.length === 0) {
    return (
      <p className="text-xs text-muted-foreground py-4 text-center">
        No routing rules configured. Rules are evaluated top-to-bottom; first match wins.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-xs text-muted-foreground">
            <th className="text-left py-2 px-2 w-8">Pos</th>
            <th className="text-left py-2 px-2">Condition</th>
            <th className="text-left py-2 px-2">Action</th>
            <th className="text-right py-2 px-2">Saved 7d</th>
            <th className="text-right py-2 px-2 w-16"></th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => (
            <tr
              key={rule.id}
              draggable
              onDragStart={(e) => handleDragStart(e, rule.id)}
              onDrop={(e) => handleDrop(e, rule.position)}
              onDragOver={handleDragOver}
              className="border-b hover:bg-muted/30 cursor-grab active:cursor-grabbing"
            >
              <td className="py-2 px-2 text-muted-foreground tabular-nums">{rule.position}</td>
              <td className="py-2 px-2">
                <code className="text-xs bg-muted rounded px-1 py-0.5">
                  {JSON.stringify(rule.condition)}
                </code>
              </td>
              <td className="py-2 px-2">
                <code className="text-xs bg-muted rounded px-1 py-0.5">
                  {JSON.stringify(rule.action)}
                </code>
              </td>
              <td className="py-2 px-2 text-right tabular-nums text-xs">
                {rule.saved_7d != null ? fmtUsdPrecise(rule.saved_7d) : "—"}
              </td>
              <td className="py-2 px-2 text-right">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-destructive hover:text-destructive"
                  onClick={() => onDelete(rule.id)}
                >
                  Remove
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SpendRulesSection() {
  const queryClient = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ["spend-rules"],
    queryFn: fetchRules,
    refetchInterval: 60_000,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteRule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["spend-rules"] })
      toast.success("Rule deleted")
    },
    onError: () => toast.error("Failed to delete rule"),
  })

  const reorderMutation = useMutation({
    mutationFn: ({ id, position }: { id: string; position: number }) => reorderRule(id, position),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["spend-rules"] }),
    onError: () => toast.error("Failed to reorder rule"),
  })

  const rules = data?.data ?? []

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-sm">Routing Rules</CardTitle>
            <CardDescription className="text-xs mt-0.5">
              Evaluated top-to-bottom; first match wins. Drag rows to reorder.
            </CardDescription>
          </div>
          <Badge variant="outline" className="text-xs">
            {rules.length} {rules.length === 1 ? "rule" : "rules"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2].map((i) => <Skeleton key={i} className="h-8 w-full" />)}
          </div>
        ) : (
          <RulesTable
            rules={rules}
            onDelete={(id) => deleteMutation.mutate(id)}
            onReorder={(id, position) => reorderMutation.mutate({ id, position })}
          />
        )}
      </CardContent>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Ceiling edit (inline)
// ---------------------------------------------------------------------------

function CeilingEdit({ currentCeiling }: { currentCeiling: number | null }) {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(String(currentCeiling ?? ""))

  const mutation = useMutation({
    mutationFn: (usd: number) => updateCeiling(usd),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["spend-forecast"] })
      setEditing(false)
      toast.success("Monthly ceiling updated")
    },
    onError: () => toast.error("Failed to update ceiling"),
  })

  if (!editing) {
    return (
      <Button variant="outline" size="sm" className="text-xs h-7" onClick={() => setEditing(true)}>
        {currentCeiling != null ? `Edit ceiling (${fmtUsd(currentCeiling)})` : "Set ceiling"}
      </Button>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">$</span>
      <input
        type="number"
        className="w-24 text-xs border rounded px-2 py-1 bg-background"
        value={value}
        min="0.01"
        step="0.01"
        onChange={(e) => setValue(e.target.value)}
        autoFocus
      />
      <Button
        size="sm"
        className="text-xs h-7"
        disabled={mutation.isPending}
        onClick={() => {
          const parsed = parseFloat(value)
          if (isNaN(parsed) || parsed <= 0) {
            toast.error("Enter a positive amount")
            return
          }
          mutation.mutate(parsed)
        }}
      >
        Save
      </Button>
      <Button variant="ghost" size="sm" className="text-xs h-7" onClick={() => setEditing(false)}>
        Cancel
      </Button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SettingsSpendPage() {
  const queryClient = useQueryClient()
  const { data: forecastData, isLoading: forecastLoading } = useQuery({
    queryKey: ["spend-forecast"],
    queryFn: fetchForecast,
    refetchInterval: 120_000,
  })

  const forecast = forecastData?.data

  // §5.3 — Connect to the spend stream and update KPIs incrementally.
  // streamedCostUsd is a monotonic cumulative counter of live "call" events
  // received since mount.  Snapshot events are excluded so this value does NOT
  // overlap with the server-fetched MTD baseline in the polled forecast.
  const { streamedCostUsd } = useSpendStream()

  // Compose a live forecast by adding the monotonic stream total directly on top
  // of the polled MTD baseline.  No subtraction needed because streamedCostUsd
  // only counts real-time events that arrived after the snapshot.
  const liveForecast = useMemo(() => {
    if (!forecast) return forecast
    if (streamedCostUsd === 0) return forecast
    const liveMtd = forecast.mtd_usd + streamedCostUsd
    const daysIn = forecast.days_in_month
    const daysElapsed = Math.max(forecast.days_elapsed, 1)
    const liveProjected = (liveMtd / daysElapsed) * daysIn
    return {
      ...forecast,
      mtd_usd: liveMtd,
      projected_eom_usd: liveProjected,
    }
  }, [forecast, streamedCostUsd])

  // When new spend events arrive, invalidate the breakdown query on the next
  // natural polling cycle.  Throttled to at most once per 30 s to avoid
  // excessive invalidations when events are frequent.
  const lastInvalidationRef = useRef<number>(0)
  const invalidateBreakdown = useCallback(() => {
    const now = Date.now()
    if (now - lastInvalidationRef.current > 30_000) {
      lastInvalidationRef.current = now
      queryClient.invalidateQueries({ queryKey: ["spend-breakdown"] })
    }
  }, [queryClient])

  useEffect(() => {
    if (streamedCostUsd > 0) {
      invalidateBreakdown()
    }
  }, [streamedCostUsd, invalidateBreakdown])

  return (
    <Page archetype="overview" title="Spend">
      <div className="space-y-6">
        {/* KPI strip */}
        {forecastLoading && !liveForecast ? (
          <Card>
            <CardContent className="p-0">
              <div className="grid grid-cols-4 divide-x">
                {[1, 2, 3, 4].map((i) => (
                  <div key={i} className="p-4">
                    <Skeleton className="h-4 w-20 mb-2" />
                    <Skeleton className="h-8 w-16" />
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ) : liveForecast ? (
          <KpiStrip forecast={liveForecast} />
        ) : null}

        {/* Forecast SVG chart */}
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-sm">Forecast</CardTitle>
                <CardDescription className="text-xs mt-0.5">
                  Solid = actual MTD spend. Dashed = linear projection to end of month.
                  {liveForecast?.ceiling_usd != null
                    ? " Red hairline = monthly ceiling."
                    : ""}
                </CardDescription>
              </div>
              {liveForecast && (
                <CeilingEdit currentCeiling={liveForecast.ceiling_usd} />
              )}
            </div>
          </CardHeader>
          <CardContent>
            {forecastLoading && !liveForecast ? (
              <Skeleton className="h-48 w-full" />
            ) : liveForecast ? (
              <ForecastChart days={liveForecast.days} ceiling_usd={liveForecast.ceiling_usd} />
            ) : (
              <p className="text-xs text-muted-foreground">No forecast data available.</p>
            )}
          </CardContent>
          {/* TODO: Add anomaly detection (deferred §D13 — threshold TBD) */}
        </Card>

        {/* Breakdown */}
        <BreakdownSection />

        {/* Routing rules */}
        <SpendRulesSection />
      </div>
    </Page>
  )
}
