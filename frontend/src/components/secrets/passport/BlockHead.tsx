// ---------------------------------------------------------------------------
// BlockHead — mono eyebrow section header with optional right caption (bu-qo3sf)
//
// A section divider used to title the credential page blocks: "KV BAND",
// "SCOPES", "WHAT BREAKS", "PROBE RESULT", "AUDIT".
//
// Dispatch §2d: "Eyebrow: 10px / mono / uppercase / 0.14em letter-spacing /
// muted color. Used to title sections in lieu of a heading."
//
// The optional right caption renders the same mono 10px muted typography for
// supplementary context (e.g. "last 10 entries", "granted 5 of 7").
// ---------------------------------------------------------------------------

import * as React from "react"

import { Eyebrow } from "@/components/ui/Eyebrow"
import { cn } from "@/lib/utils"

export interface BlockHeadProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Section label. Rendered as uppercase mono eyebrow. */
  label: string
  /**
   * Optional supplementary text shown on the right in the same mono muted
   * style. Use for counts, timestamps, or brief meta.
   */
  caption?: React.ReactNode
}

/**
 * Mono eyebrow section header.
 *
 * @example
 *   <BlockHead label="Audit" />
 *   <BlockHead label="Audit" caption="last 10 entries" />
 */
export function BlockHead({ label, caption, className, ...props }: BlockHeadProps) {
  return (
    <div
      className={cn(
        "flex items-center justify-between",
        "py-2 border-b border-[var(--border,oklch(1_0_0/0.10))]",
        className,
      )}
      {...props}
    >
      <Eyebrow as="span">{label}</Eyebrow>
      {caption !== undefined && caption !== null && (
        <span
          className="font-mono text-[10px] font-normal uppercase tracking-[0.14em] leading-none"
          style={{ color: "var(--dim,oklch(0.55_0_0))" }}
        >
          {caption}
        </span>
      )}
    </div>
  )
}
