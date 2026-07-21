// ---------------------------------------------------------------------------
// SpendPage — /spend  [bu-86c4c.11, JARVIS audit move 8]
//
// The single nav-visible Spend surface. Merges what used to be two
// disconnected pages with different vocabularies and zero cross-links:
//   - /costs           ("Costs & Usage" — legacy Card idiom, orphaned from
//                        both sidebar and command palette)
//   - /settings/spend  ("Spend" — Dispatch language, MTD/forecast/ceiling/
//                        routing rules, live per-call WS stream)
// Both routes now redirect here (see router-config.tsx).
//
// Answers three questions in order, Dispatch language throughout (no card
// chrome, hairline borders, state color only when state demands):
//   1. Am I on budget?  — posture strip: MTD vs ceiling meter, projected
//      EOM, live today-burn from the spend WS stream (ported from
//      /settings/spend's KpiStrip/ForecastChart/CeilingEdit, unchanged).
//   2. What changed?    — a ranked "movers" strip (butler spend deltas vs
//      the prior window of equal length) and an HONEST per-butler-per-day
//      stacked chart. Honest because GET /api/spend/daily now preserves
//      real per-butler identity per day (bu-86c4c.11 backend fix — see
//      spend.py:get_daily_costs) instead of the period-aggregate smear
//      CostStripeChart used to fabricate (bu-86c4c.1 truth amnesty).
//   3. Why?              — on-page evidence layer: Top Sessions and
//      by-schedule projected-monthly costs, every butler row and session
//      drilling through to /butlers/:name?tab=spend or /sessions/:id.
//
// Controls (ceiling, routing rules) stay on the same surface as the signals
// they govern — noticing a spike and capping the schedule that caused it is
// one continuous motion.
//
// The evidence layer (Top Sessions + by-schedule projected costs) is scoped
// to the same TimeWindowPicker window as the daily chart above it (bu-oaiiw
// backend support: GET /api/spend/top-sessions and GET /api/spend/by-schedule
// now accept from/to, mirroring /api/spend/daily).
// ---------------------------------------------------------------------------

import { useState, useMemo, useRef } from "react"
import { Link, useSearchParams } from "react-router"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { differenceInCalendarDays, subDays } from "date-fns"

import { Page } from "@/components/ui/page"
import { Button } from "@/components/ui/button"
import { SpendUnavailableFootnote } from "@/components/spend/SpendUnavailableFootnote"
import { SourceDegradedNote } from "@/components/ui/query-boundary"
import { Skeleton } from "@/components/ui/skeleton"
import { Eyebrow } from "@/components/ui/Eyebrow"
import { toast } from "sonner"
import { apiFetch } from "@/api/client"
import { useSpendTicker, type LiveUnpricedSpendEvent } from "@/hooks/use-spend-ticker"
import { useFleetHaltStatus } from "@/hooks/use-fleet-halt"
import { useModelCatalog } from "@/hooks/use-model-catalog"
import { useBusAwarePollInterval } from "@/hooks/use-bus-aware-poll-interval"
import {
  useSpendSummary,
  useDailySpend,
  useTopSessions,
  useCostsBySchedule,
} from "@/hooks/use-spend"
import { useTimeWindow, formatWindowDate, OWNER_TZ_DEFAULT } from "@/hooks/use-time-window"
import { TimeWindowPicker } from "@/components/workspace/TimeWindowPicker"
import { CostStripeChart } from "@/components/costs/CostStripeChart"
import { SpendVerdictOpener } from "@/components/costs/SpendVerdictOpener"
import { formatCostUsd } from "@/lib/format-cost"
import { cn } from "@/lib/utils"
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry"
import { computeMovers, type Mover } from "@/lib/spend-movers"
import type { ForecastData, ForecastDay } from "@/lib/spend-forecast"
import type { ComplexityTier, SpendDivergence, UnpricedModelUsage } from "@/api/types"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
// ForecastData/ForecastDay live in lib/spend-forecast.ts so SpendVerdictOpener
// can share the same shape without a page-to-component import cycle.

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
  // Set only on the "purpose" dimension (bu-og0j2): true when the ledger-backed
  // query failed or the dashboard DB pool is unavailable -- there is no per-butler
  // MCP fallback for this dimension, so an empty breakdown must never be read as
  // "genuinely no purpose-tagged spend this month" (see SourceDegradedNote below).
  source_error?: boolean
  // Set on the butler/model/feature dimensions (bu-jad4j.3): butlers whose cost
  // source failed and were dropped from the per-butler fan-out. When non-empty the
  // breakdown undercounts, so an empty result must not read as a genuine "$0 month"
  // and a populated result must footnote the missing butlers.
  unavailable_butlers?: string[]
  /** Executed models excluded from the priced subtotal because no price exists. */
  unpriced_models?: UnpricedModelUsage[]
  /** Declared marginal-cost class for model rows, including subscription zeroes. */
  billing_classes?: Record<string, "metered" | "subscription" | "local">
  divergences?: SpendDivergence[]
  divergence_source_error?: boolean
  historical_attribution_note?: string | null
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

function fetchForecast(): Promise<{ data: ForecastData }> {
  return apiFetch<{ data: ForecastData }>("/spend/forecast")
}

