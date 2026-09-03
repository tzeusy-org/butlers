// ---------------------------------------------------------------------------
// StatusBoardCell — card-like grid cell for the butler status board
// (bu-hb7dh.6)
//
// Renders a single butler tile in the status-board grid. Each cell is a link
// to the butler detail page and includes:
//   - left-edge state rail (colored by eligibility: emerald/amber/red/dim)
//   - top row: ButlerMark + name + activity chip
//   - role tagline (butler description)
//   - KPI quartet: SESS 24H / SPEND / LOAD / LAST
//   - 24h activity stripe pinned at the bottom
//
// Click-to-restore: when activity is 'quarantined' OR eligibility is 'stale',
// the activity chip becomes a <button> that calls onRestore(name).
//
// Activity door (bu-27dxl.8.3): the 24h activity stripe is always its own
// nested <button> that opens the butler's Activity tab
// (/butlers/{name}?tab=activity), independent of the root tile's Overview
// destination. Because the cell always nests at least this one interactive
// control, the outer container always renders as <div role="link"> (never a
// real <a>) to avoid nesting interactive content inside a link (invalid HTML
// per spec).
//
// Doctrine:
//   - NO inline style except inside ActivityStripe (its own typed-primitive exemption).
//   - NO raw oklch in JSX. All colors via Tailwind tokens.
//   - NO em-dash in any visible string.
//   - All timestamps via <Time>; never new Date().toLocaleString().
// ---------------------------------------------------------------------------

import { useNavigate } from "react-router"

import { ButlerMark } from "@/components/ui/ButlerMark"
import { RowLink } from "@/components/ui/RowLink"
import { Skeleton } from "@/components/ui/skeleton"
import { Time, formatRelativeCompact } from "@/components/ui/time"
import { Tip } from "@/components/ui/tip"
import { ActivityStripe } from "@/components/butlers/ActivityStripe"
import type { StatusBoardRow, ActivityVerb, EligibilityState } from "@/hooks/use-butler-status-board"
import { formatCostUsd } from "@/lib/format-cost"

// ---------------------------------------------------------------------------
// Activity chip
// ---------------------------------------------------------------------------

/** Maps activity verb to display label (uppercase, mono, short). */
function activityLabel(activity: ActivityVerb): string {
  switch (activity) {
    case "running":     return "RUNNING"
    case "idle":        return "IDLE"
    case "overdue":     return "OVERDUE"
    case "offline":     return "OFFLINE"
    case "quarantined": return "QUARANTINED"
    case "unknown":     return "UNKNOWN"
  }
}

/** Maps activity verb to chip color classes. */
function activityChipClasses(activity: ActivityVerb): string {
  switch (activity) {
    case "running":
      return "text-[var(--green)]"
    case "idle":
      return "text-muted-foreground"
    case "overdue":
      return "text-[var(--amber-text)]"
    case "offline":
      return "text-destructive"
    case "quarantined":
      return "text-destructive"
    case "unknown":
      return "text-muted-foreground"
  }
}

/**
 * Compact duration label for the cron-expectation tooltip ("silent 3d",
 * "silent 5h"). Deliberately local/minimal rather than a new shared
 * formatter -- the codebase already has several duplicated duration
 * formatters (JARVIS audit finding); this one is a single title-attribute
 * string, not a KPI value, so it stays inline.
 */
function formatSilenceCompact(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  if (days >= 1) return `${days}d`
  const hours = Math.floor(seconds / 3600)
  if (hours >= 1) return `${hours}h`
  const minutes = Math.max(1, Math.floor(seconds / 60))
  return `${minutes}m`
}

/**
 * Cron-expectation tooltip for the activity chip: "silent 3d, expected
 * daily" instead of a flat OVERDUE/IDLE that means the same thing for an
 * hourly and a weekly butler.
 */
function cadenceTooltip(row: Pick<StatusBoardRow, "activity" | "silenceSeconds" | "cadenceLabel">): string | undefined {
  if (row.silenceSeconds == null) return undefined
  const silence = formatSilenceCompact(row.silenceSeconds)
  if (row.activity === "overdue") {
    return `Silent ${silence}, expected ${row.cadenceLabel ?? "regularly"}`
  }
  if (row.activity === "idle" && row.cadenceLabel) {
    return `Silent ${silence}, expected ${row.cadenceLabel}`
  }
  return undefined
}

// ---------------------------------------------------------------------------
// State rail
// ---------------------------------------------------------------------------

/** Color class for the left-edge state rail, keyed off eligibility per spec. */
function eligibilityRailClass(eligibility: EligibilityState): string {
  switch (eligibility) {
    case "active":      return "bg-[var(--green)]"
    case "stale":       return "bg-[var(--amber)]"
    case "quarantined": return "bg-destructive"
    case "unavailable": return "bg-muted-foreground/30"
  }
}

// ---------------------------------------------------------------------------
// KPI cell helper
// ---------------------------------------------------------------------------

