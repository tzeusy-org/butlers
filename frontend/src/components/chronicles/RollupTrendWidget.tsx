// ---------------------------------------------------------------------------
// RollupTrendWidget — small daily-rollup trend chart (bu-333dq, telemetry-
// distillation bead 5, design doc §6.5).
//
// A trailing-N-day stacked bar of lane seconds (reusing the same
// GET /api/chronicler/rollups the API bead ships), with flag glyphs below
// each column. Deliberately quiet per design-language.md: no fabricated
// alarm, no color outside the existing lane/severity vocabulary.
//
// Three "no number here" states, rendered distinctly (never a false
// all-clear zero — see ChroniclerRollupsResponse/-Day/-LaneRow docstrings):
//   - a day whose status isn't "materialized" renders an empty column plus
//     a muted "···" mark instead of flag glyphs;
//   - a lane flagged `unavailable` (feeder_dark) renders with the same
//     neutral hatch pattern used for "data unavailable",
//     not a solid lane color;
//   - a genuine query failure (`rollups_source_error`) renders the shared
//     SourceDegradedNote instead of the chart.
// ---------------------------------------------------------------------------

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { useChroniclesRollups } from "@/hooks/use-chronicles"
import { addIsoDays } from "@/pages/chronicles-date-nav"
import { neutralDensityColor } from "@/lib/chart-colors"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { SourceDegradedNote } from "@/components/ui/query-boundary"
import { LANE_TAXONOMY, type Category } from "./lane-taxonomy"
import { pivotRollupDays, type RollupTrendDayRow } from "./rollup-trend-utils"

const DEFAULT_TRAILING_DAYS = 14

const UNAVAILABLE_PATTERN_ID = "rollup-trend-unavailable-hatch"

const SORTED_CATEGORIES = (Object.keys(LANE_TAXONOMY) as Category[])
  .filter((c) => c !== "other")
  .sort((a, b) => LANE_TAXONOMY[a].sortOrder - LANE_TAXONOMY[b].sortOrder)

// ---------------------------------------------------------------------------
// Format helpers (mirrors AggregateStackedBar's local-noon day parsing)
// ---------------------------------------------------------------------------

function formatDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number)
  const d = new Date(year, month - 1, date, 12, 0, 0)
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" })
}

function formatSeconds(totalSeconds: number): string {
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  if (h === 0) return `${m}m`
  if (m === 0) return `${h}h`
  return `${h}h ${m}m`
}

/**
 * Mirrors AttentionList's severityGlyph vocabulary (a "~" amber mark for
 * warning, a muted "·" otherwise) so a flag day reads consistently with the
 * rest of the dashboard's attention-item vocabulary.
 */
function flagGlyph(severity: string): { char: string; color: string } {
  if (severity.toLowerCase() === "warning") {
    return { char: "~", color: "var(--severity-medium)" }
  }
  return { char: "·", color: "var(--muted-foreground)" }
}

// ---------------------------------------------------------------------------
// Tooltip
// ---------------------------------------------------------------------------

interface TooltipPayloadEntry {
  dataKey: string
  value: number
  payload: RollupTrendDayRow
}

