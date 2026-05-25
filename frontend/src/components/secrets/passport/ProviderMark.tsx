// ---------------------------------------------------------------------------
// ProviderMark — mono 22px square letter-mark for providers (bu-qo3sf)
//
// Hairline border, no colour. Used in WhatBreaks rows and credential headings
// to identify the provider.
//
// Distinct from ButlerMark: ButlerMark uses butler category hues; ProviderMark
// is neutral (hairline border only) because providers are not butlers and have
// no hue assignment.
//
// Dispatch §4f (for context): "The hue only ever shows on the butler's
// letter-mark." — ProviderMark therefore has NO coloured background.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface ProviderMarkProps extends React.HTMLAttributes<HTMLSpanElement> {
  /**
   * Provider slug. The first character (uppercased) is used as the initial.
   * Example: "google" → "G", "telegram" → "T"
   */
  provider: string
}

/**
 * Neutral 22px square letter-mark for providers.
 *
 * Hairline border, no background colour. The initial is the first character
 * of the provider slug, uppercased.
 *
 * @example
 *   <ProviderMark provider="google" />     // renders "G"
 *   <ProviderMark provider="telegram" />   // renders "T"
 */
export function ProviderMark({ provider, className, ...props }: ProviderMarkProps) {
  const initial = provider.charAt(0).toUpperCase()

  return (
    <span
      aria-label={provider}
      className={cn(
        "inline-flex items-center justify-center shrink-0",
        "font-mono font-semibold leading-none",
        "border border-[var(--border,oklch(1_0_0/0.10))]",
        "rounded-[3px]",
        className,
      )}
      style={{
        width: 22,
        height: 22,
        fontSize: "60%",
        color: "var(--mfg,oklch(0.708_0_0))",
        backgroundColor: "transparent",
      }}
      {...props}
    >
      {initial}
    </span>
  )
}
