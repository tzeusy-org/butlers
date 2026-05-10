// ---------------------------------------------------------------------------
// StatusBoardCell — card-like grid cell for the butler status board
// (bu-hb7dh.6)
//
// Renders a single butler tile in the status-board grid. Each cell is a link
// to the butler detail page and includes:
//   - left-edge state rail (only for 'red' and 'amber' tones)
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
import { Time } from "@/components/ui/time"
import { ActivityStripe } from "@/components/butlers/ActivityStripe"
import type { StatusBoardRow, ActivityVerb, CellTone } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Activity chip
// ---------------------------------------------------------------------------

/** Maps activity verb to display label (uppercase, mono, short). */
function activityLabel(activity: ActivityVerb): string {
  switch (activity) {
    case "running":     return "RUNNING"
    case "idle":        return "IDLE"
    case "paused":      return "PAUSED"
    case "awaiting":    return "AWAITING"
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
    case "paused":
      return "text-destructive"
    case "awaiting":
      return "text-amber-500"
    case "quarantined":
      return "text-destructive"
  }
}

// ---------------------------------------------------------------------------
// State rail
// ---------------------------------------------------------------------------

/** Color class for the left-edge state rail, or null to suppress the rail. */
function railColorClass(tone: CellTone): string | null {
  switch (tone) {
    case "red":   return "bg-destructive"
    case "amber": return "bg-amber-500"
    default:      return null
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
export function StatusBoardCell({ row, onRestore }: StatusBoardCellProps) {
  const {
    name,
    description,
    activity,
    cellTone,
    eligibility,
    sessions24h,
    costToday,
    loadPct,
    lastRunISO,
    hourlyStripe,
  } = row

  const isRestorable = activity === "quarantined" || eligibility === "stale"
  const railClass = railColorClass(cellTone)
  const markTone = activity === "running" ? "fill" : "neutral"
  const href = `/butlers/${name}`

  const ariaLabel = `${name}, ${activity}, last run ${lastRunISO ? "recently" : "unknown"}, ${sessions24h} sessions in 24h`

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
      {/* Left-edge state rail — only for red and amber tones */}
      {railClass ? (
        <div
          className={[
            "absolute left-0 top-0 w-0.5 h-full",
            railClass,
          ].join(" ")}
          aria-hidden="true"
        />
      ) : null}

      {/* Top row: ButlerMark + name + activity chip */}
      <div className="flex items-center gap-3">
        <ButlerMark name={name} size={28} tone={markTone} />

        <span className="text-base font-medium capitalize flex-1 min-w-0 truncate">
          {name}
        </span>

        {/* Activity chip — plain span when not restorable */}
        {isRestorable && onRestore ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              onRestore(name)
            }}
            className={[
              "font-mono text-[9px] uppercase tracking-wider cursor-pointer",
              "underline underline-offset-2 decoration-current/50",
              activityChipClasses(activity),
            ].join(" ")}
          >
            {activityLabel(activity)}
          </button>
        ) : (
          <span
            className={[
              "font-mono text-[9px] uppercase tracking-wider",
              activityChipClasses(activity),
            ].join(" ")}
          >
            {activityLabel(activity)}
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
        <KpiCell label="SESS 24H" value={sessions24h} />
        <KpiCell label="SPEND" value={`$${costToday.toFixed(2)}`} />
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

      {/* 24h activity stripe — pinned bottom */}
      <div className="mt-auto pt-4">
        <div className="flex items-center justify-between mb-1">
          <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
            24H ACTIVITY
          </span>
          <span className="font-mono text-[9px] text-muted-foreground">
            past 24 h
          </span>
        </div>
        <ActivityStripe counts={hourlyStripe} />
      </div>

      {/* Hover affordance: "open →" bottom-right */}
      <div className="absolute bottom-4 right-5 opacity-0 group-hover:opacity-85 transition-opacity duration-[120ms] ease-in-out">
        <span className="font-mono text-[11px] text-muted-foreground">
          open →
        </span>
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
