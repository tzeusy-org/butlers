// ---------------------------------------------------------------------------
// Voice — serif narrative text primitive (bu-rixan)
//
// Source Serif 4, 16px, weight 400, normal tracking, 1.6 leading.
// Used exclusively where the system speaks in sentences: Overview briefings,
// empty states, "Why this shape" elaborations.
//
// Dispatch Design Language §2a/§2b: "Voice: Source Serif 4, 16px, 400."
// §4g: "The briefing surface — Voice. Reserve it for places the system is
// literally speaking in sentences."
//
// Two sub-variants per §4g:
//   "roman"  — roman weight, used for briefings (default)
//   "italic" — italic style, used for empty-state lines
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface VoiceProps extends React.HTMLAttributes<HTMLElement> {
  /**
   * Rendered element. Defaults to "p" for paragraph-level use.
   * Use "span" for inline voice text within a larger container.
   */
  as?: "p" | "span" | "div"
  /**
   * Style variant:
   *   "roman"  — normal style, used for briefings (default)
   *   "italic" — italic style, used for empty states
   *
   * "Voice is always serif italic for empty states, serif roman for briefings."
   * — Dispatch Design Language §4g
   */
  variant?: "roman" | "italic"
  children: React.ReactNode
}

/**
 * Serif narrative voice text.
 *
 * Source Serif 4, 16px, 400 weight, 1.6 leading.
 * Use only where the system speaks in sentences. Never decorative.
 *
 * @example
 *   <Voice>Inventory of every credential the system holds.</Voice>
 *   <Voice variant="italic">Nothing waiting.</Voice>
 */
export function Voice({ as: Tag = "p", variant = "roman", children, className, ...props }: VoiceProps) {
  return (
    <Tag
      className={cn(
        // Font family — Source Serif 4
        "font-serif text-base font-normal",
        // Leading per spec
        "leading-[1.6]",
        // Tracking — normal per spec
        "tracking-normal",
        // Color — primary foreground
        "text-[var(--fg,oklch(0.985_0_0))]",
        // Variant
        variant === "italic" && "italic",
        className,
      )}
      {...props}
    >
      {children}
    </Tag>
  )
}
