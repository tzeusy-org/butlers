/**
 * ButlerFrictionPanel -- friction ledger + outcome-carrying self-observation
 * console surface (bu-8cdl1.9 S3).
 *
 * Typed friction episodes (degenerate_tool_loop, guardrail_termination,
 * classification_timeout, recovered_error, dead_end) are derived
 * deterministically at session close (bu-8cdl1.9 S2) into the
 * `sessions_friction` table and had zero frontend wiring until this panel --
 * guardrail terminations and recovered errors were computed and then
 * routed nowhere. This surfaces both that typed breakdown and
 * `sessions_summary`'s succeeded/failed/by_error_marker outcome aggregates
 * for the same window, so a butler's self-observation is visible, not just
 * recorded.
 */

import { useState } from "react"

import { MonoLabel, Panel } from "@/components/butler-detail/atoms"
import type { Tone } from "@/components/butler-detail/atoms-utils"
import { SourceDegradedNote } from "@/components/ui/query-boundary"
import { useButlerFrictionSummary } from "@/hooks/use-butler-analytics"

type Period = "today" | "7d" | "30d"

const PERIOD_OPTIONS: { value: Period; label: string }[] = [
  { value: "today", label: "TODAY" },
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
]

// Every kind sessions_friction.kind accepts (core_220), in the fixed display
// order the panel renders them -- not insertion order from the API, which
// depends on which kinds actually occurred in the window.
const FRICTION_KIND_ORDER = [
  "degenerate_tool_loop",
  "guardrail_termination",
  "classification_timeout",
  "recovered_error",
  "dead_end",
] as const

const FRICTION_KIND_LABELS: Record<string, string> = {
  degenerate_tool_loop: "degenerate tool loop",
  guardrail_termination: "guardrail termination",
  classification_timeout: "classification timeout",
  recovered_error: "recovered error",
  dead_end: "dead end",
}

/** recovered_error is a success that carried a leftover error -- amber, not red. */
function frictionKindTone(kind: string, count: number): Tone {
  if (count === 0) return "dim"
  if (kind === "recovered_error") return "amber"
  return "red"
}

function PeriodToggle({ value, onChange }: { value: Period; onChange: (p: Period) => void }) {
  return (
    <div
      role="group"
      aria-label="Friction summary period"
      className="inline-flex items-center rounded-md border border-border"
      data-testid="friction-period-toggle"
    >
      {PERIOD_OPTIONS.map((option) => {
        const isActive = value === option.value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={isActive}
            onClick={() => onChange(option.value)}
            className={[
              "inline-flex items-center justify-center px-2 py-1",
              "font-mono text-[10px] uppercase tabular-nums transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:ring-offset-1",
              "first:rounded-l-sm last:rounded-r-sm",
              isActive ? "bg-foreground text-background" : "bg-transparent text-foreground hover:bg-muted",
            ].join(" ")}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

function FrictionEpisodeList({ byKind }: { byKind: Record<string, number> }) {
  const total = FRICTION_KIND_ORDER.reduce((sum, kind) => sum + (byKind[kind] ?? 0), 0)
  if (total === 0) {
    return <MonoLabel color="dim">no friction episodes this period</MonoLabel>
  }
  return (
    <ul data-testid="friction-episode-list">
      {FRICTION_KIND_ORDER.map((kind) => {
        const count = byKind[kind] ?? 0
        return (
          <li
            key={kind}
            className="flex items-baseline justify-between gap-3 py-1 border-b border-border/40 last:border-b-0"
            data-testid="friction-episode-row"
          >
            <MonoLabel color={frictionKindTone(kind, count)} className="text-[11px]">
              {FRICTION_KIND_LABELS[kind] ?? kind}
            </MonoLabel>
            <span className="font-mono tnum text-xs text-foreground">{count}</span>
          </li>
        )
      })}
    </ul>
  )
}

function ErrorMarkerList({ byErrorMarker }: { byErrorMarker: Record<string, number> }) {
  const entries = Object.entries(byErrorMarker).sort(([, a], [, b]) => b - a)
  if (entries.length === 0) {
    return <MonoLabel color="dim">no failures this period</MonoLabel>
  }
  return (
    <ul data-testid="friction-error-marker-list">
      {entries.map(([marker, count]) => (
        <li
          key={marker}
          className="flex items-baseline justify-between gap-3 py-1 border-b border-border/40 last:border-b-0"
          data-testid="friction-error-marker-row"
        >
          <MonoLabel color="dim" className="text-[11px]">
            {marker}
          </MonoLabel>
          <span className="font-mono tnum text-xs text-foreground">{count}</span>
        </li>
      ))}
    </ul>
  )
}

export interface ButlerFrictionPanelProps {
  butlerName: string
}

export function ButlerFrictionPanel({ butlerName }: ButlerFrictionPanelProps) {
  const [period, setPeriod] = useState<Period>("7d")
  const { data, isLoading, isError } = useButlerFrictionSummary(butlerName, period)

  return (
    <Panel title="friction" span={4} testId="panel-friction">
      <div className="flex items-center justify-between gap-2 mb-2">
        <MonoLabel color="dim" className="opacity-60">
          typed episodes + session outcomes
        </MonoLabel>
        <PeriodToggle value={period} onChange={setPeriod} />
      </div>

      {isLoading ? (
        <MonoLabel color="dim">loading</MonoLabel>
      ) : isError || !data ? (
        <SourceDegradedNote label="Friction summary" testId="friction-summary-error" />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <MonoLabel color="dim" className="mb-1 block">
              friction episodes ({data.total})
            </MonoLabel>
            <FrictionEpisodeList byKind={data.by_kind} />
          </div>
          <div>
            <MonoLabel color="dim" className="mb-1 block">
              outcomes ({data.succeeded} succeeded / {data.failed} failed)
            </MonoLabel>
            <ErrorMarkerList byErrorMarker={data.by_error_marker} />
          </div>
        </div>
      )}
    </Panel>
  )
}
