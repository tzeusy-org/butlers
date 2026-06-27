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
//
// Design language: Dispatch. No card chrome, no word-badges — state is a
// {dot, numeral, colour} only when state demands. Display weight 500 (never
// 700). Numerals are tabular. Mirrors SettingsConsolePage / SettingsModelsPage
// and the shared atoms in components/butler-detail/atoms.tsx.
// ---------------------------------------------------------------------------

import { useState, useMemo, useRef, useEffect, useCallback } from "react"
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Page } from "@/components/ui/page"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { apiFetch } from "@/api/client"
import { useSpendStream } from "@/hooks/use-spend-stream"
import { useModelCatalog } from "@/hooks/use-model-catalog"
import type { ComplexityTier } from "@/api/types"
import { cn } from "@/lib/utils"

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
  projection_confidence: "low" | "normal"
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
// Shared mono eyebrow — 10px uppercase, 0.14em tracking, muted
// ---------------------------------------------------------------------------

function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p
      className={cn(
        "font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground leading-none",
        className,
      )}
    >
      {children}
    </p>
  )
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

function fmtUsd(n: number): string {
  if (n < 0.01) return "$0.00"
  return `$${n.toFixed(2)}`
}

function fmtUsdPrecise(n: number): string {
  return `$${n.toFixed(4)}`
}

// ---------------------------------------------------------------------------
// KPI Strip — hairline-divided, no card chrome. Mega numerals are weight 500,
// tabular. State colour appears only when state demands (over-ceiling).
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
          tone === "red" ? "text-[var(--red)]" : "text-foreground",
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
        value={fmtUsd(forecast.mtd_usd)}
        sub={`${forecast.days_elapsed} day${forecast.days_elapsed === 1 ? "" : "s"} elapsed`}
      />
      <KpiCell
        testId="kpi-projected-eom"
        label="Projected EOM"
        value={fmtUsd(forecast.projected_eom_usd)}
        tone={overCeiling ? "red" : "fg"}
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
    <section className="border border-border">
      <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-border">
        <Eyebrow>Spend Breakdown</Eyebrow>
        <div className="flex gap-1">
          {(["butler", "model", "feature"] as BreakdownBy[]).map((dim) => (
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
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-4 w-full" />)}
          </div>
        ) : entries.length === 0 ? (
          <p className="font-serif italic text-muted-foreground text-sm">
            No spend has been recorded yet.
          </p>
        ) : (
          <div className="space-y-2">
            {entries.map(([label, value]) => (
              <BreakdownBar key={label} label={label} value={value} maxValue={maxValue} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------
// Routing rules table (drag-to-reorder)
// ---------------------------------------------------------------------------

// Render a condition/action JSONB object as labelled chips instead of raw JSON, so
// the table reflects the same structured vocabulary the editor produces.
function fmtConstraintValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((v) => String(v)).join(" | ")
  return String(value)
}

function conditionChips(condition: Record<string, unknown>): { label: string; value: string }[] {
  const order = ["butler", "complexity", "tier", "trigger"]
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
  "schedule",
  "healing",
  "retry",
  "qa",
  "extraction",
  "external",
]

// ---------------------------------------------------------------------------
// Create-rule form — collects a condition (butler + complexity + trigger, all
// optional, ANDed) and an action (route-to model and/or per-call cost cap; at
// least one effect required). Produces a rule whose shape the dispatch-time
// evaluator and the enforced API schema both accept. An empty condition is a
// valid catch-all.
// ---------------------------------------------------------------------------

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
    createMutation.mutate({ condition, action })
  }

  // Distinct, sorted target model_ids from the catalog (dedup across tiers).
  const modelIds = useMemo(() => {
    const models = catalogData?.data ?? []
    return Array.from(new Set(models.map((m) => m.model_id))).sort()
  }, [catalogData])

  const conditionSummary =
    butler.trim() || complexity || trigger
      ? [
          butler.trim() ? `butler = ${butler.trim()}` : null,
          complexity ? `complexity = ${complexity}` : null,
          trigger ? `trigger = ${trigger}` : null,
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

  return (
    <form
      onSubmit={handleSubmit}
      data-testid="create-rule-form"
      className="mb-4 flex flex-col gap-3 border border-border/60 p-3"
    >
      <Eyebrow>Condition (all optional, ANDed)</Eyebrow>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
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
            className="text-xs border rounded px-2 py-1 bg-background"
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
      </div>
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
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-xs h-7"
          onClick={onCancel}
        >
          Cancel
        </Button>
      </div>
    </form>
  )
}

function SpendRulesSection() {
  const queryClient = useQueryClient()
  const [creating, setCreating] = useState(false)
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
          <CreateRuleForm
            onCancel={() => setCreating(false)}
            onCreated={() => setCreating(false)}
          />
        )}
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
      </div>
    </section>
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

  // Over-ceiling attention condition — the only state-color-on-background use.
  const overCeiling =
    liveForecast?.ceiling_usd != null &&
    liveForecast.projected_eom_usd > liveForecast.ceiling_usd

  return (
    <Page archetype="overview" title="Spend">
      <div className="space-y-6">
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
                {fmtUsd(liveForecast.projected_eom_usd)}
              </span>{" "}
              exceeds the monthly ceiling of{" "}
              <span className="tabular-nums font-medium">
                {fmtUsd(liveForecast.ceiling_usd!)}
              </span>
              .
            </p>
          </div>
        )}

        {/* KPI strip */}
        {forecastLoading && !liveForecast ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 border-t border-l border-border/60">
            {[1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="flex flex-col gap-1.5 px-4 py-3 border-r border-b border-border/60"
              >
                <Skeleton className="h-3 w-20" />
                <Skeleton className="h-8 w-16" />
              </div>
            ))}
          </div>
        ) : liveForecast ? (
          <KpiStrip forecast={liveForecast} />
        ) : null}

        {/* Forecast SVG chart */}
        <section className="border border-border">
          <div className="flex items-start justify-between gap-4 px-4 py-3 border-b border-border">
            <div className="flex flex-col gap-1">
              <Eyebrow>Forecast</Eyebrow>
              <p className="text-xs text-muted-foreground">
                Solid = actual MTD spend. Dashed = linear projection to end of month.
                {liveForecast?.ceiling_usd != null
                  ? " Red hairline = monthly ceiling."
                  : ""}
              </p>
            </div>
            {liveForecast && (
              <CeilingEdit currentCeiling={liveForecast.ceiling_usd} />
            )}
          </div>
          <div className="p-4">
            {forecastLoading && !liveForecast ? (
              <Skeleton className="h-48 w-full" />
            ) : liveForecast ? (
              <ForecastChart days={liveForecast.days} ceiling_usd={liveForecast.ceiling_usd} />
            ) : (
              <p className="font-serif italic text-muted-foreground text-sm">
                No forecast data is available yet.
              </p>
            )}
          </div>
          {/* TODO: Add anomaly detection (deferred §D13 — threshold TBD) */}
        </section>

        {/* Breakdown */}
        <BreakdownSection />

        {/* Routing rules */}
        <SpendRulesSection />
      </div>
    </Page>
  )
}
