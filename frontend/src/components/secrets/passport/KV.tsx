// ---------------------------------------------------------------------------
// KV — generic label + mono value pair (bu-qo3sf)
//
// A key-value row for the dense KV band on each credential page.
//
// butler-secrets §Evidence-Over-Value Affordance Contract §2:
//   "Dense KV band — issued / expires / last verified / last used / source /
//   target / category, all in mono with tabular numerals."
//
// Distinct from the butler-detail atoms.tsx KV: this variant uses the Dispatch
// Mono primitive for values and is tuned for credential page density rather
// than the butler detail panel grid.
// ---------------------------------------------------------------------------

import * as React from "react"

import { Mono } from "@/components/ui/Mono"
import { cn } from "@/lib/utils"

export interface KVProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Label text. Rendered in --mfg (muted) to let the value stand out. */
  label: string
  /** Value to display. Rendered in mono --fg. */
  value: React.ReactNode
  /**
   * When true the value renders in muted --mfg rather than full --fg.
   * Use for null/absent values such as "—".
   */
  valueMuted?: boolean
}

/**
 * Label + mono value pair, separated by a hairline border-bottom.
 *
 * @example
 *   <KV label="issued" value="14 Jan 2026" />
 *   <KV label="expires" value="—" valueMuted />
 */
export function KV({ label, value, valueMuted = false, className, ...props }: KVProps) {
  return (
    <div
      className={cn(
        "flex items-baseline gap-4 py-1.5",
        "border-b border-[var(--border-soft,oklch(1_0_0/0.06))] last:border-b-0",
        className,
      )}
      {...props}
    >
      <span
        className="shrink-0 font-mono text-[10px] uppercase tracking-[0.14em] leading-none w-28 truncate"
        style={{ color: "var(--mfg,oklch(0.708_0_0))" }}
      >
        {label}
      </span>
      <Mono muted={valueMuted} className="flex-1 min-w-0 break-all">
        {value}
      </Mono>
    </div>
  )
}
