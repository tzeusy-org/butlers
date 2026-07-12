// ---------------------------------------------------------------------------
// ButlerDetailHeader — header-slot wrapper for the butler detail page.
// (bu-ja5bt.3)
//
// Composes:
//   - Butler identity — name (H1) and description, hue via <ButlerMark>
//
// The header does NOT render ButlerDetailActions; the Page archetype provides
// a separate `actions` slot for that. This component covers ONLY the header
// slot content per the spec.
//
// Contract:
//   - props: butler (active butler name)
//   - Skeleton state while data loads
//   - Error state mirrors loaded dimensions to avoid layout shift
//   - Token-only chrome: no hex, oklch, rgb literals, no inline style
//   - Butler hue appears ONLY on <ButlerMark> — never on other chrome elements
//   - No em-dashes in any JSX string literal
//   - No `pid` anywhere (gate violation)
//
// Doctrine: design-language.md Non-negotiables 1 (token system), 2 (Page is a
// primitive), 6 (no em-dashes). Butler-hue scope restricted to ButlerMark.
// ---------------------------------------------------------------------------

import type { ReactNode } from "react"

import { ButlerMark } from "@/components/ui/ButlerMark"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { useButler } from "@/hooks/use-butlers"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
import { useSchedules } from "@/hooks/use-schedules"
import { useSpendSummary } from "@/hooks/use-spend"
import { formatCostUsd } from "@/lib/format-cost"
import { titleize } from "@/lib/utils"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ButlerDetailHeaderProps {
  /** The active butler name (from URL params). */
  butler: string
  /** Operational controls rendered on the right side of the identity header. */
  actions?: ReactNode
}

// Delegates to the shared formatter [bu-sd0l7.3] — this used to clamp any
// nonzero sub-cent spend to "$0.00" (the exact bug documented at the top of
// lib/format-cost.ts), before formatCostUsd existed.
function formatCurrency(amount: number | null | undefined): string {
  return amount == null ? "--" : formatCostUsd(amount)
}

/**
 * Earliest upcoming `next_run_at` across a butler's enabled schedules, or
 * null when there is no schedule (or none has a known next fire time).
 */
function earliestNextRun(schedules: { enabled: boolean; next_run_at: string | null }[]): string | null {
  const candidates = schedules
    .filter((s) => s.enabled && s.next_run_at)
    .map((s) => s.next_run_at as string)
  if (candidates.length === 0) return null
  return candidates.reduce((earliest, current) =>
    new Date(current).getTime() < new Date(earliest).getTime() ? current : earliest,
  )
}

function activityToneClass(activity: string): string {
  switch (activity) {
    case "running":
      return "text-[var(--green)]"
    case "overdue":
      return "text-[var(--amber-text)]"
    case "offline":
    case "quarantined":
      return "text-destructive"
    default:
      return "text-muted-foreground"
  }
}

// ---------------------------------------------------------------------------
// ButlerDetailHeader
// ---------------------------------------------------------------------------

/**
 * Header-slot primitive for the butler detail page.
 *
 * Renders the active butler identity block (name + description via ButlerMark
 * hue scope). Intended to be passed as the `header` prop on
 * `<Page archetype="status-board">`.
 *
 * The sibling-butler navigation now lives in the shell PageHeader beside the
 * search/theme controls. The actions slot (ButlerDetailActions) is provided
 * separately by the Page shell; this component does not render it.
 *
 * @example
 *   <ButlerDetailHeader butler="relationship" />
 */
export function ButlerDetailHeader({ butler, actions }: ButlerDetailHeaderProps) {
  const { rows, aggregates } = useButlerStatusBoard()
  const { data: butlerResponse } = useButler(butler)
  const { data: schedulesResponse } = useSchedules(butler)
  const { data: spendResponse } = useSpendSummary("today")

  // Find the active butler's description from the status board rows.
  // Falls back to null when loading, errored, or not found.
  const activeRow = rows.find((r) => r.name === butler) ?? null
  const butlerDetail = butlerResponse?.data ?? null
  const description = activeRow?.description ?? butlerDetail?.description ?? null
  const activity = activeRow?.activity ?? "unknown"

  // Header trivia (bu-86c4c.18): port/uptime told the operator nothing about
  // what the butler actually did or will do. Replace it with the three facts
  // a calm-confidence glance needs -- last run, next scheduled fire, and
  // today's spend -- sourced from the same data already fetched elsewhere on
  // this page (status board heartbeat, schedules, spend summary).
  const lastRunISO = activeRow?.lastRunISO ?? null
  const nextRunISO = earliestNextRun(schedulesResponse?.data ?? [])
  const costToday = spendResponse?.data?.by_butler?.[butler] ?? null

  // ---------------------------------------------------------------------------
  // Skeleton state
  // ---------------------------------------------------------------------------

  if (aggregates.isLoading) {
    return (
      <div
        data-testid="butler-detail-header"
        className="flex flex-col gap-2 border-b border-border px-7 py-3"
        aria-busy="true"
      >
        {/* Identity skeleton — mirrors loaded identity block height */}
        {/* ButlerMark is h-6 (24px); H1 text-2xl has line-height 2rem (h-8=32px) */}
        <div className="flex items-center gap-2 py-0.5">
          <Skeleton className="h-6 w-6 shrink-0 rounded" />
          <Skeleton className="h-8 w-32 rounded-sm" />
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Error state — mirrors loaded-state dimensions to avoid layout shift
  // ---------------------------------------------------------------------------

  if (aggregates.isError && rows.length === 0) {
    return (
      <div
        data-testid="butler-detail-header"
        className="flex flex-col gap-2 border-b border-border px-7 py-3"
      >
        {/* Identity block preserved at loaded dimensions */}
        <div className="flex items-center gap-2 py-0.5">
          {/* Butler hue appears ONLY on ButlerMark */}
          <ButlerMark name={butler} size={24} tone="fill" />
          <h1 className="text-2xl font-bold tracking-tight capitalize">{butler}</h1>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Loaded state
  // ---------------------------------------------------------------------------

  return (
    <div
      data-testid="butler-detail-header"
      className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 border-b border-border px-7 py-3 md:grid-cols-[auto_1fr_auto]"
    >
      <ButlerMark name={butler} size={40} tone={activity === "running" ? "fill" : "neutral"} />

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] uppercase tracking-[0.06em]">
          <span className="text-muted-foreground">/butlers/{butler}</span>
          <span className={`inline-flex items-center gap-1.5 ${activityToneClass(activity)}`}>
            <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
            {activity}
          </span>
          <span className="text-muted-foreground" data-testid="butler-header-facts">
            last run{" "}
            {lastRunISO ? <Time value={lastRunISO} mode="relative-compact" /> : "--"}
            {" · next "}
            {nextRunISO ? <Time value={nextRunISO} mode="relative-compact" /> : "--"}
            {" · "}
            {formatCurrency(costToday)} today
          </span>
        </div>
        <div className="mt-1 flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-2xl font-semibold tracking-tight capitalize">{titleize(butler)}</h1>
          {description ? (
            <span className="min-w-0 truncate text-sm font-normal text-muted-foreground">
              <span aria-hidden="true">· </span>
              {description}
            </span>
          ) : null}
        </div>
      </div>

      {actions ? (
        <div className="col-span-2 flex items-center justify-start md:col-span-1 md:justify-end">
          {actions}
        </div>
      ) : null}
    </div>
  )
}
