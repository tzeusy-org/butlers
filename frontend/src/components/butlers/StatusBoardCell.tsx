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
// the activity chip becomes a <button> that calls onRestore(name). The outer
// container switches from <a> to <div role="link"> to avoid nesting interactive
// content inside a link (invalid HTML per spec).
//
// Doctrine:
//   - NO inline style except inside ActivityStripe (its own typed-primitive exemption).
//   - NO raw oklch in JSX. All colors via Tailwind tokens.
//   - NO em-dash in any visible string.
//   - All timestamps via <Time>; never new Date().toLocaleString().
// ---------------------------------------------------------------------------

import { ButlerMark } from "@/components/ui/ButlerMark"
import { Skeleton } from "@/components/ui/skeleton"
import { Time } from "@/components/ui/time"
import { ActivityStripe } from "@/components/butlers/ActivityStripe"
import type { StatusBoardRow, ActivityVerb, EligibilityState } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Activity chip
// ---------------------------------------------------------------------------

/** Maps activity verb to display label (uppercase, mono, short). */
function activityLabel(activity: ActivityVerb): string {
  switch (activity) {
    case "running":     return "RUNNING"
    case "idle":        return "IDLE"
    case "offline":     return "OFFLINE"
    case "quarantined": return "QUARANTINED"
  }
}

/** Maps activity verb to chip color classes. */
function activityChipClasses(activity: ActivityVerb): string {
  switch (activity) {
    case "running":
      return "text-emerald-600 dark:text-emerald-400"
    case "idle":
      return "text-muted-foreground"
    case "offline":
      return "text-destructive"
    case "quarantined":
      return "text-destructive"
  }
}

// ---------------------------------------------------------------------------
// State rail
// ---------------------------------------------------------------------------

/** Color class for the left-edge state rail, keyed off eligibility per spec. */
function eligibilityRailClass(eligibility: EligibilityState): string {
  switch (eligibility) {
    case "active":      return "bg-emerald-500"
    case "stale":       return "bg-amber-500"
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
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * Card-like grid tile for a single butler in the status-board grid.
 *
 * When the cell is restorable (quarantined or stale eligibility) and onRestore
 * is provided, the outer container switches from <a> to <div role="link"> so
 * that the restore <button> is not nested inside interactive content (invalid
 * HTML per spec). Navigation is handled via onClick and onKeyDown on the div.
 *
 * @example
 *   <StatusBoardCell row={row} onRestore={(name) => setEligibility(name, 'active')} />
 */
export function StatusBoardCell({ row, onRestore, isRestorePending = false }: StatusBoardCellProps) {
  const {
    name,
    description,
    activity,
    eligibility,
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
  // Prepend Vite's BASE_URL so the link resolves correctly when the app is
  // mounted under a path prefix (e.g. /butlers-dev/). Without this, a raw
  // /butlers/{name} bypasses the prefix and 404s on the bare /butlers/ path.
  const basePath = (import.meta.env.BASE_URL || "/").replace(/\/+$/, "")
  const href = `${basePath}/butlers/${name}`

  const ariaLabel = `${name}, ${heartbeatUnavailable ? "heartbeat unavailable" : activity}, last run ${lastRunISO ? "recently" : "unknown"}, ${hourlyStripeLoading ? sessions24h : hourlyStripeError ? "unknown" : hourlyTotal} sessions in 24h`

  const containerClass = [
    "group relative flex flex-col",
    "border-r border-b border-border/60",
    "p-5 min-h-56",
    "transition-colors duration-[120ms] ease-in-out",
    "hover:bg-foreground/[0.025] dark:hover:bg-foreground/[0.025]",
    "no-underline text-inherit cursor-pointer",
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
        <ButlerMark name={name} size={28} tone={markTone} />

        <span className="text-base font-medium capitalize flex-1 min-w-0 truncate">
          {name}
        </span>

        {/* Activity chip — plain span when not restorable.
            When heartbeat data is unavailable (source down or schema_unreachable),
            the activity verdict is unreliable: show '—' instead of a false 'IDLE'. */}
        {isRestorable && onRestore ? (
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
              !isRestorePending && (heartbeatUnavailable ? "text-muted-foreground" : eligibility === "stale" ? "text-amber-600 dark:text-amber-400" : activityChipClasses(activity)),
            ].filter(Boolean).join(" ")}
          >
            {isRestorePending ? "RESTORING…" : heartbeatUnavailable ? "—" : eligibility === "stale" ? "STALE" : activityLabel(activity)}
          </button>
        ) : (
          <span
            className={[
              "font-mono text-[9px] uppercase tracking-wider",
              heartbeatUnavailable ? "text-muted-foreground" : eligibility === "stale" ? "text-amber-600 dark:text-amber-400" : activityChipClasses(activity),
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
        <KpiCell label="SPEND" value={costToday !== null ? `$${costToday.toFixed(2)}` : "—"} />
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

      {/* 24h activity stripe — pinned bottom. The right-side caption swaps from
          "past 24 h" to the "open →" hover affordance so the click target hint
          never overlaps the stripe bars below. */}
      <div className="mt-auto pt-4">
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
      </div>
    </>
  )

  // When a restore chip is present, switch to div+role="link" so the <button>
  // is not nested inside an <a> (invalid HTML: interactive content inside
  // interactive content). Navigation is handled imperatively.
  if (isRestorable && onRestore) {
    return (
      <div
        role="link"
        tabIndex={0}
        aria-label={ariaLabel}
        className={containerClass}
        onClick={() => { window.location.href = href }}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") window.location.href = href }}
      >
        {innerContent}
      </div>
    )
  }

  return (
    <a
      href={href}
      aria-label={ariaLabel}
      className={containerClass}
    >
      {innerContent}
    </a>
  )
}
