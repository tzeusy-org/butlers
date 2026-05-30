// ---------------------------------------------------------------------------
// StateLabel — mono 10px lowercase credential state label (bu-qo3sf)
//
// Renders the textual state of a credential as a compact mono lowercase
// label. Used in the heading + state plaque section of each credential page.
//
// butler-secrets §Evidence-Over-Value Affordance Contract §1:
//   "state label (one of {ok, expired, revoked, expiring_soon, scope_mismatch,
//   failed, never_set})"
//
// State colour mapping follows Dispatch §1b — colour appears only when state
// demands. For "ok", the label renders in --dim (neutral muted) so that a
// healthy credential page has zero red/amber pixels.
//
// Dispatch §4e: "State SHALL be expressed as one of {dot, sliver, numeral,
// colour} — never as a word."
// NOTE: StateLabel is an additional affordance beyond the dot/sliver pair.
// It is explicitly called for in the Evidence-Over-Value spec. "Status badges
// containing the words 'Connected', 'Active', 'Linked' are FORBIDDEN" — the
// forbidden words are branded affirmations, not state-accurate labels.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

import type { CredentialState } from "./Sliver"

export interface StateLabelProps extends React.HTMLAttributes<HTMLSpanElement> {
  /** Credential state to render. */
  state: CredentialState
}

/** Maps credential state to a CSS color token. */
const STATE_COLORS: Record<CredentialState, string> = {
  ok: "var(--dim,oklch(0.55_0_0))",
  expired: "var(--red)",
  revoked: "var(--red)",
  failed: "var(--red)",
  scope_mismatch: "var(--amber)",
  expiring_soon: "var(--amber)",
  never_set: "var(--dim,oklch(0.55_0_0))",
}

/** Human-readable display for each state (lowercase, no underscores). */
const STATE_LABELS: Record<CredentialState, string> = {
  ok: "ok",
  expired: "expired",
  revoked: "revoked",
  failed: "failed",
  scope_mismatch: "scope mismatch",
  expiring_soon: "expiring soon",
  never_set: "never set",
}

/**
 * Mono 10px lowercase state label.
 *
 * Renders the credential state as compact monospace text. Coloured when state
 * demands; dim/neutral for "ok".
 *
 * @example
 *   <StateLabel state="expired" />   // renders "expired" in --red
 *   <StateLabel state="ok" />        // renders "ok" in --dim
 */
export function StateLabel({ state, className, style, ...props }: StateLabelProps) {
  const color = STATE_COLORS[state]
  const label = STATE_LABELS[state]

  return (
    <span
      className={cn(
        "font-mono text-[10px] font-normal",
        "tracking-normal leading-none",
        "tabular-nums",
        className,
      )}
      style={{ color, ...style }}
      {...props}
    >
      {label}
    </span>
  )
}
