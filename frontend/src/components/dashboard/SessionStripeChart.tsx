// ---------------------------------------------------------------------------
// SessionStripeChart — stacked bar chart of session counts over time (bu-2okpr.2)
//
// Renders a recharts BarChart with one stacked Bar per butler.
// X = time bucket (hourly for <= 48h windows, daily otherwise)
// Y = session count
// Color: deterministic mapping butler-name -> --category-1..8 CSS tokens
// ---------------------------------------------------------------------------

import { useMemo } from "react"
import {
  Bar,
  BarChart,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { ChartSkeleton } from "@/components/skeletons"
import type { ButlerSummary } from "@/api/types"
import { useAutoRefresh } from "@/hooks/use-auto-refresh"
import {
  bucketUnit,
  currentWindow,
  formatBucketKey,
  pivotSessionsIntoRows,
  useSessionStripeData,
} from "./session-stripe-utils"

// ---------------------------------------------------------------------------
// Color tokens — maps butler index (0-based, mod 8) to CSS variable names
// ---------------------------------------------------------------------------

const CATEGORY_VARS = [
  "var(--category-1)",
  "var(--category-2)",
  "var(--category-3)",
  "var(--category-4)",
  "var(--category-5)",
  "var(--category-6)",
  "var(--category-7)",
  "var(--category-8)",
] as const

/** Deterministic butler-name to color token mapping.
 * Known butlers use their index in the roster; unlisted butlers use a
 * name hash so multiple unlisted butlers get distinct colors.
 */
function butlerColor(name: string, allButlers: readonly ButlerSummary[]): string {
  const idx = allButlers.findIndex((b) => b.name === name)
  if (idx !== -1) return CATEGORY_VARS[idx % CATEGORY_VARS.length]
  // Fallback: deterministic hash for unlisted butlers
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash)
  }
  return CATEGORY_VARS[Math.abs(hash) % CATEGORY_VARS.length]
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

interface TooltipPayloadEntry {
  dataKey: string
  value: number
  color: string
}

interface CustomTooltipProps {
  active?: boolean
  label?: string
  payload?: TooltipPayloadEntry[]
  unit: "hour" | "day"
}

function StripeTooltip({ active, label, payload, unit }: CustomTooltipProps) {
  if (!active || !payload || payload.length === 0 || !label) return null

  const entries = payload.filter((p) => p.value > 0).sort((a, b) => b.value - a.value)

  if (entries.length === 0) return null

  const total = entries.reduce((sum, p) => sum + p.value, 0)

  return (
    <div className="rounded-md border bg-popover p-3 text-sm shadow-md">
      <p className="mb-2 font-medium">{formatBucketKey(label, unit)}</p>
      {entries.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2">
          <span
            className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
            style={{ backgroundColor: p.color }}
          />
          <span className="text-muted-foreground">{p.dataKey}:</span>
          <span className="ml-auto font-mono">{p.value}</span>
        </div>
      ))}
      {entries.length > 1 && (
        <div className="mt-2 border-t pt-2 flex justify-between text-muted-foreground">
          <span>Total</span>
          <span className="font-mono">{total}</span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SessionStripeChartProps {
  /** Rolling window length in hours. Defaults to 24. */
  windowHours?: number
  butlers: ButlerSummary[]
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SessionStripeChart({ windowHours = 24, butlers }: SessionStripeChartProps) {
  const { refetchInterval } = useAutoRefresh(60_000)
  const { data, isLoading, isError } = useSessionStripeData(windowHours, refetchInterval)

  const sessions = data?.data ?? []

  // Memoize the pivot and name-ordering so they don't rerun on every render.
  // currentWindow() recomputes on every render so the pivot always aligns with
  // the rolling window the hook uses on its next refetch.
  const { unit, rows, orderedNames } = useMemo(() => {
    const w = currentWindow(windowHours)
    const u = bucketUnit(w.from, w.to)
    const r = pivotSessionsIntoRows(sessions, w.from, w.to, u)

    const present = new Set<string>()
    for (const s of sessions) {
      if (s.butler) present.add(s.butler)
    }
    const knownSet = new Set(butlers.map((b) => b.name))
    const ordered = [
      ...butlers.map((b) => b.name).filter((n) => present.has(n)),
      ...Array.from(present).filter((n) => !knownSet.has(n)).sort(),
    ]
    return { unit: u, rows: r, orderedNames: ordered }
  }, [sessions, windowHours, butlers])

  if (isLoading) {
    return <ChartSkeleton height="h-[200px]" testId="session-stripe-skeleton" />
  }

  if (isError) {
    return (
      <div
        className="flex h-[200px] items-center justify-center text-sm text-muted-foreground"
        data-testid="session-stripe-error"
      >
        Failed to load session data.
      </div>
    )
  }

  if (sessions.length === 0) {
    return (
      <div
        className="flex h-[200px] items-center justify-center text-sm text-muted-foreground"
        data-testid="session-stripe-empty"
      >
        No sessions in the past 24 hours
      </div>
    )
  }

  return (
    <div data-testid="session-stripe-chart">
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
          <XAxis
            dataKey="bucket"
            tickFormatter={(v: string) => formatBucketKey(v, unit)}
            tick={{ fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            allowDecimals={false}
            tick={{ fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            width={32}
          />
          <Tooltip content={<StripeTooltip unit={unit} />} />
          <Legend iconSize={10} wrapperStyle={{ fontSize: 11 }} />
          {orderedNames.map((name) => (
            <Bar
              key={name}
              dataKey={name}
              stackId="bucket"
              fill={butlerColor(name, butlers)}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