function KpiCell({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="font-mono tabular-nums text-xs font-medium">
        {value}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface StatusBoardCellProps {
  row: StatusBoardRow
  /** Called with the butler name when the user clicks the restore chip. */
  onRestore?: (name: string) => void
  /** True while the restore mutation for this specific butler is in flight. */
  isRestorePending?: boolean
  /** True when the board's keyboard cursor is currently on this tile. */
  isCursorActive?: boolean
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Card-like grid tile for a single butler in the status-board grid.
 *
 * The outer container always renders as <div role="link"> (never a real <a>)
 * because the cell always nests the activity-stripe door <button>, plus the
 * restore <button> on restorable (quarantined/stale) rows — nesting either
 * inside interactive content is invalid HTML per spec. Root-tile navigation
 * (Overview) is handled via onClick/onKeyDown on the div; the nested activity
 * button opens the Activity tab instead, stopping propagation so a click on
 * it never also fires the root's navigation.
 *
 * @example
 *   <StatusBoardCell row={row} onRestore={(name) => setEligibility(name, 'active')} />
 */
export function StatusBoardCell({
  row,
  onRestore,
  isRestorePending = false,
  isCursorActive = false,
}: StatusBoardCellProps) {
  const {
    name,
    type,
    description,
    activity,
    eligibility,
    quarantineReason,
    sessions24h,
    costToday,
    loadPct,
    lastRunISO,
    hourlyStripe,
    hourlyTotal,
    hourlyStripeLoading,
    hourlyStripeError,
    heartbeatUnavailable,
  } = row

  const isRestorable = activity === "quarantined" || eligibility === "stale"
  const railClass = eligibilityRailClass(eligibility)
  const markTone = activity === "running" ? "fill" : "neutral"
  // The router is configured with basename=BASE_URL (router-config.tsx), so
  // react-router Link/navigate handle the path prefix automatically.
  const navigate = useNavigate()
  const routePath = `/butlers/${name}`
  const activityTabPath = `${routePath}?tab=activity`

  // Use the same formatRelativeCompact helper that <Time mode="relative-compact">
  // renders so screen-reader users get the same truthful relative label.
  const lastRunLabel = lastRunISO ? formatRelativeCompact(new Date(lastRunISO)) : "unknown"
  const ariaLabel = `${name}, ${heartbeatUnavailable ? "heartbeat unavailable" : activity}, last run ${lastRunLabel}, ${hourlyStripeLoading ? sessions24h : hourlyStripeError ? "unknown" : hourlyTotal} sessions in 24h`

  // Cron-expectation tooltip on the chip -- "silent 3d, expected daily"
  // instead of a flat OVERDUE/IDLE that means the same thing regardless of
  // this butler's own schedule.
  const chipTitle = !heartbeatUnavailable ? cadenceTooltip(row) : undefined

  const containerClass = [
    "group relative flex flex-col",
    "border-r border-b border-border/60",
    "p-5 min-h-56",
    "transition-colors duration-[120ms] ease-in-out",
    "hover:bg-foreground/[0.025] dark:hover:bg-foreground/[0.025]",
    "no-underline text-inherit cursor-pointer",
    isCursorActive ? "ring-2 ring-foreground ring-inset" : "",
  ].join(" ")

  const innerContent = (
    <>
      {/* Left-edge state rail — colored by eligibility per spec */}
      <div
        className={[
          "absolute left-0 top-0 w-0.5 h-full",
          railClass,
        ].join(" ")}
        aria-hidden="true"
      />

      {/* Top row: ButlerMark + name + activity chip */}
      <div className="flex items-center gap-3">
        <ButlerMark name={name} size={28} tone={markTone} type={type} />

        <span className="text-base font-medium capitalize flex-1 min-w-0 truncate">
          {name}
        </span>

        {/* Activity chip — plain span when not restorable.
            When heartbeat data is unavailable (source down or schema_unreachable),
            the activity verdict is unreliable: show '—' instead of a false 'IDLE'. */}
        {isRestorable && onRestore ? (
          <Tip
            content={
              isRestorePending
                ? undefined
                : eligibility === "quarantined"
                  ? (quarantineReason ?? undefined)
                  : chipTitle
            }
          >
            <button
              type="button"
              disabled={isRestorePending}
              onClick={(e) => {
                e.stopPropagation()
                onRestore(name)
              }}
              className={[
                "font-mono text-[9px] uppercase tracking-wider",
                isRestorePending
                  ? "cursor-not-allowed text-muted-foreground"
                  : "cursor-pointer underline underline-offset-2 decoration-current/50",
                !isRestorePending && (heartbeatUnavailable ? "text-muted-foreground" : eligibility === "stale" ? "text-[var(--amber-text)]" : activityChipClasses(activity)),
              ].filter(Boolean).join(" ")}
            >
              {isRestorePending ? "RESTORING…" : heartbeatUnavailable ? "—" : eligibility === "stale" ? "STALE" : activityLabel(activity)}
            </button>
          </Tip>
        ) : (
          <span
            title={eligibility === "quarantined" ? quarantineReason ?? undefined : chipTitle}
            className={[
              "font-mono text-[9px] uppercase tracking-wider",
              heartbeatUnavailable ? "text-muted-foreground" : eligibility === "stale" ? "text-[var(--amber-text)]" : activityChipClasses(activity),
            ].join(" ")}
          >
            {heartbeatUnavailable ? "—" : eligibility === "stale" ? "STALE" : activityLabel(activity)}
          </span>
        )}
      </div>

      {/* Role tagline */}
      {description ? (
        <p className="mt-1 text-xs text-muted-foreground leading-snug pl-[calc(28px+12px)]">
          {description}
        </p>
      ) : null}

      {/* Quarantine reason -- surfaced at the exact moment of the restore
          decision, not hidden behind the chip's hover-only title. */}
      {eligibility === "quarantined" && quarantineReason ? (
        <p className="mt-1 text-xs text-destructive leading-snug pl-[calc(28px+12px)]">
          {quarantineReason}
        </p>
      ) : null}

      {/* KPI quartet */}
      <div className="grid grid-cols-4 gap-2 border-t border-border/40 pt-3 mt-3">
        <KpiCell
          label="SESS 24H"
          value={
            hourlyStripeLoading ? (
              <Skeleton className="h-3 w-8 mt-0.5" />
            ) : hourlyStripeError ? (
              "—"
            ) : (
              hourlyTotal
            )
          }
        />
        <KpiCell label="SPEND" value={costToday !== null ? formatCostUsd(costToday) : "—"} />
        <KpiCell label="LOAD" value={loadPct != null ? `${loadPct}%` : "—"} />
        <KpiCell
          label="LAST"
          value={
            lastRunISO ? (
              <Time mode="relative-compact" value={lastRunISO} />
            ) : (
              "—"
            )
          }
        />
      </div>

      {/* 24h activity stripe — pinned bottom, its own nested door to the
          butler's Activity tab (bu-27dxl.8.3). A sparse/loading/errored
          stripe stays reachable (no completeness claim implied by the click
          target itself) -- only the visible content differs. This is a real
          <button>, not nested inside the outer <a>/<Link>, per the RowLink
          "no anchor-in-anchor" contract: the outer container below always
          renders as the accessible div[role=link] fallback so this control
          is valid HTML. stopPropagation keeps a click here from also
          triggering the outer tile's Overview navigation. The right-side
          caption swaps from "past 24 h" to the "open →" hover affordance so
          the click target hint never overlaps the stripe bars below. */}
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          navigate(activityTabPath)
        }}
        aria-label={`Open ${name} activity`}
        className="mt-auto block w-full appearance-none border-0 bg-transparent p-0 pt-4 text-left cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset rounded-sm"
      >
        <div className="flex items-center justify-between mb-1">
          <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
            24H ACTIVITY
          </span>
          <span className="relative inline-flex items-center font-mono text-[9px] text-muted-foreground">
            <span className="transition-opacity duration-[120ms] ease-in-out group-hover:opacity-0">
              past 24 h
            </span>
            <span
              aria-hidden="true"
              className="absolute right-0 top-0 whitespace-nowrap opacity-0 group-hover:opacity-85 transition-opacity duration-[120ms] ease-in-out"
            >
              open →
            </span>
          </span>
        </div>
        {hourlyStripeLoading ? (
          <Skeleton className="h-[22px] w-full" />
        ) : hourlyStripeError ? (
          <div
            className="h-[22px] flex items-center"
            aria-label="Activity data unavailable"
            role="img"
          >
            <span className="font-mono text-[9px] text-muted-foreground uppercase tracking-wider">
              data unavailable
            </span>
          </div>
        ) : (
          <ActivityStripe counts={hourlyStripe} />
        )}
      </button>
    </>
  )

  // bu-86c4c.16: RowLink supplies the shared navigating-row contract (real
  // <Link> normally; accessible div[role=link] + Enter/Space fallback when a
  // nested interactive control is present — see the primitive's own docs for
  // why the fallback exists). Every cell now nests the activity-stripe door
  // (bu-27dxl.8.3, in addition to the restore chip on restorable rows), so
  // the root always renders the div[role=link] fallback: nesting either
  // nested control inside a real <a> would be invalid HTML
  // (anchor-in-anchor / interactive-in-anchor). Root Enter/Space still
  // activates Overview navigation via onActivate below -- unaffected by the
  // nested button, which stops propagation on its own click.
  return (
    <RowLink
      to={routePath}
      hasNestedInteractive
      onActivate={() => navigate(routePath)}
      aria-label={ariaLabel}
      className={containerClass}
      data-butler-name={name}
      data-board-cursor={isCursorActive || undefined}
    >
      {innerContent}
    </RowLink>
  )
}
