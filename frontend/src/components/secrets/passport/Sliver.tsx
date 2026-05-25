// ---------------------------------------------------------------------------
// Sliver — 2px vertical rail (bu-qo3sf)
//
// The left-edge state indicator for spine rows on the /secrets passport page.
// Coloured only when state demands (non-ok credentials); neutral (transparent)
// for ok credentials so that a healthy day renders zero state colour.
//
// Dispatch §4e + butler-secrets §Evidence-Over-Value Affordance Contract:
//   "Each credential row SHALL surface a 2px left-edge sliver (coloured only
//   when state demands)."
//
// Credential states that carry colour:
//   expired        → --red
//   revoked        → --red
//   failed         → --red
//   scope_mismatch → --amber
//   expiring_soon  → --amber
//   never_set      → --dim
//   ok             → transparent (no state colour)
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

/** Credential states for the passport Sliver. */
export type CredentialState =
  | "ok"
  | "expired"
  | "revoked"
  | "failed"
  | "scope_mismatch"
  | "expiring_soon"
  | "never_set"

export interface SliverProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Credential state that drives the sliver colour. */
  state: CredentialState
}

/** Maps credential state to a CSS color token (or transparent for ok). */
const SLIVER_COLORS: Record<CredentialState, string | undefined> = {
  ok: undefined,
  expired: "var(--red)",
  revoked: "var(--red)",
  failed: "var(--red)",
  scope_mismatch: "var(--amber)",
  expiring_soon: "var(--amber)",
  never_set: "var(--dim,oklch(0.55_0_0))",
}

/**
 * 2px vertical rail for spine rows.
 *
 * Coloured only when state demands. For `state="ok"`, renders as a
 * transparent 2px strip so the column gutter is preserved.
 *
 * @example
 *   <Sliver state="expired" />  // red rail
 *   <Sliver state="ok" />       // transparent — no colour on a calm day
 */
export function Sliver({ state, className, style, ...props }: SliverProps) {
  const color = SLIVER_COLORS[state]

  return (
    <span
      aria-hidden="true"
      className={cn("inline-block shrink-0 self-stretch", className)}
      style={{
        width: 2,
        backgroundColor: color ?? "transparent",
        borderRadius: 1,
        ...style,
      }}
      {...props}
    />
  )
}
