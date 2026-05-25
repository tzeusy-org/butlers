// ---------------------------------------------------------------------------
// Display — large display headline primitive (bu-rixan)
//
// Inter Tight, 44px, weight 500, -0.025em tracking, 1.08 leading.
// Used for hero headlines, page-level KPI numbers, and editorial display text.
//
// Dispatch Design Language §2b: "Display: sans 44px, 500, -0.025em, 1.08."
// §2b note: "Display weight is 500, never 700. Bold display is loud. Tight
// tracking does the work that weight would do."
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface DisplayProps extends React.HTMLAttributes<HTMLElement> {
  /**
   * Rendered element. Defaults to "h1" for semantic hierarchy.
   * Use "span" for non-heading display text (e.g. KPI numbers).
   */
  as?: "h1" | "h2" | "span" | "div" | "p"
  children: React.ReactNode
}

/**
 * Large editorial display headline.
 *
 * Inter Tight, 44px, weight 500, -0.025em tracking, 1.08 leading.
 * Weight is intentionally 500 — never 700. Tight tracking does the
 * work that bold weight would do.
 *
 * @example
 *   <Display>Secrets</Display>
 *   <Display as="span">42</Display>
 */
export function Display({ as: Tag = "h1", children, className, ...props }: DisplayProps) {
  return (
    <Tag
      className={cn(
        // Font family — Inter Tight via --font-sans
        "font-sans font-medium",
        // Size and leading per spec
        "text-[44px] leading-[1.08]",
        // Tracking — tight per spec (-0.025em)
        "tracking-[-0.025em]",
        // Color — primary foreground
        "text-[var(--fg,oklch(0.985_0_0))]",
        className,
      )}
      {...props}
    >
      {children}
    </Tag>
  )
}
