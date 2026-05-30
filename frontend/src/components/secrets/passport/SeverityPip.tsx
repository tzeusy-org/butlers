// ---------------------------------------------------------------------------
// SeverityPip — 1-char mono severity indicator (bu-qo3sf)
//
// Used in WhatBreaks rows to signal the severity of a feature dependency.
//
// butler-secrets §Evidence-Over-Value Affordance Contract §4:
//   "severity pip per row"
//
// Severity levels from the provider_feature_catalogue schema:
//   high   → ↑ — red
//   medium → · — amber
//   low    → ↓ — dim
//
// One character per severity to maintain visual compactness. Colour is the
// only semantic signal; no words like "HIGH" are rendered (per Dispatch §5:
// "one affordance per signal").
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

/** Severity levels from the breaks-catalogue. */
export type Severity = "high" | "medium" | "low"

export interface SeverityPipProps extends React.HTMLAttributes<HTMLSpanElement> {
  severity: Severity
}

interface PipSpec {
  char: string
  color: string
  label: string
}

const PIP_MAP: Record<Severity, PipSpec> = {
  high:   { char: "↑", color: "var(--red)",                   label: "high severity"   },
  medium: { char: "·", color: "var(--amber)",                  label: "medium severity" },
  low:    { char: "↓", color: "var(--dim,oklch(0.55_0_0))",  label: "low severity"    },
}

/**
 * 1-char mono severity pip.
 *
 * @example
 *   <SeverityPip severity="high" />    // ↑ in red
 *   <SeverityPip severity="medium" />  // · in amber
 *   <SeverityPip severity="low" />     // ↓ in dim
 */
export function SeverityPip({ severity, className, style, ...props }: SeverityPipProps) {
  const { char, color, label } = PIP_MAP[severity]

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
