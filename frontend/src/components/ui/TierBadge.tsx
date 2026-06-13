// ---------------------------------------------------------------------------
// TierBadge — Dunbar tier badge primitive (bu-ec2wb)
//
// Renders a compact tier indicator: mono 9px uppercase label + 6px coloured
// dot on the left, colored by Dunbar tier via --tier-1..6 tokens.
//
// Brief §2: "Extract inline style into reusable TierBadge component.
//            Mono 9px uppercase + 6px coloured dot."
// Amendment 9: Reuses existing --tier-1..6 tokens only. No new tokens.
//
// Source: extracted from the former EntitiesPage dunbarTierBadgeStyle() helper.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * Dunbar tier size values. Maps 1:1 to the canonical tier ramp:
 *   5   → tier-1 (Support Clique  — innermost, deep red)
 *   15  → tier-2 (Sympathy Group  — orange-red)
 *   50  → tier-3 (Good Friends    — amber-brown)
 *   150 → tier-4 (Meaningful      — green)
 *   500 → tier-5 (Acquaintances   — blue)
 *   1500 / other → tier-6 (Recognizable — gray)
 */
export type DunbarTier = 5 | 15 | 50 | 150 | 500 | 1500

export interface TierBadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  /**
   * Dunbar tier value. Use the canonical tier sizes (5, 15, 50, 150, 500, 1500).
   * Any other number falls back to tier-6 (gray, outermost).
   */
  tier: number
  /** Optional className forwarded to the root element. */
  className?: string
}

/**
 * Map a raw Dunbar tier number to its CSS custom-property color.
 * Matches the logic from the former EntitiesPage dunbarTierBadgeStyle() helper.
 */
export function tierColor(tier: number): string {
  switch (tier) {
    case 5:
      return "var(--tier-1)"
    case 15:
      return "var(--tier-2)"
    case 50:
      return "var(--tier-3)"
    case 150:
      return "var(--tier-4)"
    case 500:
      return "var(--tier-5)"
    default:
      return "var(--tier-6)"
  }
}

/** Human-readable label for a Dunbar tier size. */
export function tierLabel(tier: number): string {
  switch (tier) {
    case 5:
      return "S"
    case 15:
      return "A"
    case 50:
      return "B"
    case 150:
      return "C"
    case 500:
      return "D"
    default:
      return "F"
  }
}

/**
 * Compact Dunbar tier badge: 6px colored dot + single-letter tier label.
 * Monospace, 9px, uppercase.
 *
 * @example
 *   <TierBadge tier={5} />    // innermost — deep red dot + "S"
 *   <TierBadge tier={150} />  // mid-tier  — green dot + "C"
 *   <TierBadge tier={1500} /> // outermost — gray dot + "F"
 */
export function TierBadge({ tier, className, ...props }: TierBadgeProps) {
  const color = tierColor(tier)
  const label = tierLabel(tier)

  return (
    <span
      role="img"
      aria-label={`Tier ${label}`}
      className={cn(
        "inline-flex items-center gap-1",
        "font-mono text-[9px] font-medium uppercase leading-none",
        "text-[var(--mfg,oklch(0.708_0_0))]",
        className,
      )}
      {...props}
    >
      {/* 6px coloured dot per Brief §2 */}
      <span
        aria-hidden="true"
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          backgroundColor: color,
          display: "inline-block",
          flexShrink: 0,
        }}
      />
      {label}
    </span>
  )
}