function fetchBreakdown(
  by: "butler" | "model" | "feature" | "purpose",
): Promise<{ data: BreakdownData }> {
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

// Create a routing rule. The shape mirrors the dispatch-time evaluator in
// src/butlers/core/model_routing.py:apply_spend_routing_rules and the enforced
// pydantic schema in butlers.api.routers.spend (SpendRuleCondition / SpendRuleAction,
// extra keys rejected with 422). Condition keys are `butler`, `complexity` (alias
// `tier`), and/or `trigger` (the dispatch trigger_source), ANDed together; an empty
// condition is a catch-all. Action effects: `model` (a priced model_id the matched
// dispatch routes TO) and/or `max_cost_per_call` (a hard per-call USD cap the spawner
// enforces). At least one effect is required. Omitting `position` appends to the end.
function createRule(body: {
  condition: Record<string, unknown>
  action: Record<string, unknown>
}): Promise<{ data: SpendRule }> {
  return apiFetch<{ data: SpendRule }>("/spend/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}

// Canonical complexity tiers (model_routing.Complexity), highest → lowest.
const COMPLEXITY_TIERS: ComplexityTier[] = [
  "reasoning",
  "workhorse",
  "cheap",
  "specialty",
  "local",
  "legacy",
]

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function fmtUsdPrecise(n: number): string {
  return `$${n.toFixed(4)}`
}

function unpricedCallCount(models: readonly UnpricedModelUsage[] | undefined): number {
  return (models ?? []).reduce((total, model) => total + model.calls, 0)
}

function unpricedModelNames(models: readonly UnpricedModelUsage[] | undefined): string {
  return (models ?? []).map((model) => model.model).join(", ")
}

function mergeUnpricedModels(
  ledgerModels: readonly UnpricedModelUsage[] | undefined,
  liveEvents: readonly LiveUnpricedSpendEvent[],
): UnpricedModelUsage[] {
  const merged = new Map<string, UnpricedModelUsage>()
  const addUsage = (usage: UnpricedModelUsage) => {
    const previous = merged.get(usage.model)
    merged.set(usage.model, {
      model: usage.model,
      calls: (previous?.calls ?? 0) + usage.calls,
      input_tokens: (previous?.input_tokens ?? 0) + usage.input_tokens,
      output_tokens: (previous?.output_tokens ?? 0) + usage.output_tokens,
      cached_input_tokens: (previous?.cached_input_tokens ?? 0) + usage.cached_input_tokens,
      cache_creation_tokens: (previous?.cache_creation_tokens ?? 0) + usage.cache_creation_tokens,
    })
  }

  for (const usage of ledgerModels ?? []) addUsage(usage)
  for (const event of liveEvents) addUsage({ ...event, calls: 1 })
  return [...merged.values()]
}

// ---------------------------------------------------------------------------
// Posture — KPI Strip. Hairline-divided, no card chrome. Mega numerals are
// weight 500, tabular. State colour appears only when state demands
// (over-ceiling).
// ---------------------------------------------------------------------------

interface KpiCellProps {
  label: string
  value: string
  sub?: string
  tone?: "fg" | "red"
  testId?: string
}

function KpiCell({ label, value, sub, tone = "fg", testId }: KpiCellProps) {
  return (
    <div
      className="flex flex-col gap-1.5 px-4 py-3 border-r border-b border-border/60 last:border-r-0 sm:[&:nth-child(2)]:border-r-0 lg:[&:nth-child(2)]:border-r lg:[&:nth-child(4)]:border-r-0"
      data-testid={testId}
    >
      <Eyebrow>{label}</Eyebrow>
      <span
        className={cn(
          "text-[28px] font-medium tracking-tight tabular-nums leading-none",
          tone === "red" ? "text-[var(--red-text)]" : "text-foreground",
        )}
      >
        {value}
      </span>
      {sub && (
        <span className="font-mono text-xs tabular-nums text-muted-foreground leading-tight">
          {sub}
        </span>
      )}
    </div>
  )
}

function KpiStrip({ forecast }: { forecast: ForecastData }) {
  const daysRemaining = forecast.days_in_month - forecast.days_elapsed
  const unpricedCalls = unpricedCallCount(forecast.unpriced_models)
  const blindModels = forecast.ceiling_blind_to_unpriced_models ?? 0
  const pct =
    forecast.ceiling_usd != null && forecast.ceiling_usd > 0
      ? Math.min(100, Math.round((forecast.mtd_usd / forecast.ceiling_usd) * 100))
      : null
  const overCeiling =
    forecast.ceiling_usd != null && forecast.projected_eom_usd > forecast.ceiling_usd

  return (
    <div
      className="grid grid-cols-2 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="kpi-strip"
    >
      <KpiCell
        testId="kpi-mtd"
        label="MTD Spend"
        value={formatCostUsd(forecast.mtd_usd)}
        sub={
          unpricedCalls > 0
            ? `${forecast.days_elapsed} day${forecast.days_elapsed === 1 ? "" : "s"} elapsed · excludes ${unpricedCalls.toLocaleString()} unpriced calls`
            : `${forecast.days_elapsed} day${forecast.days_elapsed === 1 ? "" : "s"} elapsed`
        }
      />
      <KpiCell
        testId="kpi-projected-eom"
        label="Projected EOM"
        value={formatCostUsd(forecast.projected_eom_usd)}
        tone={overCeiling ? "red" : "fg"}
        sub={`${daysRemaining} day${daysRemaining === 1 ? "" : "s"} remaining`}
      />
      <KpiCell
        testId="kpi-ceiling"
        label="Monthly Ceiling"
        value={forecast.ceiling_usd != null ? formatCostUsd(forecast.ceiling_usd) : "—"}
        sub={
          blindModels > 0
            ? `blind to ${blindModels} unpriced model${blindModels === 1 ? "" : "s"}`
            : pct != null
              ? `${pct}% used`
              : undefined
        }
      />
      <KpiCell
        testId="kpi-days-in-month"
        label="Days in Month"
        value={String(forecast.days_in_month)}
        sub={`${forecast.days_elapsed} elapsed / ${daysRemaining} left`}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Posture — hand-rolled SVG forecast chart [§5.5 — no chart library].
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
  const yTicks = Array.from({ length: tickCount + 1 }, (_, i) => (maxCost * i) / tickCount)

  // X-axis labels: first day of month + today + last day
  const xLabels: Array<{ i: number; label: string }> = []
  if (days.length > 0) xLabels.push({ i: 0, label: days[0].date.slice(8) })
  if (firstProjIdx > 0) xLabels.push({ i: firstProjIdx - 1, label: "today" })
  if (days.length > 1)
    xLabels.push({ i: days.length - 1, label: days[days.length - 1].date.slice(8) })

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
              {formatCostUsd(v)}
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
          stroke="var(--red)"
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
          stroke="var(--primary)"
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
          stroke="var(--primary)"
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
// Posture — ceiling edit (inline)
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
        {currentCeiling != null ? `Edit ceiling (${formatCostUsd(currentCeiling)})` : "Set ceiling"}
      </Button>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted-foreground">$</span>
      <input
        type="number"
        className="w-24 text-xs border rounded px-2 py-1 bg-background tabular-nums"
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
// What changed — Movers strip: ranked butler spend deltas vs the prior
// window of equal length. computeMovers/Mover now live in lib/spend-movers.ts
// so the SpendVerdictOpener page opener (bu-qvnce.9) shares the exact same
// honest-delta logic instead of duplicating it.
// ---------------------------------------------------------------------------

function MoverChip({ mover }: { mover: Mover }) {
  const up = mover.delta > 0
  return (
    <Link
      to={`/butlers/${mover.name}?tab=spend`}
      className="flex items-center gap-2 border border-border/60 px-3 py-2 hover:bg-muted/40"
      data-testid="mover-chip"
    >
      <span
        className={cn(
          "shrink-0 h-2 w-2 rounded-full",
          up ? "bg-[var(--red)]" : "bg-[var(--green,var(--primary))]",
        )}
        aria-hidden
      />
      <span className="flex flex-col gap-0.5">
        <span className="font-mono text-xs">{mover.name}</span>
        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
          {up ? "+" : "−"}
          {formatCostUsd(Math.abs(mover.delta))}
          {mover.prior === 0 ? " · new" : mover.current === 0 ? " · stopped" : ""}
        </span>
      </span>
    </Link>
  )
}

function MoversStrip({
  current,
  prior,
  windowDays,
  isLoading,
  isError,
  unavailableButlers,
}: {
  current: Record<string, number>
  prior: Record<string, number>
  windowDays: number
  isLoading: boolean
  isError: boolean
  unavailableButlers: ReadonlySet<string>
}) {
  const movers = useMemo(
    () => computeMovers(current, prior, unavailableButlers),
    [current, prior, unavailableButlers],
  )

  return (
    <section className="border border-border" data-testid="movers-strip">
      <div className="flex flex-col gap-1 px-4 py-3 border-b border-border">
        <Eyebrow>Movers</Eyebrow>
        <p className="text-xs text-muted-foreground">
          Ranked butler spend deltas vs the prior {windowDays}-day window.
        </p>
      </div>
      <div className="p-4 flex flex-col gap-3">
        {isLoading ? (
          <div className="flex gap-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-12 w-32" />
            ))}
          </div>
        ) : isError ? (
          <SourceDegradedNote label="Movers" detail="spend comparison unavailable" />
        ) : movers.length === 0 ? (
          <p className="font-serif italic text-muted-foreground text-sm">
            No spend change vs the prior window.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {movers.map((m) => (
              <MoverChip key={m.name} mover={m} />
            ))}
          </div>
        )}
        {!isLoading && !isError && unavailableButlers.size > 0 && (
          <SourceDegradedNote
            label="Movers"
            detail={`excluded from comparison, cost source unavailable: ${Array.from(unavailableButlers).join(", ")}`}
          />
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// What changed — breakdown bars (CSS only, no chart lib)
// ---------------------------------------------------------------------------

interface BreakdownBarProps {
  label: string
  value: number | null
  maxValue: number
  href?: string
  billingClass?: "metered" | "subscription" | "local"
}

function BreakdownBar({ label, value, maxValue, href, billingClass }: BreakdownBarProps) {
  const isUnpriced = value === null
  const pct = !isUnpriced && maxValue > 0 ? (value / maxValue) * 100 : 0
  const labelEl = href ? (
    <Link to={href} className="w-40 truncate text-muted-foreground font-mono text-xs hover:underline">
      {label}
    </Link>
  ) : (
    <span className="w-40 truncate text-muted-foreground font-mono text-xs">{label}</span>
  )
  return (
    <div className="flex items-center gap-3 text-sm">
      {labelEl}
      <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full bg-primary rounded-full transition-all"
          style={{ width: `${pct.toFixed(1)}%` }}
        />
      </div>
      <span className="w-36 text-right tabular-nums text-xs">
        {isUnpriced ? <span aria-label="unpriced">{"—"}/unpriced</span> : fmtUsdPrecise(value)}
        {billingClass === "subscription" && " · subscription"}
        {billingClass === "local" && " · local"}
      </span>
    </div>
  )
}

type BreakdownBy = "butler" | "model" | "feature" | "purpose"

function BreakdownSection() {
  const [by, setBy] = useState<BreakdownBy>("butler")
  // Live path: spendPatch invalidates ["spend-breakdown"] on every spend
  // call event (bu-01r64.4, see event-cache-registry.ts) -- previously a
  // bespoke, throttled useEffect in the page's root component did this by
  // hand. The poll below is now a bus-aware reconciliation sweep, not the
  // primary update path.
  const refetchInterval = useBusAwarePollInterval()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["spend-breakdown", by],
    queryFn: () => fetchBreakdown(by),
    refetchInterval,
  })

  const entries = useMemo(() => {
    const breakdown = data?.data?.breakdown ?? {}
    const priced = Object.entries(breakdown).map(([label, value]) => ({ label, value }))
    const unpriced =
      by === "model"
        ? (data?.data?.unpriced_models ?? [])
            .filter((model) => !(model.model in breakdown))
            .map((model) => ({ label: model.model, value: null }))
        : []
    return [...priced.sort((a, b) => b.value - a.value), ...unpriced]
  }, [by, data])
  const maxValue = Math.max(0, ...entries.map((entry) => entry.value ?? 0))
  const sourceError = data?.data?.source_error === true
  const divergenceCount = data?.data?.divergences?.length ?? 0
  // butler/model/feature dimensions name any butler dropped from the fan-out in
  // `unavailable_butlers` (purpose uses `source_error` above instead). When
  // non-empty the breakdown undercounts, so an empty result is an outage — not a
  // genuine "$0 month" — and a populated one must footnote the missing butlers
  // (bu-jad4j.3).
  const unavailableButlers = by === "purpose" ? [] : (data?.data?.unavailable_butlers ?? [])

  return (
    <section className="border border-border">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border">
        <Eyebrow>Spend Breakdown · 30d</Eyebrow>
        <div className="flex gap-1">
          {(["butler", "model", "feature", "purpose"] as BreakdownBy[]).map((dim) => (
            <Button
              key={dim}
              variant={by === dim ? "default" : "ghost"}
              size="sm"
              className="h-6 px-2 font-mono text-[10px] uppercase tracking-widest"
              onClick={() => setBy(dim)}
            >
              {dim}
            </Button>
          ))}
        </div>
      </div>
      <div className="p-4">
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-4 w-full" />
            ))}
          </div>
        ) : isError && entries.length === 0 ? (
          // A failed breakdown query must not fall through to "No spend has
          // been recorded yet." — an outage would read as a genuine $0 month
          // (bu-mkd5r, three-way state contract). Only when nothing is cached:
          // a background-refetch error keeps the last-good breakdown visible.
          <SourceDegradedNote
            label="Spend breakdown"
            detail="unavailable"
            onRetry={() => void refetch()}
          />
        ) : sourceError ? (
          <SourceDegradedNote
            label={by === "purpose" ? "Purpose breakdown" : "Spend breakdown"}
            detail="ledger source unavailable"
          />
        ) : entries.length === 0 && unavailableButlers.length > 0 ? (
          // Empty because butlers dropped out of the fan-out, not a genuine $0
          // month — name them rather than the calm "nothing recorded" line
          // (bu-jad4j.3).
          <SpendUnavailableFootnote
            label="Spend breakdown"
            butlers={unavailableButlers}
            variant="empty"
            testId="breakdown-unavailable"
          />
        ) : entries.length === 0 ? (
          <p className="font-serif italic text-muted-foreground text-sm">
            No spend has been recorded yet.
          </p>
        ) : (
          <div className="space-y-2">
            {entries.map(({ label, value }) => (
              <BreakdownBar
                key={label}
                label={label}
                value={value}
                maxValue={maxValue}
                href={by === "butler" ? `/butlers/${label}?tab=spend` : undefined}
                billingClass={data?.data?.billing_classes?.[label]}
              />
            ))}
            {(data?.data?.unpriced_models?.length ?? 0) > 0 && (
              <SourceDegradedNote
                label="Spend breakdown"
                detail={`excludes ${unpricedCallCount(data?.data?.unpriced_models).toLocaleString()} unpriced calls (${unpricedModelNames(data?.data?.unpriced_models)})`}
                testId="breakdown-unpriced"
              />
            )}
            {divergenceCount > 0 && (
              <SourceDegradedNote
                label="Spend breakdown"
                detail={`ledger/session token drift in ${divergenceCount} day-butler bucket${divergenceCount === 1 ? "" : "s"}`}
                testId="breakdown-divergence"
              />
            )}
            {data?.data?.divergence_source_error && (
              <SourceDegradedNote
                label="Spend breakdown"
                detail="session-to-ledger comparison unavailable"
                testId="breakdown-divergence-source-error"
              />
            )}
            {data?.data?.historical_attribution_note && (
              <SourceDegradedNote
                label="Historical attribution"
                detail={data.data.historical_attribution_note}
                testId="breakdown-historical-attribution"
              />
            )}
            {unavailableButlers.length > 0 && (
              // Populated but partial: some butlers are absent from the bars.
              <SpendUnavailableFootnote
                label="Spend breakdown"
                butlers={unavailableButlers}
                variant="partial"
                testId="breakdown-unavailable"
              />
            )}
          </div>
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Why — evidence layer: Top Sessions + by-schedule projected costs, both
// scoped to the same [from, to] window as the daily chart's TimeWindowPicker
// (bu-oaiiw).
// ---------------------------------------------------------------------------

function TopSessionsSection({ from, to }: { from: Date; to: Date }) {
  const { data, isLoading, isError } = useTopSessions(10, from, to)
  const sessions = data?.data ?? []
  // Butlers dropped from the top-sessions fan-out (meta.unavailable_butlers).
  // When non-empty the ranking omits their sessions, so an empty table is an
  // outage — not "genuinely no expensive sessions" — and a populated one must
  // footnote the missing butlers (bu-jad4j.3).
  const unavailableButlers = data?.meta?.unavailable_butlers ?? []

  return (
    <section className="border border-border" data-testid="top-sessions-section">
      <div className="flex flex-col gap-1 px-4 py-3 border-b border-border">
        <Eyebrow>Most Expensive Sessions</Eyebrow>
        <p className="text-xs text-muted-foreground">
          Top sessions by cost, {formatWindowDate(from)} – {formatWindowDate(to)}. Click through
          to session detail.
        </p>
      </div>
      <div className="p-4">
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : isError ? (
          <p className="text-sm text-muted-foreground">Failed to load top sessions.</p>
        ) : sessions.length === 0 && unavailableButlers.length > 0 ? (
          // Empty because butlers dropped out of the fan-out, not a genuine
          // absence of expensive sessions — name them (bu-jad4j.3).
          <SpendUnavailableFootnote
            label="Top sessions"
            butlers={unavailableButlers}
            variant="empty"
            testId="top-sessions-unavailable"
          />
        ) : sessions.length === 0 ? (
          <p className="font-serif italic text-muted-foreground text-sm">
            No session data available.
          </p>
        ) : (
          <div className="overflow-x-auto flex flex-col gap-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  <th className="text-left py-2 px-2 font-normal">Butler</th>
                  <th className="text-left py-2 px-2 font-normal">Model</th>
                  <th className="text-right py-2 px-2 font-normal">Tokens</th>
                  <th className="text-right py-2 px-2 font-normal">Cost</th>
                  <th className="text-right py-2 px-2 font-normal">When</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id} className="border-b border-border/60 hover:bg-muted/30">
                    <td className="py-2 px-2">
                      <Link to={`/butlers/${s.butler}?tab=spend`} className="hover:underline">
                        {s.butler}
                      </Link>
                    </td>
                    <td className="py-2 px-2 text-muted-foreground text-xs">{s.model}</td>
                    <td className="py-2 px-2 text-right tabular-nums text-xs">
                      {s.input_tokens.toLocaleString()} / {s.output_tokens.toLocaleString()}
                    </td>
                    <td className="py-2 px-2 text-right tabular-nums font-medium">
                      {formatCostUsd(s.cost_usd)}
                    </td>
                    <td className="py-2 px-2 text-right text-xs text-muted-foreground">
                      <Link to={`/sessions/${s.session_id}`} className="hover:underline">
                        {new Date(s.started_at).toLocaleString("en-US", {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {unavailableButlers.length > 0 && (
              // Populated but partial: some butlers' sessions are absent from
              // the ranking.
              <SpendUnavailableFootnote
                label="Top sessions"
                butlers={unavailableButlers}
                variant="partial"
                testId="top-sessions-unavailable"
              />
            )}
          </div>
        )}
      </div>
    </section>
  )
}

function ByScheduleSection({ from, to }: { from: Date; to: Date }) {
  const { data, isLoading, isError } = useCostsBySchedule(from, to)
  const schedules = data?.data ?? []
  // Butlers dropped from the by-schedule fan-out (meta.unavailable_butlers).
  // When non-empty the ranking omits their schedules, so an empty table is an
  // outage — not "genuinely no scheduled-task cost data" — and a populated one
  // must footnote the missing butlers (bu-h3ej9).
  const unavailableButlers = data?.meta?.unavailable_butlers ?? []

  return (
    <section className="border border-border" data-testid="by-schedule-section">
      <div className="flex flex-col gap-1 px-4 py-3 border-b border-border">
        <Eyebrow>By Schedule</Eyebrow>
        <p className="text-xs text-muted-foreground">
          Projected monthly cost per cron job, runs from {formatWindowDate(from)} –{" "}
          {formatWindowDate(to)}. See which schedule is burning money.
        </p>
      </div>
      <div className="p-4">
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2].map((i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : isError ? (
          <p className="text-sm text-muted-foreground">Failed to load schedule costs.</p>
        ) : schedules.length === 0 && unavailableButlers.length > 0 ? (
          // Empty because butlers dropped out of the fan-out, not a genuine
          // absence of scheduled-task cost data — name them (bu-h3ej9).
          <SpendUnavailableFootnote
            label="Schedule costs"
            butlers={unavailableButlers}
            variant="empty"
            testId="by-schedule-unavailable"
          />
        ) : schedules.length === 0 ? (
          <p className="font-serif italic text-muted-foreground text-sm">
            No scheduled-task cost data available.
          </p>
        ) : (
          <div className="overflow-x-auto flex flex-col gap-3">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                  <th className="text-left py-2 px-2 font-normal">Schedule</th>
                  <th className="text-left py-2 px-2 font-normal">Butler</th>
                  <th className="text-left py-2 px-2 font-normal">Cron</th>
                  <th className="text-right py-2 px-2 font-normal">Runs</th>
                  <th className="text-right py-2 px-2 font-normal">Avg/run</th>
                  <th className="text-right py-2 px-2 font-normal">Projected/mo</th>
                </tr>
              </thead>
              <tbody>
                {schedules.map((s) => (
                  <tr
                    key={`${s.butler}-${s.schedule_name}`}
                    className="border-b border-border/60 hover:bg-muted/30"
                  >
                    <td className="py-2 px-2 font-mono text-xs">{s.schedule_name}</td>
                    <td className="py-2 px-2">
                      <Link to={`/butlers/${s.butler}?tab=spend`} className="hover:underline">
                        {s.butler}
                      </Link>
                    </td>
                    <td className="py-2 px-2 text-muted-foreground text-xs">{s.cron}</td>
                    <td className="py-2 px-2 text-right tabular-nums text-xs">{s.total_runs}</td>
                    <td className="py-2 px-2 text-right tabular-nums text-xs">
                      {formatCostUsd(s.avg_cost_per_run)}
                    </td>
                    <td className="py-2 px-2 text-right tabular-nums font-medium">
                      {formatCostUsd(s.projected_monthly_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {unavailableButlers.length > 0 && (
              // Populated but partial: some butlers' schedules are absent from
              // the ranking (bu-h3ej9).
              <SpendUnavailableFootnote
                label="Schedule costs"
                butlers={unavailableButlers}
                variant="partial"
                testId="by-schedule-unavailable"
              />
            )}
          </div>
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Controls — routing rules table (drag-to-reorder)
// ---------------------------------------------------------------------------

// Render a condition/action JSONB object as labelled chips instead of raw JSON, so
// the table reflects the same structured vocabulary the editor produces.
function fmtConstraintValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((v) => String(v)).join(" | ")
  return String(value)
}

function conditionChips(condition: Record<string, unknown>): { label: string; value: string }[] {
  const order = ["butler", "complexity", "tier", "trigger", "purpose"]
  const keys = Object.keys(condition).sort(
    (a, b) => order.indexOf(a) - order.indexOf(b) || a.localeCompare(b),
  )
  return keys.map((k) => ({ label: k, value: fmtConstraintValue(condition[k]) }))
}

function actionChips(action: Record<string, unknown>): { label: string; value: string }[] {
  const chips: { label: string; value: string }[] = []
  if (action.model != null) chips.push({ label: "model", value: String(action.model) })
  if (action.max_cost_per_call != null)
    chips.push({ label: "cap", value: `$${Number(action.max_cost_per_call)}` })
  // Surface any unrecognized keys verbatim so nothing is silently hidden.
  for (const [k, v] of Object.entries(action)) {
    if (k === "model" || k === "max_cost_per_call") continue
    chips.push({ label: k, value: fmtConstraintValue(v) })
  }
  return chips
}

function RuleChips({
  entries,
  emptyLabel,
}: {
  entries: { label: string; value: string }[]
  emptyLabel: string
}) {
  if (entries.length === 0) {
    return <span className="text-xs italic text-muted-foreground">{emptyLabel}</span>
  }
  return (
    <div className="flex flex-wrap gap-1">
      {entries.map((e) => (
        <span
          key={e.label}
          className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-xs"
        >
          <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
            {e.label}
          </span>
          <span className="font-mono">{e.value}</span>
        </span>
      ))}
    </div>
  )
}

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
      <p className="font-serif italic text-muted-foreground text-sm py-4 text-center">
        No routing rules are configured; rules evaluate top-to-bottom and the first match wins.
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            <th className="text-left py-2 px-2 w-8 font-normal">Pos</th>
            <th className="text-left py-2 px-2 font-normal">Condition</th>
            <th className="text-left py-2 px-2 font-normal">Action</th>
            <th className="text-right py-2 px-2 font-normal">Saved 7d</th>
            <th className="text-right py-2 px-2 w-16 font-normal"></th>
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
              className="border-b border-border/60 hover:bg-muted/30 cursor-grab active:cursor-grabbing"
            >
              <td className="py-2 px-2 text-muted-foreground tabular-nums">{rule.position}</td>
              <td className="py-2 px-2">
                <RuleChips entries={conditionChips(rule.condition)} emptyLabel="any dispatch" />
              </td>
              <td className="py-2 px-2">
                <RuleChips entries={actionChips(rule.action)} emptyLabel="—" />
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

// Common dispatch trigger sources operators may want to gate on. These mirror the
// trigger_source values passed at the spawner call site (src/butlers/core/spawner.py
// and its callers). Free-form values are not offered — the evaluator fails closed on
// trigger sources it cannot match, and these cover the meaningful dispatch classes.
const TRIGGER_SOURCES: string[] = [
  "route",
  "tick",
  "classification",
  "schedule",
  "healing",
  "retry",
  "qa",
  "extraction",
  "external",
]

// Purpose is an alias dimension for the same dispatch trigger_source (bu-og0j2 /
// bu-qvnce.12) -- see model_routing._rule_condition_matches. Offers the same
// vocabulary plus "discretion", the one purpose value stamped by a path
// (connectors.discretion_dispatcher) that has no equivalent trigger_source.
const PURPOSE_VALUES: string[] = [...TRIGGER_SOURCES, "discretion"]

interface CreateRuleFormProps {
  onCancel: () => void
  onCreated: () => void
}

function CreateRuleForm({ onCancel, onCreated }: CreateRuleFormProps) {
  const queryClient = useQueryClient()
  const { data: catalogData } = useModelCatalog()

  const [butler, setButler] = useState("")
  const [complexity, setComplexity] = useState<"" | ComplexityTier>("")
  const [trigger, setTrigger] = useState("")
  const [purpose, setPurpose] = useState("")
  const [model, setModel] = useState("")
  const [maxCostPerCall, setMaxCostPerCall] = useState("")

  const createMutation = useMutation({
    mutationFn: createRule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["spend-rules"] })
      toast.success("Rule created")
      onCreated()
    },
    onError: () => toast.error("Failed to create rule"),
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const targetModel = model.trim()
    const capRaw = maxCostPerCall.trim()

    // Build the action from the supplied effects; at least one is required.
    const action: Record<string, unknown> = {}
    if (targetModel) action.model = targetModel
    if (capRaw) {
      const cap = Number(capRaw)
      if (!Number.isFinite(cap) || cap <= 0) {
        toast.error("Per-call cap must be a positive number")
        return
      }
      action.max_cost_per_call = cap
    }
    if (Object.keys(action).length === 0) {
      toast.error("Set at least one effect: route-to model and/or per-call cap")
      return
    }

    // Build the condition with only the constraints the user supplied; all keys
    // are optional and ANDed by the evaluator. An empty object is a catch-all.
    const condition: Record<string, unknown> = {}
    if (butler.trim()) condition.butler = butler.trim()
    if (complexity) condition.complexity = complexity
    if (trigger) condition.trigger = trigger
    if (purpose) condition.purpose = purpose
    createMutation.mutate({ condition, action })
  }

  // Distinct, sorted target model_ids from the catalog (dedup across tiers).
  const modelIds = useMemo(() => {
    const models = catalogData?.data ?? []
    return Array.from(new Set(models.map((m) => m.model_id))).sort()
  }, [catalogData])

  const conditionSummary =
    butler.trim() || complexity || trigger || purpose
      ? [
          butler.trim() ? `butler = ${butler.trim()}` : null,
          complexity ? `complexity = ${complexity}` : null,
          trigger ? `trigger = ${trigger}` : null,
          purpose ? `purpose = ${purpose}` : null,
        ]
          .filter(Boolean)
          .join(" and ")
      : "any dispatch (catch-all)"

  const effectSummary = [
    model.trim() ? `route to ${model.trim()}` : null,
    maxCostPerCall.trim() ? `cap each call at $${maxCostPerCall.trim()}` : null,
  ]
    .filter(Boolean)
    .join(" and ")

  const triggerPurposeHint = trigger
    ? "Trigger selected. Clear it to choose Purpose; both target the same dispatch source."
    : purpose
      ? "Purpose selected. Clear it to choose Trigger; both target the same dispatch source."
      : "Choose either Trigger or Purpose; both target the same dispatch source."

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="create-rule-form"
      className="mb-4 flex flex-col gap-3 border border-border/60 p-3"
    >
      <Eyebrow>Condition (all optional, ANDed)</Eyebrow>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="flex flex-col gap-1">
          <Eyebrow>Butler</Eyebrow>
          <input
            type="text"
            aria-label="Butler condition"
            placeholder="any butler"
            className="text-xs border rounded px-2 py-1 bg-background"
            value={butler}
            onChange={(e) => setButler(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <Eyebrow>Complexity</Eyebrow>
          <select
            aria-label="Complexity condition"
            className="text-xs border rounded px-2 py-1 bg-background"
            value={complexity}
            onChange={(e) => setComplexity(e.target.value as "" | ComplexityTier)}
          >
            <option value="">any tier</option>
            {COMPLEXITY_TIERS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <Eyebrow>Trigger</Eyebrow>
          <select
            aria-label="Trigger condition"
            aria-describedby="trigger-purpose-alias-hint"
            className="text-xs border rounded px-2 py-1 bg-background disabled:cursor-not-allowed disabled:opacity-50"
            disabled={Boolean(purpose)}
            value={trigger}
            onChange={(e) => setTrigger(e.target.value)}
          >
            <option value="">any trigger</option>
            {TRIGGER_SOURCES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <Eyebrow>Purpose</Eyebrow>
          <select
            aria-label="Purpose condition"
            aria-describedby="trigger-purpose-alias-hint"
            className="text-xs border rounded px-2 py-1 bg-background disabled:cursor-not-allowed disabled:opacity-50"
            disabled={Boolean(trigger)}
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
          >
            <option value="">any purpose</option>
            {PURPOSE_VALUES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p
        id="trigger-purpose-alias-hint"
        data-testid="trigger-purpose-alias-hint"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="text-xs text-muted-foreground"
      >
        {triggerPurposeHint}
      </p>
      <Eyebrow>Action (set at least one effect)</Eyebrow>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <Eyebrow>Route to model</Eyebrow>
          <select
            aria-label="Target model"
            className="text-xs border rounded px-2 py-1 bg-background"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            <option value="">no re-route</option>
            {modelIds.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <Eyebrow>Max cost per call (USD)</Eyebrow>
          <input
            type="number"
            min="0"
            step="0.01"
            inputMode="decimal"
            aria-label="Max cost per call"
            placeholder="no cap"
            className="text-xs border rounded px-2 py-1 bg-background"
            value={maxCostPerCall}
            onChange={(e) => setMaxCostPerCall(e.target.value)}
          />
        </label>
      </div>
      <p className="text-xs text-muted-foreground">
        Matches dispatches where <span className="font-mono">{conditionSummary}</span> and{" "}
        <span className="font-mono">{effectSummary || "…"}</span>.
      </p>
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" className="text-xs h-7" disabled={createMutation.isPending}>
          Create rule
        </Button>
        <Button type="button" variant="ghost" size="sm" className="text-xs h-7" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

function SpendRulesSection() {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
  // Live path: spendPatch invalidates ["spend-rules"] on every spend call
  // event (bu-01r64.4) -- a reconciliation nudge alongside the direct
  // mutation invalidations below (create/delete/reorder), not their
  // replacement. The poll is a bus-aware reconciliation sweep.
  const refetchInterval = useBusAwarePollInterval()
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["spend-rules"],
    queryFn: fetchRules,
    refetchInterval,
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

  // Palette verb (bu-t64p2 -- reachability sweep, bu-qvnce.11 slice 5). Reuses
  // this section's own existing "+ Add rule" affordance.
  const spendRuleCommands = useMemo<PaletteCommand[]>(() => {
    if (creating) return []
    return [
      {
        id: "spend-add-rule",
        label: "Add spend rule",
        keywords: ["new", "rule", "routing"],
        perform: () => setCreating(true),
      },
    ]
  }, [creating])
  useRegisterCommands(spendRuleCommands)

  return (
    <section className="border border-border">
      <div className="flex items-center justify-between gap-4 px-4 py-3 border-b border-border">
        <div className="flex flex-col gap-1">
          <Eyebrow>Routing Rules</Eyebrow>
          <p className="text-xs text-muted-foreground">
            Evaluated top-to-bottom; first match wins. Drag rows to reorder.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] tabular-nums text-muted-foreground">
            {rules.length} {rules.length === 1 ? "rule" : "rules"}
          </span>
          {!creating && (
            <Button
              variant="outline"
              size="sm"
              className="text-xs h-7"
              data-testid="add-rule-button"
              onClick={() => setCreating(true)}
            >
              + Add rule
            </Button>
          )}
        </div>
      </div>
      <div className="p-4">
        {creating && (
          <CreateRuleForm onCancel={() => setCreating(false)} onCreated={() => setCreating(false)} />
        )}
        {isLoading ? (
          <div className="space-y-2">
            {[1, 2].map((i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : isError && rules.length === 0 ? (
          // A failed rules fetch must not render RulesTable's empty "No routing
          // rules are configured" line — that would read as a deliberately
          // empty ruleset when the endpoint is actually down (bu-mkd5r). Only
          // when nothing is cached: a background-refetch error keeps the
          // last-good ruleset visible.
          <SourceDegradedNote
            label="Routing rules"
            detail="unavailable"
            onRetry={() => void refetch()}
          />
        ) : (
          <RulesTable
            rules={rules}
            onDelete={(id) => deleteMutation.mutate(id)}
            onReorder={(id, position) => reorderMutation.mutate({ id, position })}
          />
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Fleet-halt banner + attempts drawer (bu-7o89u.3)
//
// The most consequential thing the spend system can do is halt the fleet: the
// monthly ceiling denies EVERY dispatch, fleet-wide, once MTD reaches it
// (spawner.py:1179-1202). That already happens silently -- quota_skip rows
// land in public.model_dispatch_attempts and are already served by GET
// /api/dispatch/attempts, but zero frontend surfaces read them. This section
// makes an active halt loud (red, above the projected-overage row, which is
// merely a forecast) and gives an evidence drawer with session doors into
// each denied attempt.
// ---------------------------------------------------------------------------

function formatAttemptTimestamp(iso: string): string {
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return "unknown"
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function FleetHaltBanner() {
  // Door target for the attention-ledger owner push (bu-7o89u.4): a
  // ?openDrawer=fleet-halt link lands here with the attempts drawer already
  // expanded, instead of just landing on the page and requiring another
  // click to find the evidence the push is about.
  const [searchParams] = useSearchParams()
  const [drawerOpen, setDrawerOpen] = useState(
    () => searchParams.get("openDrawer") === "fleet-halt",
  )
  const halt = useFleetHaltStatus()

  // A failed dispatch-attempts fetch must never render as "the fleet is not
  // halted" -- that would be a fabricated all-clear on the single most
  // consequential spend signal (fleet degraded-source convention).
  if (halt.isError) {
    return (
      <SourceDegradedNote
        label="Fleet-halt status"
        detail="dispatch denial feed unavailable -- cannot confirm whether the monthly ceiling is halting dispatches"
        testId="fleet-halt-source-error"
      />
    )
  }

  if (halt.isLoading || !halt.active) return null

  return (
    <div className="border border-[var(--red)]/40" data-testid="fleet-halt-banner">
      <div
        className="attention-row flex items-center gap-3 px-4 py-3"
        data-tone="red"
        role="alert"
      >
        <span className="shrink-0 h-2 w-2 rounded-full bg-[var(--red)]" aria-hidden />
        <p className="text-sm flex-1">
          <span className="font-medium">Monthly ceiling reached</span> —{" "}
          <span className="tabular-nums font-medium">{halt.deniedTotal}</span>{" "}
          {halt.deniedTotal === 1 ? "dispatch" : "dispatches"} denied since{" "}
          <span className="tabular-nums font-medium">
            {halt.since ? formatAttemptTimestamp(halt.since) : "unknown"}
          </span>
          {" · "}
          <span className="tabular-nums font-medium">{halt.deniedToday}</span> denied today.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="shrink-0 text-xs h-7"
          data-testid="fleet-halt-drawer-toggle"
          onClick={() => setDrawerOpen((open) => !open)}
        >
          {drawerOpen ? "Hide" : "View"} denied attempts
        </Button>
      </div>
      {drawerOpen && (
        <div className="p-4 border-t border-[var(--red)]/40" data-testid="fleet-halt-drawer">
          {halt.recentAttempts.length === 0 ? (
            <p className="font-serif italic text-muted-foreground text-sm">
              No recent denied attempts loaded.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                    <th className="text-left py-2 px-2 font-normal">Butler</th>
                    <th className="text-left py-2 px-2 font-normal">When</th>
                    <th className="text-left py-2 px-2 font-normal">Reason</th>
                    <th className="text-right py-2 px-2 font-normal">Session</th>
                  </tr>
                </thead>
                <tbody>
                  {halt.recentAttempts.map((a, i) => (
                    <tr
                      key={`${a.session_id ?? a.logical_session_id ?? "none"}-${a.ts}-${i}`}
                      className="border-b border-border/60"
                      data-testid="fleet-halt-attempt-row"
                    >
                      <td className="py-2 px-2">{a.butler}</td>
                      <td className="py-2 px-2 text-xs text-muted-foreground">
                        {formatAttemptTimestamp(a.ts)}
                      </td>
                      <td className="py-2 px-2 text-xs text-muted-foreground">
                        {a.failure_reason ?? "—"}
                      </td>
                      <td className="py-2 px-2 text-right text-xs">
                        {/* Session door (bu-7o89u.3): pre-session ceiling denials
                            have no session_id yet -- render a plain dash instead
                            of a dead link (mirrors TopSessionsSection's pattern). */}
                        {a.session_id ? (
                          <Link to={`/sessions/${a.session_id}`} className="hover:underline">
                            View session
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SpendPage() {
  // Posture — always the current month, independent of the explore-section
  // time window below (matches the original /settings/spend behavior).
  // Live path: spendPatch invalidates ["spend-forecast"] on every spend call
  // event (bu-01r64.4) -- the poll below is a bus-aware reconciliation
  // sweep, not the primary update path.
  const forecastRefetchInterval = useBusAwarePollInterval()
  const {
    data: forecastData,
    isLoading: forecastLoading,
    isError: forecastError,
    refetch: refetchForecast,
  } = useQuery({
    queryKey: ["spend-forecast"],
    queryFn: fetchForecast,
    refetchInterval: forecastRefetchInterval,
  })
  const forecast = forecastData?.data

  // §5.3 — Subscribe to the shared fleet event bus's "spend" events (bu-qvnce.14
  // slice 2; formerly its own /api/spend/stream socket) and update KPIs
  // incrementally. streamedCostUsd is a monotonic cumulative counter of live
  // "call" events received since mount — it never resets on its own.
  const { streamedCostUsd, streamedUnpricedEvents = [] } = useSpendTicker()

  // Each polled forecast (every 120s) is a fresh MTD baseline that already
  // reflects any spend that streamed in before that poll landed. Pin the
  // streamedCostUsd value AS OF the most recent baseline so only spend that
  // streamed in AFTER it gets added on top — otherwise the same live events
  // get counted once by the stream and again by the next poll, compounding
  // every refresh (bu-qvnce.2). Adjusted during render (React's sanctioned
  // "derive state from a prop/query change" pattern) rather than a ref,
  // since refs cannot be read or written during render (react-hooks/refs).
  const [baselineForecast, setBaselineForecast] = useState(forecast)
  const [baselineStreamedCostUsd, setBaselineStreamedCostUsd] = useState(streamedCostUsd)
  const [baselineStreamedUnpricedEventCount, setBaselineStreamedUnpricedEventCount] = useState(
    streamedUnpricedEvents.length,
  )
  if (forecast !== baselineForecast) {
    setBaselineForecast(forecast)
    setBaselineStreamedCostUsd(streamedCostUsd)
    setBaselineStreamedUnpricedEventCount(streamedUnpricedEvents.length)
  }

  const liveForecast = useMemo(() => {
    if (!forecast) return forecast
    const sinceBaseline =
      forecast === baselineForecast ? streamedCostUsd - baselineStreamedCostUsd : 0
    const liveUnpricedEvents =
      forecast === baselineForecast
        ? streamedUnpricedEvents.slice(baselineStreamedUnpricedEventCount)
        : []
    if (sinceBaseline <= 0 && liveUnpricedEvents.length === 0) return forecast
    const liveUnpricedModels = mergeUnpricedModels(forecast.unpriced_models, liveUnpricedEvents)
    const hasPricedLiveSpend = sinceBaseline > 0
    const liveMtd = forecast.mtd_usd + Math.max(sinceBaseline, 0)
    const liveProjected = hasPricedLiveSpend
      ? (liveMtd / Math.max(forecast.days_elapsed, 1)) * forecast.days_in_month
      : forecast.projected_eom_usd
    return {
      ...forecast,
      ...(hasPricedLiveSpend ? { mtd_usd: liveMtd, projected_eom_usd: liveProjected } : {}),
      unpriced_models: liveUnpricedModels,
      ceiling_blind_to_unpriced_models: Math.max(
        forecast.ceiling_blind_to_unpriced_models ?? 0,
        liveUnpricedModels.length,
      ),
    }
  }, [
    forecast,
    streamedCostUsd,
    streamedUnpricedEvents,
    baselineForecast,
    baselineStreamedCostUsd,
    baselineStreamedUnpricedEventCount,
  ])

  // NOTE: spend-breakdown invalidation on live spend events used to be a
  // bespoke, throttled useEffect here. bu-01r64.4 moved that coverage into
  // spendPatch (event-cache-registry.ts), which now invalidates
  // ["spend-breakdown"] globally on every "spend" bus event alongside
  // cost-summary/daily-costs/top-sessions/costs-by-schedule -- see
  // BreakdownSection's useBusAwarePollInterval-driven poll above for the
  // reconciliation-sweep safety net.

  // Over-ceiling attention condition — the only state-color-on-background use.
  // Never fires from a degraded ledger source (bu-7o89u.1): ceiling_source_error
  // means projected_eom_usd/ceiling_usd are fabricated zeros/null, not a real
  // reading -- an "over ceiling" banner built from those would be a false alarm.
  const overCeiling =
    !liveForecast?.ceiling_source_error &&
    liveForecast?.ceiling_usd != null &&
    liveForecast.projected_eom_usd > liveForecast.ceiling_usd

  // What changed — explore window (daily stacked chart + movers). Defaults
  // to the last 7 days via useTimeWindow's "today" preset fallback logic —
  // callers can widen with the picker below.
  const timeWindow = useTimeWindow(OWNER_TZ_DEFAULT)

  // Palette verbs (bu-t64p2 -- reachability sweep, bu-qvnce.11 slice 5).
  // Reuses TimeWindowPicker's own preset setters -- "change window" from the
  // dispatch context.
  const spendWindowCommands = useMemo<PaletteCommand[]>(() => {
    const commands: PaletteCommand[] = []
    if (timeWindow.preset !== "today") {
      commands.push({
        id: "spend-window-today",
        label: "Change window: today",
        keywords: ["window", "range", "today"],
        perform: () => timeWindow.setPreset("today"),
      })
    }
    if (timeWindow.preset !== "week") {
      commands.push({
        id: "spend-window-week",
        label: "Change window: this week",
        keywords: ["window", "range", "week"],
        perform: () => timeWindow.setPreset("week"),
      })
    }
    return commands
    // eslint-disable-next-line react-hooks/exhaustive-deps -- timeWindow.setPreset is stable (useCallback); timeWindow.preset is what actually varies the resulting command set.
  }, [timeWindow.preset])
  useRegisterCommands(spendWindowCommands)

  const {
    data: dailyResponse,
    isLoading: dailyLoading,
    isError: dailyError,
  } = useDailySpend(timeWindow.from, timeWindow.to, {
    refetchInterval: timeWindow.pollingDisabled ? false : 60_000,
  })
  const dailyData = useMemo(() => dailyResponse?.data ?? [], [dailyResponse])
  // Butlers dropped from GET /api/spend/daily's fan-out — passed to the stacked
  // chart so vanished butlers are footnoted, not silently absent (bu-jad4j.3).
  const dailyUnavailableButlers = dailyResponse?.meta?.unavailable_butlers ?? []
  const dailyUnpricedModels = dailyResponse?.meta?.unpriced_models ?? []
  const dailyDivergences = dailyResponse?.meta?.divergences ?? []

  // Movers — current window vs the immediately preceding window of equal
  // length (e.g. "last 7 days" vs "the 7 days before that").
  const windowDays = differenceInCalendarDays(timeWindow.to, timeWindow.from) + 1
  const prevTo = useMemo(() => subDays(timeWindow.from, 1), [timeWindow.from])
  const prevFrom = useMemo(() => subDays(prevTo, windowDays - 1), [prevTo, windowDays])

  const {
    data: currentSummary,
    isLoading: currentSummaryLoading,
    isError: currentSummaryError,
  } = useSpendSummary(undefined, timeWindow.from, timeWindow.to)
  const {
    data: priorSummary,
    isLoading: priorSummaryLoading,
    isError: priorSummaryError,
  } = useSpendSummary(undefined, prevFrom, prevTo)

  return (
    <Page archetype="overview" title="Spend">
      <div className="space-y-6">
        {/* Verdict opener — pace, projection confidence (previously fetched
            but discarded, see ForecastData.projection_confidence), and the
            top mover, composed from data already fetched below (JARVIS
            pursuit move 9). */}
        <SpendVerdictOpener
          forecast={liveForecast}
          forecastLoading={forecastLoading}
          forecastError={forecastError}
          currentByButler={currentSummary?.data?.by_butler ?? {}}
          priorByButler={priorSummary?.data?.by_butler ?? {}}
          unavailableButlers={
            new Set([
              ...(currentSummary?.data?.unavailable_butlers ?? []),
              ...(priorSummary?.data?.unavailable_butlers ?? []),
            ])
          }
          moversLoading={currentSummaryLoading || priorSummaryLoading}
          moversError={currentSummaryError || priorSummaryError}
        />

        {/* Fleet-halt banner (bu-7o89u.3) — the ceiling IS denying dispatches
            right now, above the merely-projected over-ceiling row below. */}
        <FleetHaltBanner />

        {/* Over-ceiling attention row — projected EOM exceeds the ceiling */}
        {overCeiling && liveForecast && (
          <div
            className="attention-row flex items-center gap-3 px-4 py-3"
            data-tone="red"
            role="alert"
            aria-label="Projected spend exceeds the monthly ceiling"
          >
            <span className="shrink-0 h-2 w-2 rounded-full bg-[var(--red)]" aria-hidden />
            <p className="text-sm">
              Projected end-of-month spend{" "}
              <span className="tabular-nums font-medium">
                {formatCostUsd(liveForecast.projected_eom_usd)}
              </span>{" "}
              exceeds the monthly ceiling of{" "}
              <span className="tabular-nums font-medium">{formatCostUsd(liveForecast.ceiling_usd!)}</span>.
            </p>
          </div>
        )}

        {/* Posture: KPI strip */}
        {forecastLoading && !liveForecast ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 border-t border-l border-border/60">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="flex flex-col gap-1.5 px-4 py-3 border-r border-b border-border/60">
                <Skeleton className="h-3 w-20" />
                <Skeleton className="h-8 w-16" />
              </div>
            ))}
          </div>
        ) : liveForecast && liveForecast.ceiling_source_error ? (
          // Ledger/gate source degraded (bu-7o89u.1): mtd_usd/projected_eom_usd
          // /ceiling_usd are fabricated zeros/null, not a real "$0 MTD" reading.
          // Never render KpiStrip from them -- that would be the exact truth-
          // amnesty bug this bead exists to close.
          <SourceDegradedNote
            label="Spend forecast"
            detail="ceiling source unavailable"
            onRetry={() => void refetchForecast()}
          />
        ) : liveForecast ? (
          <KpiStrip forecast={liveForecast} />
        ) : forecastError ? (
          // Forecast down with nothing cached: surface it here (the posture
          // slot) rather than rendering a blank strip that reads as "$0 so far
          // this month" (bu-mkd5r, three-way state contract).
          <SourceDegradedNote
            label="Spend forecast"
            detail="unavailable"
            onRetry={() => void refetchForecast()}
          />
        ) : null}

        {/* Posture: forecast chart */}
        <section className="border border-border">
          <div className="flex items-start justify-between gap-4 px-4 py-3 border-b border-border">
            <div className="flex flex-col gap-1">
              <Eyebrow>Forecast</Eyebrow>
              <p className="text-xs text-muted-foreground">
                Solid = actual MTD spend. Dashed = linear projection to end of month.
                {liveForecast?.ceiling_usd != null ? " Red hairline = monthly ceiling." : ""}
              </p>
            </div>
            {liveForecast && <CeilingEdit currentCeiling={liveForecast.ceiling_usd} />}
          </div>
          <div className="p-4">
            {forecastLoading && !liveForecast ? (
              <Skeleton className="h-48 w-full" />
            ) : liveForecast && liveForecast.ceiling_source_error ? (
              // The dashed projection is derived from the degraded ledger MTD
              // (would render as a flat $0 line) -- show only the real solid
              // actuals and drop the ceiling hairline (ceiling_usd is null here
              // anyway). KpiStrip's SourceDegradedNote above already names the
              // outage; don't repeat it inline over the chart.
              <ForecastChart
                days={liveForecast.days.filter((d) => !d.projected)}
                ceiling_usd={null}
              />
            ) : liveForecast ? (
              <ForecastChart days={liveForecast.days} ceiling_usd={liveForecast.ceiling_usd} />
            ) : forecastError ? (
              // Outage already announced in the posture slot above; render
              // nothing here rather than "No forecast data is available yet.",
              // which would contradict it by reading as a genuine empty
              // (bu-mkd5r, three-way state contract).
              null
            ) : (
              <p className="font-serif italic text-muted-foreground text-sm">
                No forecast data is available yet.
              </p>
            )}
            {liveForecast && (liveForecast.unavailable_butlers?.length ?? 0) > 0 && (
              // Butlers dropped from the per-day fan-out powering the solid
              // actuals above (independent of ceiling_source_error) -- the
              // chart's actuals undercount, so name the gap (bu-jad4j.3 style).
              <SourceDegradedNote
                className="mt-3"
                label="Forecast actuals"
                detail={`excluded, cost source unavailable: ${liveForecast.unavailable_butlers!.join(", ")}`}
                testId="forecast-unavailable-butlers"
              />
            )}
            {liveForecast && (liveForecast.ceiling_blind_to_unpriced_models ?? 0) > 0 && (
              <SourceDegradedNote
                className="mt-3"
                label="Monthly ceiling"
                detail={`blind to ${liveForecast.ceiling_blind_to_unpriced_models} unpriced model${liveForecast.ceiling_blind_to_unpriced_models === 1 ? "" : "s"}: ${unpricedModelNames(liveForecast.unpriced_models)}`}
                testId="forecast-unpriced"
              />
            )}
            {liveForecast && liveForecast.divergences && liveForecast.divergences.length > 0 && (
              <SourceDegradedNote
                className="mt-3"
                label="Forecast attribution"
                detail={`ledger/session token drift in ${liveForecast.divergences.length} day-butler bucket${liveForecast.divergences.length === 1 ? "" : "s"}`}
                testId="forecast-divergence"
              />
            )}
            {liveForecast?.divergence_source_error && (
              <SourceDegradedNote
                className="mt-3"
                label="Forecast attribution"
                detail="session-to-ledger comparison unavailable"
                testId="forecast-divergence-source-error"
              />
            )}
            {liveForecast?.historical_attribution_note && (
              <SourceDegradedNote
                className="mt-3"
                label="Historical attribution"
                detail={liveForecast.historical_attribution_note}
                testId="forecast-historical-attribution"
              />
            )}
          </div>
        </section>

        {/* What changed: movers strip */}
        <MoversStrip
          current={currentSummary?.data?.by_butler ?? {}}
          prior={priorSummary?.data?.by_butler ?? {}}
          windowDays={windowDays}
          isLoading={currentSummaryLoading || priorSummaryLoading}
          isError={currentSummaryError || priorSummaryError}
          unavailableButlers={
            new Set([
              ...(currentSummary?.data?.unavailable_butlers ?? []),
              ...(priorSummary?.data?.unavailable_butlers ?? []),
            ])
          }
        />

        {/* What changed: time window + honest per-butler-per-day stacked chart */}
        <section className="border border-border">
          <div className="flex flex-col gap-3 px-4 py-3 border-b border-border">
            <Eyebrow>Daily Spend</Eyebrow>
            <TimeWindowPicker window={timeWindow} />
          </div>
          <div className="p-4">
            <CostStripeChart
              data={dailyData}
              isLoading={dailyLoading}
              isError={dailyError}
              unavailableButlers={dailyUnavailableButlers}
            />
            {dailyUnpricedModels.length > 0 && (
              <SourceDegradedNote
                className="mt-3"
                label="Daily Spend"
                detail={`excludes ${unpricedCallCount(dailyUnpricedModels).toLocaleString()} unpriced calls (${unpricedModelNames(dailyUnpricedModels)})`}
                testId="daily-spend-unpriced"
              />
            )}
            {dailyDivergences.length > 0 && (
              <SourceDegradedNote
                className="mt-3"
                label="Daily Spend"
                detail={`ledger/session token drift in ${dailyDivergences.length} day-butler bucket${dailyDivergences.length === 1 ? "" : "s"}`}
                testId="daily-spend-divergence"
              />
            )}
            {dailyResponse?.meta?.divergence_source_error && (
              <SourceDegradedNote
                className="mt-3"
                label="Daily Spend"
                detail="session-to-ledger comparison unavailable"
                testId="daily-spend-divergence-source-error"
              />
            )}
            {dailyResponse?.meta?.historical_attribution_note && (
              <SourceDegradedNote
                className="mt-3"
                label="Historical attribution"
                detail={dailyResponse.meta.historical_attribution_note}
                testId="daily-spend-historical-attribution"
              />
            )}
          </div>
        </section>

        {/* Why: evidence layer, scoped to the same window as the daily chart */}
        <TopSessionsSection from={timeWindow.from} to={timeWindow.to} />
        <ByScheduleSection from={timeWindow.from} to={timeWindow.to} />

        {/* Why: period breakdown */}
        <BreakdownSection />

        {/* Controls: routing rules, in context with the signals they govern */}
        <SpendRulesSection />
      </div>
    </Page>
  )
}
