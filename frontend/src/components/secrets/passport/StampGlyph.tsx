// ---------------------------------------------------------------------------
// StampGlyph — 1-char mono audit action glyph (bu-qo3sf)
//
// Each audit action has a canonical 1-char mono shape. Used as the left-gutter
// mark in StampRow.
//
// butler-secrets §Evidence-Over-Value Affordance Contract §6:
//   "1-char mono glyph + date/time + action + actor + serif note"
//
// Glyph mapping (from tasks.md B2):
//   ✓ verified   → ok      (green)
//   ↻ rotated    → neutral (dim)
//   ✕ failed     → error   (red)
//   ⊘ revoked    → error   (red)
//   ⊕ connected  → ok      (green)
//   ! warned     → warning (amber)
//   ⤳ overrode   → warning (amber)
//   ▷ attempted  → neutral (dim)
//   ⊙ set        → neutral (dim)
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

/** Audit actions from the spec. */
export type AuditAction =
  | "verified"
  | "rotated"
  | "failed"
  | "revoked"
  | "connected"
  | "warned"
  | "overrode"
  | "attempted"
  | "set"

export interface StampGlyphProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Audit action to render. */
  action: AuditAction
}

interface GlyphSpec {
  char: string
  color: string
  label: string
}

const GLYPH_MAP: Record<AuditAction, GlyphSpec> = {
  verified:  { char: "✓", color: "var(--green)",                  label: "verified"  },
  rotated:   { char: "↻", color: "var(--dim,oklch(0.55_0_0))",   label: "rotated"   },
  failed:    { char: "✕", color: "var(--red)",                    label: "failed"    },
  revoked:   { char: "⊘", color: "var(--red)",                    label: "revoked"   },
  connected: { char: "⊕", color: "var(--green)",                  label: "connected" },
  warned:    { char: "!", color: "var(--amber)",                   label: "warned"    },
  overrode:  { char: "⤳", color: "var(--amber)",                   label: "overrode"  },
  attempted: { char: "▷", color: "var(--dim,oklch(0.55_0_0))",   label: "attempted" },
  set:       { char: "⊙", color: "var(--dim,oklch(0.55_0_0))",   label: "set"       },
}

/**
 * 1-char mono glyph for an audit action.
 *
 * Renders at 11px mono with action-appropriate colour. Carries an
 * aria-label for screen readers.
 *
 * @example
 *   <StampGlyph action="verified" />  // ✓ in green
 *   <StampGlyph action="failed" />    // ✕ in red
 */
export function StampGlyph({ action, className, style, ...props }: StampGlyphProps) {
  const { char, color, label } = GLYPH_MAP[action]

  return (
    <span
      role="img"
      aria-label={label}
      className={cn(
        "font-mono text-[11px] font-normal leading-none tabular-nums",
        "shrink-0 inline-block w-4 text-center",
        className,
      )}
      style={{ color, ...style }}
      {...props}
    >
      {char}
    </span>
  )
}
