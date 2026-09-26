// ---------------------------------------------------------------------------
// Eyebrow — section label primitive (bu-rixan)
//
// 10px / JetBrains Mono / uppercase / 0.14em letter-spacing / muted color.
// Used to title sections in lieu of a heading. Establishes rhythm without
// shouting.
//
// Dispatch Design Language §2d: "Eyebrow: mono 10px, uppercase, 0.14em
// letter-spacing, muted color. Used to title sections in lieu of a heading."
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface EyebrowProps extends React.HTMLAttributes<HTMLElement> {
  /** Rendered as <span> by default. Pass a heading or block tag as needed. */
  as?: "span" | "div" | "p" | "h2" | "h3"
  children: React.ReactNode
}

/**
 * Section eyebrow label.
 *
 * 10px mono uppercase, 0.14em letter-spacing, muted foreground color.
 * Renders inline by default; pass `as="div"` for block layout.
 *
 * @example
 *   <Eyebrow>Overview · Wed, 7 May 2026 · 14:21</Eyebrow>
 *   <Eyebrow as="div">Credentials</Eyebrow>
 */
export function Eyebrow({ as: Tag = "span", children, className, ...props }: EyebrowProps) {
  return (
    <Tag
      data-slot="eyebrow"
      className={cn(
        // Font family and size — JetBrains Mono at 10px
        "font-mono text-[10px] font-normal",
        // Uppercase with design-language tracking
        "uppercase tracking-[0.14em]",
        // Leading — compact (1.0 per spec)
        "leading-none",
        // Color — muted foreground (--mfg token)
        "text-[var(--mfg)]",
        className,
      )}
      {...props}
    >
      {children}
    </Tag>
  )
}
