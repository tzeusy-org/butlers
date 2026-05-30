// ---------------------------------------------------------------------------
// Mono — inline monospace primitive (bu-rixan)
//
// 11px / JetBrains Mono / tabular-nums. Used for fingerprints, IDs, deltas,
// KPI numbers, mono timestamps, badges, file paths.
//
// Dispatch Design Language §2b: "Mono inline: mono 11px, normal tracking, 1.4
// leading." §2c: "Every numeric value gets font-variant-numeric: tabular-nums.
// Always."
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface MonoProps extends React.HTMLAttributes<HTMLElement> {
  /**
   * HTML element to render. Defaults to "span" for inline use.
   * Use "code" when the content is a code fragment or credential key.
   */
  as?: "span" | "code" | "div" | "p"
  /** Muted color variant. Defaults to false (full-foreground). */
  muted?: boolean
  children: React.ReactNode
}

/**
 * Inline monospace text.
 *
 * 11px JetBrains Mono, tabular numerals, 1.4 leading.
 * Use for fingerprints, IDs, timestamps, file paths, deltas.
 *
 * @example
 *   <Mono>sha256:7a3f…</Mono>
 *   <Mono muted>14:21</Mono>
 *   <Mono as="code">BUTLER_TELEGRAM_TOKEN</Mono>
 */
export function Mono({ as: Tag = "span", muted = false, children, className, ...props }: MonoProps) {
  return (
    <Tag
      className={cn(
        // Font family and size
        "font-mono text-[11px] font-normal",
        // Normal tracking, 1.4 leading per spec
        "tracking-normal leading-[1.4]",
        // Tabular numerals — non-negotiable per §2c
        "tabular-nums",
        // Color
        muted
          ? "text-[var(--mfg,oklch(0.708_0_0))]"
          : "text-[var(--fg,oklch(0.985_0_0))]",
        className,
      )}
      {...props}
    >
      {children}
    </Tag>
  )
}
