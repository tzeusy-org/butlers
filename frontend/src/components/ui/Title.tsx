// ---------------------------------------------------------------------------
// Title — page title primitive (bu-rixan)
//
// Inter Tight, 24px, weight 500, -0.015em tracking, 1.2 leading.
// Used for page titles, section headings, and editorial sub-headings.
//
// Dispatch Design Language §2b: "Title: sans 24px, 500, -0.015em, 1.2."
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface TitleProps extends React.HTMLAttributes<HTMLElement> {
  /**
   * Rendered element. Defaults to "h2" for section-heading use.
   * Use "h1" for the primary page title, "span" for inline title text.
   */
  as?: "h1" | "h2" | "h3" | "span" | "div" | "p"
  children: React.ReactNode
}

/**
 * Page or section title.
 *
 * Inter Tight, 24px, weight 500, -0.015em tracking, 1.2 leading.
 *
 * @example
 *   <Title as="h1">Secrets</Title>
 *   <Title>Google OAuth</Title>
 */
export function Title({ as: Tag = "h2", children, className, ...props }: TitleProps) {
  return (
    <Tag
      className={cn(
        // Font family — Inter Tight via --font-sans
        "font-sans font-medium",
        // Size and leading per spec (24px per §2b — not text-2xl which is 1.728rem in this scale)
        "text-[24px] leading-[1.2]",
        // Tracking — tight per spec (-0.015em)
        "tracking-[-0.015em]",
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
