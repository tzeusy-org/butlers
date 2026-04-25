// ---------------------------------------------------------------------------
// SourceStateBadgeStrip — bu-ig72b.22
//
// Horizontal strip of category badges driven by useChroniclesSourceState().
// Renders one badge per source adapter, styled by compatibility and active state.
//
// Badge states per dashboard-chronicles spec 'Disabled Lane Affordances':
//   supported + active      → enabled badge (category colour from LANE_TAXONOMY)
//   supported + inactive    → yellow banner with inactive_reason + last_error tooltip
//   planned                 → disabled badge, tooltip "Adapter planned; not yet implemented"
//   deferred                → hidden by default; revealed by toggle
//   not_time_bearing        → never shown
//
// The deferred-lanes toggle persists in localStorage under
// "chronicles.showDeferredLanes".
// ---------------------------------------------------------------------------

import { useState } from "react"

import type { ChroniclerSourceStateRow } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useChroniclesSourceState } from "@/hooks/use-chronicles"
import { readBooleanSetting, writeBooleanSetting } from "@/lib/local-settings"
import { LANE_TAXONOMY } from "./lane-taxonomy"
import type { Category } from "./lane-taxonomy"
import { getBadgeState, buildInactiveTooltip } from "./source-state-utils"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SHOW_DEFERRED_KEY = "chronicles.showDeferredLanes"

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/** Resolve a LANE_TAXONOMY entry for the source if the source_name matches a known category. */
function getLaneConfig(sourceName: string) {
  return LANE_TAXONOMY[sourceName as Category] ?? null
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface SourceBadgeProps {
  row: ChroniclerSourceStateRow
}

function ActiveBadge({ row }: SourceBadgeProps) {
  const lane = getLaneConfig(row.source_name)
  const label = lane?.label ?? row.source_name
  const Icon = lane?.icon ?? null

  return (
    <Badge
      className={lane ? `${lane.colour} text-white border-transparent` : undefined}
      aria-label={`${label}: active`}
    >
      {Icon && <Icon aria-hidden />}
      {label}
    </Badge>
  )
}

function InactiveBadge({ row }: SourceBadgeProps) {
  const lane = getLaneConfig(row.source_name)
  const label = lane?.label ?? row.source_name
  const Icon = lane?.icon ?? null
  const tooltipText = buildInactiveTooltip(row)

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          className="bg-yellow-500 text-white border-transparent cursor-help"
          aria-label={`${label}: no recent data`}
          aria-describedby={undefined}
        >
          {Icon && <Icon aria-hidden />}
          {label}
          <span className="sr-only"> — no recent data</span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs whitespace-pre-line text-left">
        {tooltipText}
      </TooltipContent>
    </Tooltip>
  )
}

function PlannedBadge({ row }: SourceBadgeProps) {
  const lane = getLaneConfig(row.source_name)
  const label = lane?.label ?? row.source_name
  const Icon = lane?.icon ?? null

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant="outline"
          className="opacity-50 cursor-help"
          aria-label={`${label}: planned`}
          aria-disabled="true"
        >
          {Icon && <Icon aria-hidden />}
          {label}
        </Badge>
      </TooltipTrigger>
      <TooltipContent>Adapter planned; not yet implemented</TooltipContent>
    </Tooltip>
  )
}

function DeferredBadge({ row }: SourceBadgeProps) {
  const lane = getLaneConfig(row.source_name)
  const label = lane?.label ?? row.source_name
  const Icon = lane?.icon ?? null

  return (
    <Badge
      variant="secondary"
      className="opacity-60"
      aria-label={`${label}: deferred`}
    >
      {Icon && <Icon aria-hidden />}
      {label}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SourceStateBadgeStrip() {
  const { data } = useChroniclesSourceState()

  const [showDeferred, setShowDeferred] = useState<boolean>(() =>
    readBooleanSetting(SHOW_DEFERRED_KEY, false),
  )

  function toggleDeferred() {
    const next = !showDeferred
    setShowDeferred(next)
    writeBooleanSetting(SHOW_DEFERRED_KEY, next)
  }

  // Tolerate cold boot — data may be undefined before first fetch resolves.
  const rows: ChroniclerSourceStateRow[] = data?.data ?? []

  const activeBadges: ChroniclerSourceStateRow[] = []
  const inactiveBadges: ChroniclerSourceStateRow[] = []
  const plannedBadges: ChroniclerSourceStateRow[] = []
  const deferredBadges: ChroniclerSourceStateRow[] = []

  for (const row of rows) {
    const state = getBadgeState(row)
    if (state === "active") activeBadges.push(row)
    else if (state === "inactive") inactiveBadges.push(row)
    else if (state === "planned") plannedBadges.push(row)
    else if (state === "deferred") deferredBadges.push(row)
    // null (not_time_bearing) → skipped
  }

  const hasDeferredLanes = deferredBadges.length > 0
  const hasAnyBadge =
    activeBadges.length > 0 ||
    inactiveBadges.length > 0 ||
    plannedBadges.length > 0 ||
    hasDeferredLanes

  if (!hasAnyBadge) return null

  return (
    <TooltipProvider>
      <div
        className="flex flex-wrap items-center gap-2"
        aria-label="Source adapter state"
        data-testid="source-state-badge-strip"
      >
        {activeBadges.map((row) => (
          <ActiveBadge key={row.source_name} row={row} />
        ))}

        {inactiveBadges.map((row) => (
          <InactiveBadge key={row.source_name} row={row} />
        ))}

        {plannedBadges.map((row) => (
          <PlannedBadge key={row.source_name} row={row} />
        ))}

        {showDeferred &&
          deferredBadges.map((row) => (
            <DeferredBadge key={row.source_name} row={row} />
          ))}

        {hasDeferredLanes && (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-xs text-muted-foreground"
            onClick={toggleDeferred}
            aria-pressed={showDeferred}
            aria-label={showDeferred ? "Hide deferred lanes" : "Show deferred lanes"}
          >
            {showDeferred ? "Hide deferred" : `+${deferredBadges.length} deferred`}
          </Button>
        )}
      </div>
    </TooltipProvider>
  )
}