function RollupTrendTooltip({
  active,
  label,
  payload,
}: {
  active?: boolean
  label?: string
  payload?: TooltipPayloadEntry[]
}) {
  if (!active || !payload || payload.length === 0 || !label) return null
  const row = payload[0]?.payload
  if (!row) return null

  if (row.status !== "materialized") {
    return (
      <div className="rounded-md border bg-popover p-3 text-sm shadow-md">
        <p className="font-medium">{formatDay(label)}</p>
        <p className="text-muted-foreground">Not yet available.</p>
      </div>
    )
  }

  const entries = payload.filter((p) =>
    (SORTED_CATEGORIES as string[]).includes(p.dataKey),
  )

  return (
    <div className="rounded-md border bg-popover p-3 text-sm shadow-md">
      <p className="mb-2 font-medium">{formatDay(label)}</p>
      {entries.map((p) => {
        const cat = p.dataKey as Category
        const isUnavailable = row.unavailableLanes.has(cat)
        return (
          <div key={cat} className="flex items-center gap-2">
            <span
              className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: LANE_TAXONOMY[cat].hex }}
            />
            <span className="text-muted-foreground">{LANE_TAXONOMY[cat].label}:</span>
            <span className="ml-auto font-mono">
              {isUnavailable ? "data unavailable" : formatSeconds(p.value)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Flag glyph row
// ---------------------------------------------------------------------------

function flagRowTitle(row: RollupTrendDayRow): string {
  if (row.status !== "materialized") return "Not yet available"
  if (row.flags.length === 0) return "No flags"
  return row.flags.map((f) => f.flag_type).join(", ")
}

function FlagGlyphRow({ rows }: { rows: RollupTrendDayRow[] }) {
  return (
    <div className="flex gap-0.5" data-testid="rollup-trend-flags">
      {rows.map((row) => (
        <div
          key={row.day}
          className="flex flex-1 items-center justify-center gap-0.5"
          title={flagRowTitle(row)}
        >
          {row.status !== "materialized" ? (
            <span
              className="text-[10px]"
              style={{ color: "var(--muted-foreground)" }}
              aria-label="Not yet available"
            >
              ···
            </span>
          ) : (
            row.flags.map((f) => {
              const glyph = flagGlyph(f.severity)
              return (
                <span
                  key={f.flag_type}
                  style={{ color: glyph.color, fontFamily: "var(--font-mono)", fontSize: "11px" }}
                  aria-label={f.flag_type}
                >
                  {glyph.char}
                </span>
              )
            })
          )}
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading / empty / error states
// ---------------------------------------------------------------------------

function TrendSkeleton() {
  return (
    <div
      className="flex h-40 flex-col gap-2 py-2"
      data-testid="rollup-trend-skeleton"
      role="status"
      aria-label="Loading trend"
    >
      <Skeleton className="h-full w-full rounded-md" />
    </div>
  )
}

function TrendErrorFallback({ onRetry }: { onRetry?: () => void }) {
  return (
    <div
      className="flex h-32 flex-col items-center justify-center gap-3 text-sm text-muted-foreground"
      data-testid="rollup-trend-error"
    >
      <p>Failed to load the trend.</p>
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface RollupTrendWidgetProps {
  /** Trailing window ends on this local day (YYYY-MM-DD), inclusive. */
  endDate: string
  /** Number of trailing days to show, including endDate. Default 14. */
  days?: number
}

export function RollupTrendWidget({ endDate, days = DEFAULT_TRAILING_DAYS }: RollupTrendWidgetProps) {
  const startDate = addIsoDays(endDate, -(days - 1))
  const { data, isLoading, isError, refetch } = useChroniclesRollups({
    start_date: startDate,
    end_date: endDate,
  })

  if (isLoading) {
    return <TrendSkeleton />
  }

  if (isError) {
    return <TrendErrorFallback onRetry={() => void refetch()} />
  }

  const response = data?.data
  if (!response) {
    return null
  }

  // Genuine query failure on an otherwise-200 response: never render the
  // chart as if it were a truthful (possibly all-zero) trend.
  if (response.rollups_source_error) {
    return (
      <SourceDegradedNote
        label="Daily trend"
        detail="data source unreachable"
        onRetry={() => void refetch()}
      />
    )
  }

  const rows = pivotRollupDays(response.days)

  return (
    <div className="space-y-1" data-testid="rollup-trend-widget">
      <div className="h-44">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
            {/* Hatch pattern for feeder_dark-affected lanes — a neutral
                density treatment for "data unavailable", not a lane hue or
                severity color (never implies a real reading). */}
            <defs>
              <pattern
                id={UNAVAILABLE_PATTERN_ID}
                patternUnits="userSpaceOnUse"
                width={8}
                height={8}
                patternTransform="rotate(45)"
              >
                <rect width={8} height={8} fill={neutralDensityColor(0.15)} fillOpacity={0.5} />
                <line
                  x1={0}
                  y1={0}
                  x2={0}
                  y2={8}
                  stroke={neutralDensityColor(0.75)}
                  strokeWidth={3}
                  strokeOpacity={0.7}
                />
              </pattern>
            </defs>
            <XAxis
              dataKey="day"
              tickFormatter={formatDay}
              tick={{ fontSize: 10 }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              tickFormatter={(v: number) => formatSeconds(v)}
              tick={{ fontSize: 10 }}
              tickLine={false}
              axisLine={false}
              width={48}
            />
            <Tooltip content={<RollupTrendTooltip />} />
            {SORTED_CATEGORIES.map((cat) => (
              <Bar key={cat} dataKey={cat} stackId="day" isAnimationActive={false}>
                {rows.map((row, i) => (
                  <Cell
                    key={i}
                    fill={
                      row.unavailableLanes.has(cat)
                        ? `url(#${UNAVAILABLE_PATTERN_ID})`
                        : LANE_TAXONOMY[cat].hex
                    }
                  />
                ))}
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <FlagGlyphRow rows={rows} />
    </div>
  )
}
