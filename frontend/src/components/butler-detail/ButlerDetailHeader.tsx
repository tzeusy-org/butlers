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
import { useButler } from "@/hooks/use-butlers"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"
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

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  if (hours < 24) return remainingMinutes > 0 ? `${hours}h ${remainingMinutes}m` : `${hours}h`
  const days = Math.floor(hours / 24)
  const remainingHours = hours % 24
  return remainingHours > 0 ? `${days}d ${remainingHours}h` : `${days}d`
}

function activityToneClass(activity: string): string {
  switch (activity) {
    case "running":
      return "text-emerald-500"
    case "awaiting":
      return "text-amber-500"
    case "paused":
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

  // Find the active butler's description from the status board rows.
  // Falls back to null when loading, errored, or not found.
  const activeRow = rows.find((r) => r.name === butler) ?? null
  const butlerDetail = butlerResponse?.data ?? null
  const processFacts = butlerDetail?.process_facts ?? null
  const description = activeRow?.description ?? butlerDetail?.description ?? null
  const port = processFacts?.port ?? butlerDetail?.port ?? null
  const uptime =
    processFacts?.registered_duration_seconds != null
      ? formatDuration(processFacts.registered_duration_seconds)
      : null
  const activity = activeRow?.activity ?? "unknown"

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
          <span className="text-muted-foreground">
            port {port ?? "--"} · uptime {uptime ?? "--"}
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
