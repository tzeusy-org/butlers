// ---------------------------------------------------------------------------
// StateDot — entity state indicator dot primitive (bu-ec2wb)
//
// Renders a 6px coloured circle. Used sparingly to signal entity state
// (unidentified / duplicate-candidate / stale / healthy).
//
// Brief §2: "6px coloured circle. Minimal use; build as primitive."
// Amendment 9: Reuses existing --red, --amber, --green, --state-unidentified
// tokens only. No new tokens.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Entity states that drive dot color.
 *
 * - "unidentified"         → orange (--state-unidentified)
 * - "duplicate-candidate"  → amber  (--amber)
 * - "stale"                → red    (--red)
 * - "healthy"              → green  (--green)
 * - "archived"             → muted  (--muted-foreground)
 */
export type EntityState = "unidentified" | "duplicate-candidate" | "stale" | "healthy" | "archived"

export interface StateDotProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Entity curation state. Drives the dot color. */
  state: EntityState
  /**
   * Diameter in pixels. Defaults to 6 (per Brief §2 spec).
   * Use sparingly — this is a compact primitive.
   */
  size?: number
}

/** Maps each state to its CSS custom-property color. */
const STATE_COLORS: Record<EntityState, string> = {
  unidentified: "var(--state-unidentified)",
  "duplicate-candidate": "var(--amber)",
  stale: "var(--red)",
  healthy: "var(--green)",
  archived: "var(--muted-foreground)",
}

/** Human-readable label for each state (used as aria-label fallback). */
const STATE_LABELS: Record<EntityState, string> = {
  unidentified: "Unidentified",
  "duplicate-candidate": "Duplicate candidate",
  stale: "Stale",
  healthy: "Healthy",
  archived: "Archived",
}

/**
 * 6px solid-fill circle indicating entity curation state.
 *
 * @example
 *   <StateDot state="unidentified" />
 *   <StateDot state="stale" />
 */
export function StateDot({ state, size = 6, className, style, ...props }: StateDotProps) {
  const color = STATE_COLORS[state]
  const label = STATE_LABELS[state]

  return (
    <span
      role="img"
      aria-label={label}
      className={cn("inline-block shrink-0 rounded-full", className)}
      style={{
        width: size,
        height: size,
        backgroundColor: color,
        ...style,
      }}
      {...props}
    />
  )
}
